"""Temporal causal tracing over code graph and git timeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from ..core.models import TimelineEvent
from ..storage.base import StorageBackend

logger = logging.getLogger(__name__)


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _file_matches(candidate: str, changed: str) -> bool:
    return (
        candidate == changed
        or candidate.endswith(f"/{changed}")
        or changed.endswith(f"/{candidate}")
    )


def _importance_score(level: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(level, 1)


@dataclass
class CausalTraceEvent:
    """Scored timeline event for a query."""

    event: TimelineEvent
    score: float
    matched_files: list[str]


@dataclass
class CausalTraceResult:
    """Result payload for temporal causal tracing."""

    query: str
    seed_symbols: list[str] = field(default_factory=list)
    related_symbols: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    events: list[CausalTraceEvent] = field(default_factory=list)


class CausalTracer:
    """Build timeline-aware causal candidates for a query."""

    def __init__(self, backend: StorageBackend):
        self.backend = backend

    def trace(
        self,
        query: str,
        hops: int = 1,
        seed_limit: int = 5,
        timeline_limit: int = 100,
        limit: int = 10,
    ) -> CausalTraceResult:
        """Trace likely causal timeline events for a query."""
        seed_results = self.backend.search_lexical(query, k=seed_limit)
        seed_chunks = [result.chunk for result in seed_results]

        seed_symbols = [chunk.symbol for chunk in seed_chunks]
        related_symbols = set(seed_symbols)
        related_files = {_normalize_path(str(chunk.file_path)) for chunk in seed_chunks}

        frontier = list(seed_symbols)
        for _ in range(max(0, hops)):
            next_frontier: list[str] = []
            for symbol in frontier:
                try:
                    relationships = self.backend.get_code_relationships(
                        from_entity=symbol,
                        limit=seed_limit * 2,
                    )
                except Exception as exc:
                    logger.debug("Skipping graph expansion for '%s': %s", symbol, exc)
                    relationships = []

                for relationship in relationships:
                    target = relationship.to_entity
                    if target not in related_symbols:
                        related_symbols.add(target)
                        next_frontier.append(target)

                    target_results = self.backend.search_lexical(target, k=1)
                    for target_result in target_results:
                        related_files.add(_normalize_path(str(target_result.chunk.file_path)))

            frontier = next_frontier
            if not frontier:
                break

        try:
            timeline_events = self.backend.get_timeline_events(limit=timeline_limit)
        except Exception as exc:
            logger.debug("Skipping timeline scoring due to backend error: %s", exc)
            timeline_events = []

        scored_events = self._score_events(
            timeline_events=timeline_events,
            related_files=related_files,
        )

        scored_events.sort(
            key=lambda item: (
                item.score,
                item.event.created_at or datetime.min,
            ),
            reverse=True,
        )

        return CausalTraceResult(
            query=query,
            seed_symbols=seed_symbols,
            related_symbols=sorted(related_symbols),
            related_files=sorted(related_files),
            events=scored_events[:limit],
        )

    def _score_events(
        self,
        timeline_events: Iterable[TimelineEvent],
        related_files: set[str],
    ) -> list[CausalTraceEvent]:
        scored: list[CausalTraceEvent] = []

        for event in timeline_events:
            event_files = [_normalize_path(path) for path in (event.files_changed or [])]
            matched: list[str] = []
            for event_file in event_files:
                if any(_file_matches(candidate, event_file) for candidate in related_files):
                    matched.append(event_file)

            if not matched:
                continue

            unique_matched = sorted(set(matched))
            score = float((len(unique_matched) * 10) + _importance_score(event.importance))
            scored.append(
                CausalTraceEvent(
                    event=event,
                    score=score,
                    matched_files=unique_matched,
                )
            )

        return scored
