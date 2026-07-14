"""Unit tests for writable index open behavior."""

import sqlite3

from sia_code.storage import usearch_backend


def test_open_index_writable_uses_load(monkeypatch, tmp_path):
    vector_path = tmp_path / "vectors.usearch"
    db_path = tmp_path / "index.db"

    # Create minimal valid SQLite DB
    sqlite3.connect(db_path).close()
    # Ensure vector index file exists with non-zero size
    vector_path.write_bytes(b"x")

    calls = {"load": 0, "view": 0}

    class FakeIndex:
        def __init__(self, *args, **kwargs):
            pass

        def load(self, _path):
            calls["load"] += 1

        def view(self, _path):
            calls["view"] += 1

        def save(self, _path):
            pass

        def __len__(self):
            return 0

        @property
        def ndim(self):
            return 768

    class FakeMetricKind:
        Cos = "cos"
        L2sq = "l2sq"

    monkeypatch.setattr(
        usearch_backend, "_lazy_usearch", lambda: (FakeIndex, FakeMetricKind)
    )
    monkeypatch.setattr(
        usearch_backend.UsearchSqliteBackend, "_get_embedder", lambda self: None
    )

    backend = usearch_backend.UsearchSqliteBackend(path=tmp_path)
    backend.open_index(writable=True)

    assert calls["load"] == 1
    assert calls["view"] == 0
