"""Unit tests for CLI backend selection and legacy compatibility."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from sia_code.cli import create_backend, main
from sia_code.config import Config


def test_legacy_usearch_with_implicit_backend_uses_compat_mode(monkeypatch, tmp_path):
    """Default sqlite-vec config should keep legacy usearch indexes working."""
    sia_dir = tmp_path / ".sia-code"
    sia_dir.mkdir()
    (sia_dir / "vectors.usearch").write_bytes(b"legacy")

    config = Config()
    assert "backend" not in config.storage.model_fields_set

    captured: dict[str, str] = {}

    def fake_create_backend(path: Path, backend_type: str = "auto", **kwargs):
        captured["backend_type"] = backend_type
        return {"path": str(path), "backend_type": backend_type}

    monkeypatch.setattr("sia_code.storage.factory.create_backend", fake_create_backend)

    backend = create_backend(sia_dir, config)

    assert backend["backend_type"] == "auto"
    assert captured["backend_type"] == "auto"


def test_legacy_usearch_with_explicit_sqlite_backend_requires_migration(tmp_path):
    """Explicit sqlite-vec config should fail fast on legacy usearch index."""
    sia_dir = tmp_path / ".sia-code"
    sia_dir.mkdir()
    (sia_dir / "vectors.usearch").write_bytes(b"legacy")

    config = Config.model_validate({"storage": {"backend": "sqlite-vec"}})
    assert "backend" in config.storage.model_fields_set

    with pytest.raises(SystemExit):
        create_backend(sia_dir, config)


def test_legacy_usearch_can_suppress_compatibility_notices(monkeypatch, tmp_path, capsys):
    """Machine-readable commands should be able to suppress stdout notices."""
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


def test_working_set_command_requests_suppressed_notices(monkeypatch, tmp_path):
    """working-set should opt into pure stdout for machine-readable JSON."""

    class _FakeBackend:
        def open_index(self):
            return None

        def close(self):
            return None

        def generate_context(self, query=None):
            return {"project_memory": {"relevant_code": [], "approved_decisions": []}}

    captured: dict[str, object] = {}

    def fake_create_backend(
        path: Path, config: Config, valid_chunks=None, suppress_stdout_notices=False
    ):
        captured["suppress_stdout_notices"] = suppress_stdout_notices
        return _FakeBackend()

    monkeypatch.setattr("sia_code.cli.require_initialized", lambda: (tmp_path, Config()))
    monkeypatch.setattr("sia_code.cli.create_backend", fake_create_backend)

    result = CliRunner().invoke(main, ["memory", "working-set", "auth flow"])

    assert result.exit_code == 0
    assert captured["suppress_stdout_notices"] is True
