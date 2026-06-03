"""Tests for UsearchSqliteBackend."""

import tempfile
from datetime import datetime
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from sia_code.core.models import Chunk, CodeRelationshipRecord
from sia_code.core.types import ChunkType, Language
from sia_code.storage.usearch_backend import UsearchSqliteBackend


@pytest.fixture
def temp_index_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / ".sia-code"


@pytest.fixture
def backend(temp_index_dir, monkeypatch):
    """Create a test backend instance."""
    backend = UsearchSqliteBackend(
        path=temp_index_dir,
        embedding_enabled=False,
    )

    class DummyEmbedder:
        def __init__(self, ndim: int):
            self.ndim = ndim

        def encode(self, texts, **kwargs):
            def _vector(text: str) -> np.ndarray:
                vec = np.zeros(self.ndim, dtype=np.float32)
                text_lower = text.lower()
                if "sum" in text_lower or "add" in text_lower:
                    vec[0] = 1.0
                elif "product" in text_lower or "multiply" in text_lower:
                    vec[1] = 1.0
                else:
                    vec[2] = 1.0
                return vec

            if isinstance(texts, list):
                return np.vstack([_vector(text) for text in texts])
            return _vector(texts)

    dummy = DummyEmbedder(backend.ndim)
    monkeypatch.setattr(backend, "_get_embedder", lambda: dummy)
    backend.create_index()
    yield backend
    backend.close()


def test_create_index(temp_index_dir):
    """Test index creation."""
    backend = UsearchSqliteBackend(path=temp_index_dir)
    backend.create_index()

    assert (temp_index_dir / "index.db").exists()
    assert backend.conn is not None
    assert backend.vector_index is not None

    backend.close()


def test_store_and_retrieve_chunks(backend):
    """Test storing and retrieving code chunks."""
    # Create test chunks
    chunks = [
        Chunk(
            symbol="test_function",
            start_line=1,
            end_line=5,
            code="def test_function():\n    pass",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=Path("test.py"),
        ),
        Chunk(
            symbol="another_function",
            start_line=7,
            end_line=10,
            code="def another_function():\n    return 42",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=Path("test.py"),
        ),
    ]

    # Store chunks
    chunk_ids = backend.store_chunks_batch(chunks)
    assert len(chunk_ids) == 2

    # Retrieve chunks
    retrieved = backend.get_chunk(chunk_ids[0])
    assert retrieved is not None
    assert retrieved.symbol == "test_function"
    assert retrieved.code == chunks[0].code


def test_semantic_search(backend):
    """Test semantic vector search."""
    if not backend.embedding_enabled:
        pytest.skip("Semantic search requires embeddings (disabled in tests).")
    # Store some chunks
    chunks = [
        Chunk(
            symbol="calculate_sum",
            start_line=1,
            end_line=3,
            code="def calculate_sum(a, b):\n    return a + b",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=Path("math.py"),
        ),
        Chunk(
            symbol="calculate_product",
            start_line=5,
            end_line=7,
            code="def calculate_product(a, b):\n    return a * b",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=Path("math.py"),
        ),
    ]

    backend.store_chunks_batch(chunks)

    # Search for addition-related code
    results = backend.search_semantic("add two numbers", k=2)

    assert len(results) > 0
    # The sum function should be more relevant
    assert "sum" in results[0].chunk.symbol.lower()


def test_decision_workflow(backend):
    """Test decision management with FIFO."""
    # Add a decision
    decision_id = backend.add_decision(
        session_id="test-session-1",
        title="Use PostgreSQL for database",
        description="We need a relational database with ACID guarantees",
        reasoning="PostgreSQL offers better JSON support than MySQL",
        alternatives=[{"option": "MySQL", "reason": "Simpler but limited JSON support"}],
    )

    assert decision_id > 0

    # Retrieve the decision
    decision = backend.get_decision(decision_id)
    assert decision is not None
    assert decision.title == "Use PostgreSQL for database"
    assert decision.status == "pending"

    # Approve the decision
    memory_id = backend.approve_decision(decision_id, category="architecture")
    assert memory_id > 0

    # Check that decision is now approved
    decision = backend.get_decision(decision_id)
    assert decision.status == "approved"
    assert decision.category == "architecture"


def test_decision_stores_conceptual_links(backend):
    links = [
        {"type": "file", "ref": "sia_code/cli.py", "rationale": "CLI entry point"},
        {"type": "symbol", "ref": "memory_trace"},
    ]

    decision_id = backend.add_decision(
        session_id="test-session-links",
        title="Conceptual links for comprehension",
        description="Track rationale and intent against code artifacts",
        conceptual_links=links,
    )

    decision = backend.get_decision(decision_id)
    assert decision is not None
    assert decision.conceptual_links == links

    memory_results = backend.search_memory("comprehension", k=3)
    assert memory_results
    metadata = memory_results[0].chunk.metadata
    assert metadata["conceptual_link_count"] == 2
    assert metadata["conceptual_links"] == links


def test_decision_fifo(backend):
    """Test that FIFO works when >100 pending decisions."""
    # Add 101 pending decisions
    for i in range(101):
        backend.add_decision(
            session_id=f"session-{i}",
            title=f"Decision {i}",
            description=f"Description for decision {i}",
        )

    # Check that only 100 pending decisions exist
    pending = backend.list_pending_decisions(limit=200)
    assert len(pending) == 100

    # The first decision (id=1) should have been deleted
    first_decision = backend.get_decision(1)
    assert first_decision is None or first_decision.status != "pending"


def test_timeline_events(backend):
    """Test timeline event management."""
    # Add a timeline event
    event_id = backend.add_timeline_event(
        event_type="tag",
        from_ref="v1.0.0",
        to_ref="v1.1.0",
        summary="Added new features and fixed bugs",
        files_changed=["src/main.py", "src/utils.py"],
        diff_stats={"insertions": 150, "deletions": 20, "files": 2},
        importance="high",
    )

    assert event_id > 0

    # Retrieve timeline events
    events = backend.get_timeline_events(from_ref="v1.0.0")
    assert len(events) > 0
    assert events[0].from_ref == "v1.0.0"
    assert events[0].to_ref == "v1.1.0"


def test_code_relationship_persistence(backend):
    """Test storing and querying persisted code relationships."""
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

    # Duplicate inserts should be ignored.
    duplicate_inserted = backend.add_code_relationships(relationships)
    assert duplicate_inserted == 0

    outgoing = backend.get_code_relationships(from_entity="main", limit=10)
    assert len(outgoing) == 2
    assert {rel.to_entity for rel in outgoing} == {"load_config", "process_data"}

    call_edges = backend.get_code_relationships(relationship_type="function_call", limit=10)
    assert len(call_edges) == 2


def test_export_import_memory(backend, temp_index_dir):
    """Test memory export and import."""
    # Add some test data
    decision_id = backend.add_decision(
        session_id="export-test",
        title="Test decision for export",
        description="This decision will be exported and re-imported",
    )
    backend.approve_decision(decision_id, category="test")

    commit_time = datetime(2024, 1, 1, 12, 0, 0)

    backend.add_timeline_event(
        event_type="tag",
        from_ref="v1.0.0",
        to_ref="v2.0.0",
        summary="Major release",
        commit_hash="abc123",
        commit_time=commit_time,
    )

    backend.add_changelog(
        tag="v2.0.0",
        version="2.0.0",
        summary="Major version with breaking changes",
        breaking_changes=["Changed API signature"],
        commit_hash="def456",
        commit_time=commit_time,
    )

    # Export memory
    export_path = backend.export_memory(include_pending=False)
    assert Path(export_path).exists()

    # Create a new backend and import
    backend2 = UsearchSqliteBackend(path=temp_index_dir / "backend2", embedding_enabled=False)
    backend2.create_index()

    result = backend2.import_memory(export_path)

    # Should have imported the data
    assert result.added > 0

    # Verify data was imported
    events = backend2.get_timeline_events()
    assert len(events) > 0

    imported_event = next(
        (e for e in events if e.from_ref == "v1.0.0" and e.to_ref == "v2.0.0"), None
    )
    assert imported_event is not None
    assert imported_event.commit_hash == "abc123"
    assert imported_event.commit_time == commit_time

    changelogs = backend2.get_changelogs()
    assert len(changelogs) > 0

    imported_changelog = next((c for c in changelogs if c.tag == "v2.0.0"), None)
    assert imported_changelog is not None
    assert imported_changelog.commit_hash == "def456"
    assert imported_changelog.commit_time == commit_time

    backend2.close()


def test_open_index_applies_migrations_for_writes(temp_index_dir):
    """Opening a legacy index in writable mode should apply schema migrations."""
    import sqlite3

    legacy_dir = temp_index_dir / "legacy"
    legacy_dir.mkdir(parents=True)

    # Minimal legacy schema without commit_hash/commit_time columns
    db_path = legacy_dir / "index.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            from_ref TEXT,
            to_ref TEXT,
            summary TEXT,
            files_changed JSON,
            diff_stats JSON,
            importance TEXT DEFAULT 'medium',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS changelogs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT UNIQUE,
            version TEXT,
            date TIMESTAMP,
            summary TEXT,
            breaking_changes JSON,
            features JSON,
            fixes JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

    # usearch backend requires vector file to exist
    (legacy_dir / "vectors.usearch").write_bytes(b"")

    backend = UsearchSqliteBackend(path=legacy_dir, embedding_enabled=False)
    backend.open_index(writable=True)

    commit_time = datetime(2024, 1, 1, 12, 0, 0)
    backend.add_changelog(
        tag="v0.0.1",
        version="0.0.1",
        summary="legacy import",
        commit_hash="abc123",
        commit_time=commit_time,
    )

    changelogs = backend.get_changelogs(limit=10)
    imported = next((c for c in changelogs if c.tag == "v0.0.1"), None)
    assert imported is not None
    assert imported.commit_hash == "abc123"
    assert imported.commit_time == commit_time

    backend.close()


def test_generate_context(backend):
    """Test LLM context generation."""
    # Add test data
    chunks = [
        Chunk(
            symbol="example_function",
            start_line=1,
            end_line=3,
            code="def example_function():\n    return 'hello'",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=Path("example.py"),
        )
    ]
    backend.store_chunks_batch(chunks)

    backend.add_decision(
        session_id="context-test",
        title="Test decision",
        description="Test description",
    )

    # Generate context
    context = backend.generate_context(query="hello world")

    assert "project_memory" in context
    assert "codebase_summary" in context["project_memory"]
    assert "recent_decisions" in context["project_memory"]
    assert context["project_memory"]["codebase_summary"]["total_chunks"] > 0


def test_mem_put_uses_uri_when_metadata_missing(temp_index_dir):
    backend = UsearchSqliteBackend(path=temp_index_dir, embedding_enabled=False)
    captured = []
    backend.store_chunks_batch = lambda chunks: captured.extend(chunks) or []

    backend.mem.put(
        title="from_uri",
        label=ChunkType.FUNCTION.value,
        metadata={},
        text="def from_uri(): pass",
        uri="pci:///tmp/usearch_uri.py#3-5",
    )

    assert captured
    assert str(captured[0].file_path) == "/tmp/usearch_uri.py"
    assert captured[0].start_line == 3
    assert captured[0].end_line == 5


def test_store_chunks_batch_keeps_stable_id_on_upsert(temp_index_dir, monkeypatch):
    backend = UsearchSqliteBackend(path=temp_index_dir, embedding_enabled=False)
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
        start_line=1,
        end_line=2,
        code="def stable():\n    return 1",
        chunk_type=ChunkType.FUNCTION,
        language=Language.PYTHON,
        file_path=Path("stable.py"),
    )

    first_id = backend.store_chunks_batch([chunk])[0]
    second_id = backend.store_chunks_batch([chunk])[0]

    assert first_id == second_id
    backend.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
