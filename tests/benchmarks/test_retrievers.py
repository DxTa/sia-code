"""Tests for benchmark retriever backend initialization."""

from pathlib import Path

from sia_code.config import Config
from tests.benchmarks.retrievers import SiaCodeRetriever


class _DummyBackend:
    def __init__(self):
        self.opened = False

    def open_index(self):
        self.opened = True


class _DummySearcher:
    def __init__(self, backend, max_hops=2):
        self.backend = backend
        self.max_hops = max_hops


def test_sia_retriever_uses_backend_factory_with_sqlite_vec(monkeypatch, tmp_path):
    """Retriever should use backend factory instead of legacy usearch-only backend."""
    index_dir = tmp_path / ".sia-code"
    index_dir.mkdir()
    Config().save(index_dir / "config.json")
    (index_dir / "index.db").touch()

    captured = {}
    backend = _DummyBackend()

    def fake_create_backend(path: Path, backend_type: str = "auto", **kwargs):
        captured["path"] = path
        captured["backend_type"] = backend_type
        captured["kwargs"] = kwargs
        return backend

    import sia_code.storage.factory as factory
    import sia_code.search.multi_hop as multi_hop

    monkeypatch.setattr(factory, "create_backend", fake_create_backend)
    monkeypatch.setattr(multi_hop, "MultiHopSearchStrategy", _DummySearcher)

    retriever = SiaCodeRetriever(index_path=index_dir)
    retriever._ensure_initialized()

    assert captured["path"] == index_dir
    assert captured["backend_type"] == "auto"
    assert backend.opened is True


def test_sia_retriever_accepts_index_db_path(monkeypatch, tmp_path):
    """Retriever should accept either .sia-code dir or .sia-code/index.db path."""
    index_dir = tmp_path / ".sia-code"
    index_dir.mkdir()
    Config().save(index_dir / "config.json")
    index_db = index_dir / "index.db"
    index_db.touch()

    captured = {}
    backend = _DummyBackend()

    def fake_create_backend(path: Path, backend_type: str = "auto", **kwargs):
        captured["path"] = path
        return backend

    import sia_code.storage.factory as factory
    import sia_code.search.multi_hop as multi_hop

    monkeypatch.setattr(factory, "create_backend", fake_create_backend)
    monkeypatch.setattr(multi_hop, "MultiHopSearchStrategy", _DummySearcher)

    retriever = SiaCodeRetriever(index_path=index_db)
    retriever._ensure_initialized()

    assert captured["path"] == index_dir
