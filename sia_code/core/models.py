"""Core data models for PCI."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .types import ByteOffset, ChunkId, ChunkType, FileId, FilePath, Language, LineNumber


@dataclass(frozen=True)
class Chunk:
    """Represents a semantic code chunk."""

    symbol: str
    start_line: LineNumber
    end_line: LineNumber
    code: str
    chunk_type: ChunkType
    language: Language
    file_path: FilePath
    file_id: FileId | None = None
    id: ChunkId | None = None
    parent_header: str | None = None
    start_byte: ByteOffset | None = None
    end_byte: ByteOffset | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self):
        """Validate chunk data."""
        if self.start_line < 1:
            raise ValueError(f"start_line must be >= 1, got {self.start_line}")
        if self.end_line < self.start_line:
            raise ValueError(
                f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
            )
        if not self.code:
            raise ValueError("code cannot be empty")

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    @property
    def char_count(self) -> int:
        return len(self.code.replace(" ", "").replace("\n", "").replace("\t", ""))

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    def overlaps_with(self, other: "Chunk") -> bool:
        return not (self.end_line < other.start_line or self.start_line > other.end_line)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "code": self.code,
            "chunk_type": self.chunk_type.value,
            "language": self.language.value,
            "file_path": str(self.file_path),
            "file_id": self.file_id,
            "id": self.id,
            "parent_header": self.parent_header,
            "metadata": self.metadata,
        }

    def with_metadata(self, extra: dict[str, Any]) -> "Chunk":
        """Return a new Chunk with additional metadata merged.

        Since Chunk is frozen, this creates a new instance.

        Args:
            extra: Additional metadata to merge with existing metadata

        Returns:
            New Chunk instance with merged metadata
        """
        merged = {**self.metadata, **extra}
        return Chunk(
            symbol=self.symbol,
            start_line=self.start_line,
            end_line=self.end_line,
            code=self.code,
            chunk_type=self.chunk_type,
            language=self.language,
            file_path=self.file_path,
            file_id=self.file_id,
            id=self.id,
            parent_header=self.parent_header,
            start_byte=self.start_byte,
            end_byte=self.end_byte,
            metadata=merged,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True)
class File:
    """Represents a source code file."""

    path: FilePath
    language: Language
    size_bytes: int
    mtime: float
    id: FileId | None = None

    @classmethod
    def from_path(cls, path: Path) -> "File":
        stat = path.stat()
        language = Language.from_extension(path.suffix)
        return cls(
            path=FilePath(str(path)),
            language=language,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
        )


@dataclass
class SearchResult:
    """Represents a search result."""

    chunk: Chunk
    score: float
    snippet: str | None = None
    highlights: list[tuple[int, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert search result to dictionary for JSON serialization."""
        return {
            "chunk": self.chunk.to_dict(),
            "score": self.score,
            "snippet": self.snippet,
            "highlights": self.highlights,
        }


@dataclass
class CodeRelationshipRecord:
    """Persisted relationship between two code entities."""

    from_entity: str
    to_entity: str
    relationship_type: str
    from_chunk_id: str | None = None
    to_chunk_id: str | None = None
    id: int | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert relationship record to dictionary form."""
        return {
            "id": self.id,
            "from_entity": self.from_entity,
            "to_entity": self.to_entity,
            "relationship_type": self.relationship_type,
            "from_chunk_id": self.from_chunk_id,
            "to_chunk_id": self.to_chunk_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class IndexStats:
    """Statistics about the code index."""

    total_files: int = 0
    total_chunks: int = 0
    total_size_bytes: int = 0
    languages: dict[Language, int] = field(default_factory=dict)
    last_indexed: datetime | None = None


# Memory system models


@dataclass
class Decision:
    """Represents a technical decision for the project."""

    id: int
    session_id: str
    title: str
    description: str
    reasoning: str | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    conceptual_links: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # 'pending', 'approved', 'rejected'
    category: str | None = None  # Set when approved
    commit_hash: str | None = None
    commit_time: datetime | None = None
    created_at: datetime | None = None
    approved_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert decision to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "title": self.title,
            "description": self.description,
            "reasoning": self.reasoning,
            "alternatives": self.alternatives,
            "conceptual_links": self.conceptual_links,
            "status": self.status,
            "category": self.category,
            "commit_hash": self.commit_hash,
            "commit_time": self.commit_time.isoformat() if self.commit_time else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
        }


@dataclass
class TimelineEvent:
    """Represents an important event in project history."""

    id: int
    event_type: str  # 'tag', 'merge', 'major_change'
    from_ref: str
    to_ref: str
    summary: str
    files_changed: list[str] = field(default_factory=list)
    diff_stats: dict[str, Any] = field(default_factory=dict)
    importance: str = "medium"  # 'high', 'medium', 'low'
    commit_hash: str | None = None
    commit_time: datetime | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert timeline event to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "from_ref": self.from_ref,
            "to_ref": self.to_ref,
            "summary": self.summary,
            "files_changed": self.files_changed,
            "diff_stats": self.diff_stats,
            "importance": self.importance,
            "commit_hash": self.commit_hash,
            "commit_time": self.commit_time.isoformat() if self.commit_time else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class ChangelogEntry:
    """Represents a changelog entry (typically from git tags)."""

    id: int
    tag: str
    version: str | None = None
    date: datetime | None = None
    summary: str = ""
    breaking_changes: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    commit_hash: str | None = None
    commit_time: datetime | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert changelog entry to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "tag": self.tag,
            "version": self.version,
            "date": self.date.isoformat() if self.date else None,
            "summary": self.summary,
            "breaking_changes": self.breaking_changes,
            "features": self.features,
            "fixes": self.fixes,
            "commit_hash": self.commit_hash,
            "commit_time": self.commit_time.isoformat() if self.commit_time else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class ImportResult:
    """Result of importing memory from file."""

    added: int = 0
    updated: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert import result to dictionary."""
        return {"added": self.added, "updated": self.updated, "skipped": self.skipped}
