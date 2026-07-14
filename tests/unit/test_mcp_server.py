import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sia_code.mcp import build_server
from sia_code.config import Config


def run_cli(args: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = f"{repo_root}:{env.get('PYTHONPATH', '')}"
    return subprocess.run(
        [sys.executable, "-m", "sia_code.cli"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def disable_embeddings(project_dir: Path) -> None:
    config_path = project_dir / ".sia-code" / "config.json"
    config = json.loads(config_path.read_text())
    config.setdefault("embedding", {})
    config["embedding"]["enabled"] = False
    config_path.write_text(json.dumps(config, indent=2))


def decode_tool_result(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        assert len(result) == 1
        return json.loads(result[0].text)
    return result


@pytest.mark.anyio
async def test_build_server_registers_expected_tool_names():
    server = build_server()
    tool_names = {tool.name for tool in await server.list_tools()}

    assert tool_names == {
        "compact",
        "config_get",
        "config_path",
        "config_set",
        "config_show",
        "embed_start",
        "embed_status",
        "embed_stop",
        "engineering_bootstrap",
        "git_context",
        "health_check",
        "index",
        "init",
        "memory_add_decision",
        "memory_approve",
        "memory_changelog",
        "memory_export",
        "memory_import",
        "memory_list",
        "memory_reject",
        "memory_search",
        "memory_sync_git",
        "memory_timeline",
        "memory_trace",
        "memory_working_set",
        "research",
        "search",
        "status",
    }


@pytest.mark.anyio
async def test_git_context_uses_explicit_index_dir(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    index_dir = tmp_path / "custom-index"
    index_dir.mkdir()
    Config().save(index_dir / "config.json")

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "git_context",
            {
                "workspace_root": str(workspace_root),
                "index_dir": str(index_dir),
                "file_paths": ["missing.py"],
                "include_blast_radius": False,
                "include_narrative": False,
            },
        )
    )

    assert result["ok"] is True
    assert result["resolved_index_dir"] == str(index_dir.resolve())


@pytest.mark.anyio
async def test_status_tool_returns_structured_result_for_initialized_workspace(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text("def hello():\n    return 'hi'\n")

    run_cli(["init"], cwd=workspace_root)
    disable_embeddings(workspace_root)
    run_cli(["index", "."], cwd=workspace_root)

    server = build_server()
    result = decode_tool_result(
        await server.call_tool("status", {"workspace_root": str(workspace_root)})
    )

    assert result["ok"] is True
    assert result["resolved_workspace_root"] == str(workspace_root.resolve())
    assert result["resolved_index_dir"].endswith(".sia-code")
    assert result["result"]["stats"]["total_files"] >= 1
    assert result["result"]["initialized"] is True


@pytest.mark.anyio
async def test_status_tool_returns_actionable_payload_for_uninitialized_workspace(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()

    server = build_server()
    result = decode_tool_result(
        await server.call_tool("status", {"workspace_root": str(workspace_root)})
    )

    assert result["ok"] is True
    assert result["result"]["initialized"] is False
    assert result["result"]["usable"] is False
    assert "recommended_action" in result["result"]


@pytest.mark.anyio
async def test_health_check_returns_actionable_payload_for_uninitialized_workspace(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "health_check",
            {"workspace_root": str(workspace_root), "probe_query": "hello"},
        )
    )

    assert result["ok"] is True
    assert result["result"]["initialized"] is False
    assert result["result"]["degraded"] is True


@pytest.mark.anyio
async def test_health_check_reports_research_ready_for_initialized_workspace(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text("def hello():\n    return 'hi'\n")

    run_cli(["init"], cwd=workspace_root)
    disable_embeddings(workspace_root)
    run_cli(["index", "."], cwd=workspace_root)

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "health_check",
            {"workspace_root": str(workspace_root), "probe_query": "hello"},
        )
    )

    assert result["ok"] is True
    assert result["result"]["initialized"] is True
    assert result["result"]["research_ready"] is True
    assert result["result"]["probe"]["query"] == "hello"


@pytest.mark.anyio
async def test_health_check_detects_missing_relationship_schema(monkeypatch, tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    sia_dir = workspace_root / ".sia-code"
    sia_dir.mkdir()
    (sia_dir / "config.json").write_text(json.dumps(Config().model_dump(), indent=2))

    class _FakeBackend:
        def open_index(self, writable: bool = False):
            return None

        def close(self):
            return None

        def get_stats(self):
            return type(
                "Stats",
                (),
                {
                    "total_files": 0,
                    "total_chunks": 0,
                    "total_size_bytes": 0,
                    "languages": {},
                    "last_indexed": None,
                },
            )()

        def search_lexical(self, query, k=5, include_deps=True, tier_boost=None):
            return []

        def get_code_relationships(
            self, from_entity=None, to_entity=None, relationship_type=None, limit=100
        ):
            raise Exception("no such table: code_relationships")

    monkeypatch.setattr("sia_code.mcp.create_backend", lambda *args, **kwargs: _FakeBackend())

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "health_check",
            {"workspace_root": str(workspace_root), "probe_query": "hello"},
        )
    )

    assert result["ok"] is True
    assert result["result"]["research_ready"] is False
    assert result["result"]["degraded"] is True
    assert any("code_relationships" in issue for issue in result["result"]["issues"])


@pytest.mark.anyio
async def test_engineering_bootstrap_returns_context_without_forcing_research(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text(
        "def hello_world():\n    return 'hi'\n\n\ndef add(a, b):\n    return a + b\n"
    )

    run_cli(["init"], cwd=workspace_root)
    disable_embeddings(workspace_root)
    run_cli(["index", "."], cwd=workspace_root)

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "engineering_bootstrap",
            {
                "workspace_root": str(workspace_root),
                "task": "Plan a fix for hello_world formatting bug",
                "research_mode": "never",
            },
        )
    )

    payload = result["result"]
    assert result["ok"] is True
    assert payload["task_classification"] in {"planning", "debugging"}
    assert payload["health"]["initialized"] is True
    assert payload["research"]["executed"] is False
    assert payload["research"]["mode"] == "never"
    assert "recommended_next_step" in payload
    assert payload["search_hits"] is not None


@pytest.mark.anyio
async def test_engineering_bootstrap_returns_guidance_for_uninitialized_workspace(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "engineering_bootstrap",
            {
                "workspace_root": str(workspace_root),
                "task": "Understand authentication flow",
            },
        )
    )

    payload = result["result"]
    assert result["ok"] is True
    assert payload["health"]["initialized"] is False
    assert payload["research_ready"] is False
    assert payload["research"]["executed"] is False
    assert "initialize" in payload["fallback_guidance"].lower()


@pytest.mark.anyio
async def test_search_warns_when_empty_results_use_stale_index(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    source = workspace_root / "main.py"
    source.write_text("def hello_world():\n    return 'hi'\n")

    run_cli(["init"], cwd=workspace_root)
    disable_embeddings(workspace_root)
    run_cli(["index", "."], cwd=workspace_root)
    source.write_text("def hello_world():\n    return 'hi'\n\ndef new_symbol_after_index():\n    return 1\n")

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "search",
            {
                "workspace_root": str(workspace_root),
                "query": "new_symbol_after_index",
                "mode": "lexical",
            },
        )
    )

    assert result["ok"] is True
    assert result["result"]["match_count"] == 0
    assert result["result"]["freshness"]["stale"] is True
    assert "stale" in result["result"]["warning"]


@pytest.mark.anyio
async def test_memory_working_set_tool_returns_json_payload(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text("def hello_world():\n    return 'hi'\n")

    run_cli(["init"], cwd=workspace_root)
    disable_embeddings(workspace_root)
    run_cli(["index", "."], cwd=workspace_root)

    server = build_server()
    result = decode_tool_result(
        await server.call_tool(
            "memory_working_set",
            {
                "workspace_root": str(workspace_root),
                "query": "hello_world",
                "agent": "planner",
                "session_id": "ses-123",
            },
        )
    )

    working_memory = result["result"]["working_memory"]
    assert result["ok"] is True
    assert working_memory["agent"] == "planner"
    assert working_memory["session_id"] == "ses-123"
    assert working_memory["query"] == "hello_world"


@pytest.mark.anyio
async def test_embed_status_tool_reports_machine_scoped_status():
    server = build_server()

    result = decode_tool_result(await server.call_tool("embed_status", {}))

    assert result["ok"] is True
    assert result["scope"] == "machine"
    assert "running" in result["result"]


@pytest.mark.anyio
async def test_status_tool_suppresses_backend_stdout_notices(monkeypatch, tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    sia_dir = workspace_root / ".sia-code"
    sia_dir.mkdir()
    (sia_dir / "config.json").write_text(json.dumps(Config().model_dump(), indent=2))

    captured = {}

    class _FakeBackend:
        def open_index(self, writable: bool = False):
            return None

        def close(self):
            return None

        def get_stats(self):
            return type(
                "Stats",
                (),
                {
                    "total_files": 0,
                    "total_chunks": 0,
                    "total_size_bytes": 0,
                    "languages": {},
                    "last_indexed": None,
                },
            )()

    def fake_create_backend(index_path, config, valid_chunks=None, suppress_stdout_notices=False):
        captured["suppress"] = suppress_stdout_notices
        return _FakeBackend()

    monkeypatch.setattr("sia_code.mcp.create_backend", fake_create_backend)

    server = build_server()
    result = decode_tool_result(
        await server.call_tool("status", {"workspace_root": str(workspace_root)})
    )

    assert result["ok"] is True
    assert captured["suppress"] is True
