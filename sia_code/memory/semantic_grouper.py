"""Semantic file grouping using existing code-chunk embeddings.

Finds files semantically related to a target by querying the
existing sia-code index (no new vector store needed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.sqlite_vec_backend import SqliteVecBackend

from .blast_radius import CoupledFile


@dataclass
class SemanticRelation:
    """A file semantically related to the target."""

    file_path: str
    similarity_score: float  # 0.0-1.0
    relation_type: str = "semantic"  # "semantic", "co-change", "both"


@dataclass
class EnrichedRelation:
    """Combined git + semantic relation."""

    file_path: str
    git_coupling: float  # 0.0-1.0 from co-change
    semantic_similarity: float  # 0.0-1.0 from embeddings
    combined_score: float
    relation_type: str  # "co-change", "semantic", "both"


class SemanticFileGrouper:
    """Find semantically related files using existing indexed embeddings.

    Leverages the persistent sqlite-vec index that sia-code already maintains.
    No new embedding work — just queries existing chunks.

    Strategy:
    1. Get chunks belonging to target file from index
    2. For each chunk, find nearest neighbors in vector space
    3. Aggregate neighbor chunks by file_path
    4. Score by sum of similarity scores per file
    """

    def __init__(self, backend: "SqliteVecBackend"):
        self.backend = backend

    def is_available(self) -> bool:
        """Check if semantic search is available (index exists + embeddings enabled)."""
        return (
            self.backend is not None
            and self.backend.conn is not None
            and self.backend.embedding_enabled
        )

    def related_files(self, file_path: str, k: int = 10) -> list[SemanticRelation]:
        """Find files semantically related to target using existing embeddings.

        Args:
            file_path: Target file path (relative to repo root).
            k: Max related files to return.

        Returns:
            List of SemanticRelation sorted by similarity score.
        """
        if not self.is_available():
            return []

        # Use search_files with file content as implicit query
        # Strategy: get the target file's content/chunks, find similar files
        try:
            # Read chunks for target file from DB
            cursor = self.backend.conn.cursor()
            cursor.execute(
                "SELECT content FROM chunks WHERE file_path = ? LIMIT 5",
                (file_path,),
            )
            rows = cursor.fetchall()

            if not rows:
                # File not in index — try with variations
                cursor.execute(
                    "SELECT content FROM chunks WHERE file_path LIKE ? LIMIT 5",
                    (f"%{file_path}",),
                )
                rows = cursor.fetchall()

            if not rows:
                return []

            # Use first few chunks as query text
            query_text = "\n".join(row[0][:500] for row in rows[:3])

            # Search for similar files
            results = self.backend.search_files(
                query=query_text, k=k + 5, vector_weight=0.9
            )

            # Filter out self and convert to SemanticRelation
            relations: list[SemanticRelation] = []
            max_score = results[0][1] if results else 1.0

            for result_path, score in results:
                # Skip self (match by suffix to handle path variations)
                if result_path.endswith(file_path) or file_path.endswith(result_path):
                    continue
                # Normalize score to 0-1
                normalized = score / max_score if max_score > 0 else 0
                relations.append(
                    SemanticRelation(
                        file_path=result_path,
                        similarity_score=min(normalized, 1.0),
                    )
                )
                if len(relations) >= k:
                    break

            return relations

        except Exception:
            # Graceful degradation — index might be corrupt or unavailable
            return []

    def semantic_blast_radius(
        self,
        file_path: str,
        git_coupled: list[CoupledFile],
        k: int = 15,
        git_weight: float = 0.7,
    ) -> list[EnrichedRelation]:
        """Combine git co-change with semantic similarity.

        Args:
            file_path: Target file.
            git_coupled: Co-change results from BlastRadiusAnalyzer.
            k: Max results.
            git_weight: Weight for git signal (1 - git_weight = semantic weight).

        Returns:
            Merged + ranked EnrichedRelation list.
        """
        semantic_weight = 1.0 - git_weight

        # Get semantic relations
        semantic = self.related_files(file_path, k=k * 2)
        semantic_map: dict[str, float] = {r.file_path: r.similarity_score for r in semantic}

        # Build git map
        git_map: dict[str, float] = {c.path: c.coupling_score for c in git_coupled}

        # Merge all files
        all_files = set(semantic_map.keys()) | set(git_map.keys())
        enriched: list[EnrichedRelation] = []

        for fp in all_files:
            git_score = git_map.get(fp, 0.0)
            sem_score = semantic_map.get(fp, 0.0)
            combined = git_weight * git_score + semantic_weight * sem_score

            # Determine relation type
            if git_score > 0 and sem_score > 0:
                rel_type = "both"
            elif git_score > 0:
                rel_type = "co-change"
            else:
                rel_type = "semantic"

            enriched.append(
                EnrichedRelation(
                    file_path=fp,
                    git_coupling=git_score,
                    semantic_similarity=sem_score,
                    combined_score=combined,
                    relation_type=rel_type,
                )
            )

        enriched.sort(key=lambda e: e.combined_score, reverse=True)
        return enriched[:k]
