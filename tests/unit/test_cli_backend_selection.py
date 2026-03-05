"""Unit tests for CLI backend selection and legacy compatibility."""

from pathlib import Path

import pytest

from sia_code.cli import create_backend
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
