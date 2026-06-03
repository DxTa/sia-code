"""Unit tests for temporal causal tracing."""

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from sia_code.core.models import Chunk, CodeRelationshipRecord, SearchResult, TimelineEvent
from sia_code.core.types import ChunkId, ChunkType, FilePath, Language, LineNumber
from sia_code.memory.causal_trace import CausalTracer


def _make_chunk(symbol: str, file_path: str, chunk_id: str) -> Chunk:
    return Chunk(
        id=ChunkId(chunk_id),
        symbol=symbol,
        start_line=LineNumber(1),
        end_line=LineNumber(3),
        code=f"def {symbol}():\n    return 1",
        chunk_type=ChunkType.FUNCTION,
        language=Language.PYTHON,
        file_path=FilePath(file_path),
    )


def _make_event(
    event_id: int,
    summary: str,
    files_changed: list[str],
    importance: str,
    created_at: datetime,
) -> TimelineEvent:
    return TimelineEvent(
        id=event_id,
        event_type="merge",
        from_ref="feature/x",
        to_ref="main",
        summary=summary,
        files_changed=files_changed,
        diff_stats={"files": len(files_changed)},
        importance=importance,
        created_at=created_at,
    )


def test_trace_returns_timeline_events_for_related_files():
    backend = MagicMock()
    backend.search_lexical.return_value = [
        SearchResult(chunk=_make_chunk("main", "app/main.py", "c1"), score=1.0)
    ]
    backend.get_code_relationships.return_value = []
    backend.get_timeline_events.return_value = [
        _make_event(
            event_id=1,
            summary="Refactor main flow",
            files_changed=["app/main.py"],
            importance="medium",
            created_at=datetime.now(),
        ),
        _make_event(
            event_id=2,
            summary="Change unrelated docs",
            files_changed=["docs/readme.md"],
            importance="high",
            created_at=datetime.now(),
        ),
    ]

    tracer = CausalTracer(backend)
    result = tracer.trace("main", hops=0, limit=10)

    assert len(result.events) == 1
    assert result.events[0].event.id == 1
    assert result.events[0].matched_files == ["app/main.py"]


def test_trace_ranks_high_importance_events_higher():
    backend = MagicMock()
    backend.search_lexical.return_value = [
        SearchResult(chunk=_make_chunk("main", "app/main.py", "c1"), score=1.0)
    ]
    backend.get_code_relationships.return_value = []

    now = datetime.now()
    backend.get_timeline_events.return_value = [
        _make_event(
            event_id=1,
            summary="Low importance match",
            files_changed=["app/main.py"],
            importance="low",
            created_at=now,
        ),
        _make_event(
            event_id=2,
            summary="High importance match",
            files_changed=["app/main.py"],
            importance="high",
            created_at=now - timedelta(hours=1),
        ),
    ]

    tracer = CausalTracer(backend)
    result = tracer.trace("main", hops=0, limit=10)

    assert len(result.events) == 2
    assert result.events[0].event.id == 2
    assert result.events[1].event.id == 1


def test_trace_expands_candidates_via_code_relationships():
    backend = MagicMock()

    source_chunk = _make_chunk("source_handler", "app/source.py", "source-1")
    helper_chunk = _make_chunk("helper", "app/helper.py", "helper-1")

    def search_lexical_side_effect(query: str, k: int = 10):
        if query == "source":
            return [SearchResult(chunk=source_chunk, score=1.0)]
        if query == "helper":
            return [SearchResult(chunk=helper_chunk, score=0.9)]
        return []

    backend.search_lexical.side_effect = search_lexical_side_effect
    backend.get_code_relationships.return_value = [
        CodeRelationshipRecord(
            from_entity="source_handler",
            to_entity="helper",
            relationship_type="function_call",
            from_chunk_id="source-1",
            to_chunk_id="helper-1",
        )
    ]
    backend.get_timeline_events.return_value = [
        _make_event(
            event_id=9,
            summary="Fix helper behavior",
            files_changed=["app/helper.py"],
            importance="medium",
            created_at=datetime.now(),
        )
    ]

    tracer = CausalTracer(backend)
    result = tracer.trace("source", hops=1, limit=10)

    assert len(result.events) == 1
    assert result.events[0].event.id == 9
    assert "helper" in result.related_symbols


def test_trace_returns_empty_when_no_overlap():
    backend = MagicMock()
    backend.search_lexical.return_value = [
        SearchResult(chunk=_make_chunk("main", "app/main.py", "c1"), score=1.0)
    ]
    backend.get_code_relationships.return_value = []
    backend.get_timeline_events.return_value = [
        _make_event(
            event_id=1,
            summary="Touches other file",
            files_changed=["app/other.py"],
            importance="high",
            created_at=datetime.now(),
        )
    ]

    tracer = CausalTracer(backend)
    result = tracer.trace("main", hops=0, limit=10)

    assert result.events == []


def test_trace_handles_missing_relationship_table_gracefully():
    backend = MagicMock()
    backend.search_lexical.return_value = [
        SearchResult(chunk=_make_chunk("main", "app/main.py", "c1"), score=1.0)
    ]
    backend.get_code_relationships.side_effect = sqlite3.OperationalError(
        "no such table: code_relationships"
    )
    backend.get_timeline_events.return_value = [
        _make_event(
            event_id=3,
            summary="Touches main flow",
            files_changed=["app/main.py"],
            importance="medium",
            created_at=datetime.now(),
        )
    ]

    tracer = CausalTracer(backend)
    result = tracer.trace("main", hops=1, limit=10)

    assert len(result.events) == 1
    assert result.events[0].event.id == 3


def test_trace_handles_timeline_schema_errors_gracefully():
    backend = MagicMock()
    backend.search_lexical.return_value = [
        SearchResult(chunk=_make_chunk("main", "app/main.py", "c1"), score=1.0)
    ]
    backend.get_code_relationships.return_value = []
    backend.get_timeline_events.side_effect = sqlite3.OperationalError(
        "no such column: commit_hash"
    )

    tracer = CausalTracer(backend)
    result = tracer.trace("main", hops=1, limit=10)

    assert result.events == []
