from pathlib import Path

import pytest

from sia_code.config import Config
from sia_code.runtime_context import (
    WorkspaceContext,
    build_working_memory_payload,
    create_backend,
    resolve_index_dir,
    resolve_workspace_context,
)


class _RunResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def _fake_run_factory(mapping: dict[tuple[str, ...], str]):
    def fake_run(args, **kwargs):
        key = tuple(args)
        if key not in mapping:
            raise AssertionError(f"Unexpected subprocess args: {args}")
        return _RunResult(mapping[key])

    return fake_run


def test_resolve_index_dir_prefers_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-index"
    monkeypatch.setenv("SIA_CODE_INDEX_DIR", str(override))
    assert resolve_index_dir(tmp_path) == override


def test_resolve_workspace_context_requires_explicit_root_or_index_dir():
    with pytest.raises(ValueError):
        resolve_workspace_context()


def test_resolve_workspace_context_uses_explicit_index_dir(tmp_path):
    index_dir = tmp_path / "index-dir"

    context = resolve_workspace_context(index_dir=index_dir)

    assert context == WorkspaceContext(workspace_root=index_dir.parent, index_dir=index_dir)


def test_resolve_workspace_context_resolves_relative_index_dir_from_workspace_root(tmp_path):
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()

    context = resolve_workspace_context(workspace_root=workspace_root, index_dir=".sia-code")

    assert context.workspace_root == workspace_root.resolve()
    assert context.index_dir == (workspace_root / ".sia-code").resolve()


def test_default_scope_uses_worktree_local_index_when_not_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("SIA_CODE_INDEX_DIR", raising=False)
    monkeypatch.delenv("SIA_CODE_INDEX_SCOPE", raising=False)

    monkeypatch.setattr(
        "sia_code.runtime_context.subprocess.run",
        _fake_run_factory(
            {
                ("git", "rev-parse", "--git-dir"): ".git\n",
                ("git", "rev-parse", "--git-common-dir"): ".git\n",
            }
        ),
    )

    context = resolve_workspace_context(workspace_root=tmp_path)

    assert context.workspace_root == tmp_path
    assert context.index_dir == tmp_path / ".sia-code"


def test_create_backend_can_suppress_compatibility_notices(monkeypatch, tmp_path, capsys):
    sia_dir = tmp_path / ".sia-code"
    sia_dir.mkdir()
    (sia_dir / "vectors.usearch").write_bytes(b"legacy")

    config = Config()

    def fake_create_backend(path: Path, backend_type: str = "auto", **kwargs):
        return {"path": str(path), "backend_type": backend_type}

    monkeypatch.setattr("sia_code.storage.factory.create_backend", fake_create_backend)

    create_backend(sia_dir, config, suppress_stdout_notices=True)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_build_working_memory_payload_uses_explicit_base_dir(tmp_path, monkeypatch):
    class _FakeBackend:
        def generate_context(self, query=None):
            return {"project_memory": {"relevant_code": [], "approved_decisions": []}}

    monkeypatch.setattr(
        "sia_code.runtime_context.get_git_commit_context",
        lambda base_dir: ("abc123", None),
    )
    monkeypatch.setattr(
        "sia_code.runtime_context.get_git_branch_context",
        lambda base_dir: "feature/test",
    )

    payload = build_working_memory_payload(
        backend=_FakeBackend(),
        query="auth flow",
        agent="planner",
        session_id="ses-123",
        base_dir=tmp_path,
    )

    assert payload["working_memory"]["agent"] == "planner"
    assert payload["working_memory"]["session_id"] == "ses-123"
    assert payload["working_memory"]["git"]["branch"] == "feature/test"
