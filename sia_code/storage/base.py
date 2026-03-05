"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.models import (
    ChangelogEntry,
    Chunk,
    CodeRelationshipRecord,
    Decision,
    ImportResult,
    IndexStats,
    SearchResult,
    TimelineEvent,
)


class StorageBackend(ABC):
    """Abstract base class for code + memory storage backends.

    Supports:
    - Code chunk storage and semantic search
    - Project memory (decisions, timeline, changelogs)
    - LLM context generation
    - Import/export for collaboration
    """

    def __init__(self, path: Path, **kwargs):
        """Initialize storage backend.

        Args:
            path: Path to storage directory (e.g., .sia-code/)
            **kwargs: Backend-specific configuration
        """
        self.path = path
        self.config = kwargs

    # ===================================================================
    # Index Lifecycle
    # ===================================================================

    @abstractmethod
    def create_index(self) -> None:
        """Create a new index."""
        ...

    @abstractmethod
    def open_index(self, writable: bool = False) -> None:
        """Open an existing index.

        Args:
            writable: Open in read-write mode when True.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the index and cleanup resources."""
        ...

    # ===================================================================
    # Code Operations
    # ===================================================================

    @abstractmethod
    def store_chunks_batch(self, chunks: list[Chunk]) -> list[str]:
        """Store multiple code chunks.

        Args:
            chunks: List of code chunks to store

        Returns:
            List of chunk IDs (in same order as input)
        """
        ...

    @abstractmethod
    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Retrieve a chunk by ID.

        Args:
            chunk_id: The chunk identifier

        Returns:
            Chunk if found, None otherwise
        """
        ...

    @abstractmethod
    def search_semantic(self, query: str, k: int = 10, filter_fn: Any = None) -> list[SearchResult]:
        """Semantic vector search.

        Args:
            query: Query text (will be embedded)
            k: Number of results to return
            filter_fn: Optional filter function

        Returns:
            List of search results sorted by relevance
        """
        ...

    @abstractmethod
    def search_lexical(self, query: str, k: int = 10) -> list[SearchResult]:
        """Lexical full-text search.

        Args:
            query: Query text
            k: Number of results to return

        Returns:
            List of search results sorted by relevance
        """
        ...

    @abstractmethod
    def search_hybrid(
        self, query: str, k: int = 10, vector_weight: float = 0.7
    ) -> list[SearchResult]:
        """Hybrid search combining semantic and lexical.

        Args:
            query: Query text
            k: Number of results to return
            vector_weight: Weight for vector search (0-1), lexical gets (1 - vector_weight)

        Returns:
            List of search results sorted by combined relevance
        """
        ...

    @abstractmethod
    def get_stats(self) -> IndexStats:
        """Get index statistics.

        Returns:
            Index statistics including file count, chunk count, etc.
        """
        ...

    # ===================================================================
    # Code Relationship Graph
    # ===================================================================

    @abstractmethod
    def add_code_relationships(self, relationships: list[CodeRelationshipRecord]) -> int:
        """Persist code relationships discovered during research.

        Args:
            relationships: Relationship records to persist

        Returns:
            Number of newly inserted relationships
        """
        ...

    @abstractmethod
    def get_code_relationships(
        self,
        from_entity: str | None = None,
        to_entity: str | None = None,
        relationship_type: str | None = None,
        limit: int = 100,
    ) -> list[CodeRelationshipRecord]:
        """Query persisted code relationships.

        Args:
            from_entity: Optional source entity filter
            to_entity: Optional target entity filter
            relationship_type: Optional relationship type filter
            limit: Maximum results to return

        Returns:
            Relationship records
        """
        ...

    # ===================================================================
    # Decision Management
    # ===================================================================

    @abstractmethod
    def add_decision(
        self,
        session_id: str,
        title: str,
        description: str,
        reasoning: str | None = None,
        alternatives: list[dict[str, Any]] | None = None,
        conceptual_links: list[dict[str, Any]] | None = None,
        commit_hash: str | None = None,
        commit_time: datetime | None = None,
    ) -> int:
        """Add a pending decision.

        Auto-drops oldest if >100 pending (FIFO).

        Args:
            session_id: LLM session that created this decision
            title: Short title for the decision
            description: Full decision context
            reasoning: Why this decision was made
            alternatives: Other options considered
            conceptual_links: Linked conceptual artifacts (code/timeline/changelog refs)
            commit_hash: Git commit hash at time of decision
            commit_time: Commit timestamp for context

        Returns:
            Decision ID
        """
        ...

    @abstractmethod
    def approve_decision(self, decision_id: int, category: str) -> int:
        """Promote decision to permanent memory.

        Args:
            decision_id: ID of pending decision
            category: Category (e.g., 'architecture', 'pattern', 'convention')

        Returns:
            Approved memory ID
        """
        ...

    @abstractmethod
    def reject_decision(self, decision_id: int) -> None:
        """Mark decision as rejected (not promoted).

        Args:
            decision_id: ID of pending decision
        """
        ...

    @abstractmethod
    def list_pending_decisions(self, limit: int = 20) -> list[Decision]:
        """List oldest pending decisions for review.

        Args:
            limit: Maximum number of decisions to return

        Returns:
            List of pending decisions, oldest first
        """
        ...

    @abstractmethod
    def get_decision(self, decision_id: int) -> Decision | None:
        """Get a specific decision by ID.

        Args:
            decision_id: Decision ID

        Returns:
            Decision if found, None otherwise
        """
        ...

    # ===================================================================
    # Timeline & Changelog Management
    # ===================================================================

    @abstractmethod
    def add_timeline_event(
        self,
        event_type: str,
        from_ref: str,
        to_ref: str,
        summary: str,
        files_changed: list[str] | None = None,
        diff_stats: dict[str, Any] | None = None,
        importance: str = "medium",
        commit_hash: str | None = None,
        commit_time: datetime | None = None,
    ) -> int:
        """Add a timeline event.

        Args:
            event_type: 'tag', 'merge', 'major_change'
            from_ref: Starting git ref (e.g., 'v1.0.0')
            to_ref: Ending git ref (e.g., 'v1.1.0')
            summary: Description of changes
            files_changed: List of affected files
            diff_stats: Statistics about the diff
            importance: 'high', 'medium', 'low'
            commit_hash: Git commit hash for the event
            commit_time: Git commit timestamp

        Returns:
            Timeline event ID
        """
        ...

    @abstractmethod
    def add_changelog(
        self,
        tag: str,
        version: str | None = None,
        summary: str = "",
        breaking_changes: list[str] | None = None,
        features: list[str] | None = None,
        fixes: list[str] | None = None,
        commit_hash: str | None = None,
        commit_time: datetime | None = None,
    ) -> int:
        """Add a changelog entry.

        Args:
            tag: Git tag (e.g., 'v1.2.0')
            version: Semantic version (e.g., '1.2.0')
            summary: Changelog summary
            breaking_changes: List of breaking changes
            features: List of new features
            fixes: List of bug fixes
            commit_hash: Git commit hash for the tag
            commit_time: Git commit timestamp

        Returns:
            Changelog entry ID
        """
        ...

    @abstractmethod
    def get_timeline_events(
        self, from_ref: str | None = None, to_ref: str | None = None, limit: int = 20
    ) -> list[TimelineEvent]:
        """Get timeline events.

        Args:
            from_ref: Filter by starting ref
            to_ref: Filter by ending ref
            limit: Maximum number of events to return

        Returns:
            List of timeline events
        """
        ...

    @abstractmethod
    def get_changelogs(self, limit: int = 20) -> list[ChangelogEntry]:
        """Get changelog entries.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of changelog entries, newest first
        """
        ...

    # ===================================================================
    # Unified Search (Code + Memory)
    # ===================================================================

    @abstractmethod
    def search_all(self, query: str, k: int = 10, vector_weight: float = 0.7) -> list[SearchResult]:
        """Search across both code and memory.

        Args:
            query: Query text
            k: Number of results to return
            vector_weight: Weight for vector search

        Returns:
            List of results from both code chunks and memory entries
        """
        ...

    @abstractmethod
    def search_memory(
        self, query: str, k: int = 10, vector_weight: float = 0.7
    ) -> list[SearchResult]:
        """Search only memory (decisions + approved).

        Args:
            query: Query text
            k: Number of results to return
            vector_weight: Weight for vector search

        Returns:
            List of results from decisions and approved memory
        """
        ...

    # ===================================================================
    # LLM Context Generation
    # ===================================================================

    @abstractmethod
    def generate_context(
        self,
        query: str | None = None,
        include_code: bool = True,
        include_decisions: bool = True,
        include_timeline: bool = True,
        include_changelogs: bool = True,
    ) -> dict[str, Any]:
        """Generate JSON context for LLM consumption.

        Args:
            query: Optional query to include relevant code
            include_code: Include code chunks in context
            include_decisions: Include decisions in context
            include_timeline: Include timeline events in context
            include_changelogs: Include changelogs in context

        Returns:
            Dictionary with project memory context
        """
        ...

    # ===================================================================
    # Import/Export for Collaboration
    # ===================================================================

    @abstractmethod
    def export_memory(
        self,
        output_path: str | Path = ".sia-code/memory.json",
        include_timeline: bool = True,
        include_changelogs: bool = True,
        include_decisions: bool = True,
        include_pending: bool = False,
    ) -> str:
        """Export memory to JSON file for git commit.

        Args:
            output_path: Path to output file
            include_timeline: Include timeline events
            include_changelogs: Include changelog entries
            include_decisions: Include approved decisions
            include_pending: Include pending decisions (for review)

        Returns:
            Path to exported file
        """
        ...

    @abstractmethod
    def import_memory(
        self,
        input_path: str | Path = ".sia-code/memory.json",
        conflict_strategy: str = "newest_wins",
    ) -> ImportResult:
        """Import memory from JSON file (e.g., after git pull).

        Args:
            input_path: Path to input file
            conflict_strategy: 'newest_wins' - keep entry with latest timestamp

        Returns:
            ImportResult with counts (added, updated, skipped)
        """
        ...
