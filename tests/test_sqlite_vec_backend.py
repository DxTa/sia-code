"""Tests for sqlite-vec backend (FTS5 + sqlite-vec)."""

from datetime import datetime

import numpy as np
import pytest
import sqlite3

from sia_code.core.models import Chunk, CodeRelationshipRecord
from sia_code.core.types import ChunkType, FilePath, Language, LineNumber
from sia_code.storage.sqlite_vec_backend import SqliteVecBackend


@pytest.fixture
def backend(tmp_path):
    """Create a temporary backend for testing."""
    test_path = tmp_path / "test_index.sia-code"
    backend = SqliteVecBackend(test_path, embedding_enabled=False, ndim=3)
    backend.create_index()
    yield backend
    backend.close()


def _make_chunks():
    return [
        Chunk(
            symbol="alpha_func",
            start_line=LineNumber(1),
            end_line=LineNumber(3),
            code="def alpha():\n    return 1",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("alpha.py"),
        ),
        Chunk(
            symbol="beta_func",
            start_line=LineNumber(5),
            end_line=LineNumber(7),
            code="def beta():\n    return 2",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("beta.py"),
        ),
    ]


def test_create_index(backend):
    assert backend.conn is not None


def test_store_and_search_lexical(backend):
    chunk_ids = backend.store_chunks_batch(_make_chunks())
    assert len(chunk_ids) == 2

    results = backend.search_lexical("alpha", k=1)
    assert results
    assert results[0].chunk.symbol == "alpha_func"


def test_semantic_search_fallback(tmp_path, monkeypatch):
    """Validate fallback vector search works without sqlite-vec."""

    class DummyEmbedder:
        def encode(self, texts, **kwargs):
            def _vec(text):
                return (
                    np.array([1.0, 0.0, 0.0], dtype=np.float32)
                    if "alpha" in text
                    else np.array([0.0, 1.0, 0.0], dtype=np.float32)
                )

            if isinstance(texts, list):
                return np.vstack([_vec(text) for text in texts])
            return _vec(texts)

    backend = SqliteVecBackend(tmp_path / "vec_index.sia-code", embedding_enabled=True, ndim=3)
    monkeypatch.setattr(backend, "_load_vec_extension", lambda *_: False)
    backend.create_index()
    backend._get_embedder = lambda: DummyEmbedder()
    backend._get_embed_batch_size = lambda: 1

    backend.store_chunks_batch(_make_chunks())
    results = backend.search_semantic("alpha", k=1)

    assert results
    assert results[0].chunk.symbol == "alpha_func"
    backend.close()


def test_mem_put_uses_uri_when_metadata_missing(tmp_path):
    backend = SqliteVecBackend(tmp_path / "uri_parse.sia-code", embedding_enabled=False, ndim=3)
    captured = []
    backend.store_chunks_batch = lambda chunks: captured.extend(chunks) or []

    backend.mem.put(
        title="from_uri",
        label=ChunkType.FUNCTION.value,
        metadata={},
        text="def from_uri(): pass",
        uri="pci:///tmp/example.py#10-20",
    )

    assert captured
    assert str(captured[0].file_path) == "/tmp/example.py"
    assert captured[0].start_line == 10
    assert captured[0].end_line == 20


def test_store_chunks_batch_keeps_stable_id_on_upsert(tmp_path, monkeypatch):
    backend = SqliteVecBackend(tmp_path / "upsert.sia-code", embedding_enabled=False, ndim=3)

    monkeypatch.setattr("sia_code.storage.sqlite_runtime.get_sqlite_module", lambda: sqlite3)

    def create_tables_without_fts(self):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uri TEXT UNIQUE,
                symbol TEXT,
                chunk_type TEXT,
                file_path TEXT,
                start_line INTEGER,
                end_line INTEGER,
                language TEXT,
                code TEXT,
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    monkeypatch.setattr(backend, "_create_tables", create_tables_without_fts.__get__(backend))
    backend.create_index()

    chunk = Chunk(
        symbol="stable",
        start_line=LineNumber(1),
        end_line=LineNumber(2),
        code="def stable():\n    return 1",
        chunk_type=ChunkType.FUNCTION,
        language=Language.PYTHON,
        file_path=FilePath("stable.py"),
    )

    first_id = backend.store_chunks_batch([chunk])[0]
    second_id = backend.store_chunks_batch([chunk])[0]

    assert first_id == second_id
    backend.close()


def test_add_changelog_stores_commit_context(backend):
    commit_time = datetime(2024, 1, 1, 12, 0, 0)
    backend.add_changelog(
        tag="v1.0.0",
        version="1.0.0",
        summary="Release 1.0",
        breaking_changes=[],
        features=[],
        fixes=[],
        commit_hash="abc123",
        commit_time=commit_time,
    )

    changelogs = backend.get_changelogs(limit=10)
    assert changelogs
    assert changelogs[0].commit_hash == "abc123"
    assert changelogs[0].commit_time == commit_time


def test_add_timeline_event_stores_commit_context(backend):
    commit_time = datetime(2024, 1, 2, 12, 0, 0)
    backend.add_timeline_event(
        event_type="merge",
        from_ref="feature",
        to_ref="main",
        summary="Merge feature",
        files_changed=[],
        diff_stats={},
        importance="medium",
        commit_hash="def456",
        commit_time=commit_time,
    )

    events = backend.get_timeline_events(limit=10)
    assert events
    assert events[0].commit_hash == "def456"
    assert events[0].commit_time == commit_time


def test_add_decision_stores_commit_context(backend):
    commit_time = datetime(2024, 1, 3, 12, 0, 0)
    decision_id = backend.add_decision(
        session_id="sess-1",
        title="Decision",
        description="Do the thing",
        reasoning="Because",
        alternatives=[{"title": "Alt"}],
        commit_hash="aaa111",
        commit_time=commit_time,
    )

    decision = backend.get_decision(decision_id)
    assert decision is not None
    assert decision.commit_hash == "aaa111"
    assert decision.commit_time == commit_time


def test_add_decision_stores_conceptual_links(backend):
    links = [
        {"type": "file", "ref": "sia_code/cli.py", "rationale": "CLI entry point"},
        {"type": "symbol", "ref": "memory_trace"},
    ]

    decision_id = backend.add_decision(
        session_id="sess-links",
        title="Trace conceptual reasoning",
        description="Link intent to code artifacts",
        reasoning="Need better comprehension",
        conceptual_links=links,
    )

    decision = backend.get_decision(decision_id)
    assert decision is not None
    assert decision.conceptual_links == links

    memory_results = backend.search_memory("conceptual", k=3)
    assert memory_results
    metadata = memory_results[0].chunk.metadata
    assert metadata["conceptual_link_count"] == 2
    assert metadata["conceptual_links"] == links


def test_code_relationship_persistence(backend):
    """Store and query persisted code relationships."""
    relationships = [
        CodeRelationshipRecord(
            from_entity="main",
            to_entity="load_config",
            relationship_type="function_call",
            from_chunk_id="1",
            to_chunk_id="2",
        ),
        CodeRelationshipRecord(
            from_entity="main",
            to_entity="process_data",
            relationship_type="function_call",
            from_chunk_id="1",
            to_chunk_id="3",
        ),
    ]

    inserted = backend.add_code_relationships(relationships)
    assert inserted == 2

    duplicate_inserted = backend.add_code_relationships(relationships)
    assert duplicate_inserted == 0

    outgoing = backend.get_code_relationships(from_entity="main", limit=10)
    assert len(outgoing) == 2
    assert {rel.to_entity for rel in outgoing} == {"load_config", "process_data"}

    call_edges = backend.get_code_relationships(relationship_type="function_call", limit=10)
    assert len(call_edges) == 2


def test_export_import_memory_preserves_commit_context(tmp_path):
    backend1 = SqliteVecBackend(tmp_path / "backend1.sia-code", embedding_enabled=False, ndim=3)
    backend1.create_index()

    commit_time = datetime(2024, 1, 1, 12, 0, 0)

    decision_id = backend1.add_decision(
        session_id="export-test",
        title="Decision export",
        description="Export/import should preserve commit context",
        commit_hash="d111",
        commit_time=commit_time,
    )
    backend1.approve_decision(decision_id, category="test")

    backend1.add_timeline_event(
        event_type="merge",
        from_ref="feature",
        to_ref="main",
        summary="Merged feature",
        commit_hash="t111",
        commit_time=commit_time,
    )

    backend1.add_changelog(
        tag="v1.0.0",
        version="1.0.0",
        summary="Release",
        commit_hash="c111",
        commit_time=commit_time,
    )

    export_path = backend1.export_memory(
        output_path=tmp_path / "memory.json", include_pending=False
    )
    backend1.close()

    backend2 = SqliteVecBackend(tmp_path / "backend2.sia-code", embedding_enabled=False, ndim=3)
    backend2.create_index()

    result = backend2.import_memory(export_path)
    assert result.added > 0

    imported_event = next(
        (
            e
            for e in backend2.get_timeline_events(limit=50)
            if e.from_ref == "feature" and e.to_ref == "main"
        ),
        None,
    )
    assert imported_event is not None
    assert imported_event.commit_hash == "t111"
    assert imported_event.commit_time == commit_time

    imported_changelog = next(
        (c for c in backend2.get_changelogs(limit=50) if c.tag == "v1.0.0"),
        None,
    )
    assert imported_changelog is not None
    assert imported_changelog.commit_hash == "c111"
    assert imported_changelog.commit_time == commit_time

    cursor = backend2.conn.cursor()
    cursor.execute("SELECT id FROM decisions WHERE title = ?", ("Decision export",))
    row = cursor.fetchone()
    assert row is not None
    imported_decision = backend2.get_decision(row["id"])
    assert imported_decision is not None
    assert imported_decision.commit_hash == "d111"
    assert imported_decision.commit_time == commit_time

    backend2.close()
