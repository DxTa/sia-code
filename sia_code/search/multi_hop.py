"""Multi-hop code research for discovering code relationships."""

import logging
from dataclasses import dataclass, field
from typing import Set

from ..core.models import Chunk, CodeRelationshipRecord
from ..core.types import ChunkId
from ..storage.base import StorageBackend
from .entity_extractor import EntityExtractor, Entity
from .query_preprocessor import QueryPreprocessor

logger = logging.getLogger(__name__)


@dataclass
class CodeRelationship:
    """Represents a relationship between code entities."""

    from_entity: str
    to_entity: str
    relationship_type: str  # "calls", "imports", "extends", "uses"
    from_chunk: ChunkId | None = None
    to_chunk: ChunkId | None = None


@dataclass
class ResearchResult:
    """Result of multi-hop code research."""

    question: str
    chunks: list[Chunk] = field(default_factory=list)
    relationships: list[CodeRelationship] = field(default_factory=list)
    hops_executed: int = 0
    total_entities_found: int = 0


class MultiHopSearchStrategy:
    """Implements multi-hop code research for architectural discovery."""

    def __init__(self, backend: StorageBackend, max_hops: int = 2):
        """Initialize multi-hop search strategy.

        Args:
            backend: Storage backend for searching
            max_hops: Maximum relationship hops to follow
        """
        self.backend = backend
        self.max_hops = max_hops
        self.extractor = EntityExtractor()
        self._preprocessor = QueryPreprocessor()  # Cache instance to avoid recreation

    def _initial_search(self, question: str, k: int) -> list:
        """Perform initial search with adaptive mode selection.

        Args:
            question: Natural language question
            k: Number of results to return

        Returns:
            List of search results
        """
        if self.backend.embedding_enabled:
            try:
                granularity = getattr(self.backend, "embedding_granularity", "chunk")
                if granularity == "budget":
                    logger.info(f"Using hybrid search for budgeted index query: {question[:100]}")
                    return self.backend.search_hybrid(question, k=k, vector_weight=0.35)

                logger.info(f"Using semantic search for query: {question[:100]}")
                return self.backend.search_semantic(question, k=k)
            except Exception as e:
                logger.warning(
                    f"Semantic/hybrid search failed ({e.__class__.__name__}: {str(e)}), "
                    "falling back to lexical search"
                )
                # Fall through to lexical search

        # Lexical search path
        logger.info(f"Using lexical search for query: {question[:100]}")
        processed_query = self._preprocessor.preprocess(question)
        search_query = processed_query or question
        if not processed_query:
            logger.debug(f"Preprocessing returned empty for query: {question[:100]}")
        return self.backend.search_lexical(search_query, k=k)

    def _expand_from_persisted_graph(
        self,
        seed_chunks: list[Chunk],
        visited_chunks: Set[ChunkId],
        max_results_per_hop: int,
        max_total_chunks: int,
    ) -> tuple[list[Chunk], list[CodeRelationship]]:
        """Expand initial results using persisted relationship edges."""
        discovered_chunks: list[Chunk] = []
        graph_relationships: list[CodeRelationship] = []

        for source_chunk in seed_chunks[:max_results_per_hop]:
            if len(visited_chunks) >= max_total_chunks:
                break

            persisted_edges = self.backend.get_code_relationships(
                from_entity=source_chunk.symbol,
                limit=max_results_per_hop,
            )

            for edge in persisted_edges:
                if len(visited_chunks) >= max_total_chunks:
                    break

                try:
                    target_results = self.backend.search_lexical(edge.to_entity, k=1)
                except Exception as exc:
                    logger.debug(f"Persisted graph lookup failed for {edge.to_entity}: {exc}")
                    continue

                for target_result in target_results:
                    target_chunk = target_result.chunk

                    if target_chunk.id and target_chunk.id in visited_chunks:
                        continue

                    discovered_chunks.append(target_chunk)
                    if target_chunk.id:
                        visited_chunks.add(target_chunk.id)

                    graph_relationships.append(
                        CodeRelationship(
                            from_entity=source_chunk.symbol,
                            to_entity=target_chunk.symbol,
                            relationship_type=edge.relationship_type,
                            from_chunk=source_chunk.id,
                            to_chunk=target_chunk.id,
                        )
                    )

        return discovered_chunks, graph_relationships

    def _persist_relationships(self, relationships: list[CodeRelationship]) -> None:
        """Persist discovered relationships to storage."""
        if not relationships:
            return

        relationship_records = [
            CodeRelationshipRecord(
                from_entity=rel.from_entity,
                to_entity=rel.to_entity,
                relationship_type=rel.relationship_type,
                from_chunk_id=str(rel.from_chunk) if rel.from_chunk else None,
                to_chunk_id=str(rel.to_chunk) if rel.to_chunk else None,
            )
            for rel in relationships
        ]
        self.backend.add_code_relationships(relationship_records)

    def research(
        self, question: str, max_results_per_hop: int = 5, max_total_chunks: int = 50
    ) -> ResearchResult:
        """Perform multi-hop code research.

        Automatically discovers code relationships by:
        1. Initial search for the question
        2. Extract entities (function calls, imports, classes) from results
        3. Follow entity references to discover related code
        4. Build relationship graph

        Args:
            question: Natural language question about the codebase
            max_results_per_hop: Maximum results to explore per hop
            max_total_chunks: Safety limit on total chunks

        Returns:
            Research result with chunks and relationships
        """
        visited_chunks: Set[ChunkId] = set()
        visited_entities: Set[str] = set()
        relationships: list[CodeRelationship] = []
        all_chunks: list[Chunk] = []

        # Hop 0: Initial search - adaptive based on embedding availability
        initial_results = self._initial_search(question, max_results_per_hop)

        if not initial_results:
            return ResearchResult(question=question, chunks=[], relationships=[], hops_executed=0)

        # Process initial results
        for result in initial_results:
            chunk = result.chunk
            all_chunks.append(chunk)
            if chunk.id:
                visited_chunks.add(chunk.id)

        # Expand from persisted graph relationships before entity-based hops.
        graph_chunks, graph_relationships = self._expand_from_persisted_graph(
            seed_chunks=all_chunks,
            visited_chunks=visited_chunks,
            max_results_per_hop=max_results_per_hop,
            max_total_chunks=max_total_chunks,
        )
        all_chunks.extend(graph_chunks)
        relationships.extend(graph_relationships)

        # Multi-hop exploration
        current_hop = 0
        entities_to_explore: list[tuple[Entity, Chunk]] = []

        # Extract entities from initial chunks
        for chunk in all_chunks[:]:
            entities = self.extractor.extract_from_chunk(chunk)
            for entity in entities:
                if entity.name not in visited_entities:
                    entities_to_explore.append((entity, chunk))
                    visited_entities.add(entity.name)

        # Follow entity references
        while current_hop < self.max_hops and entities_to_explore:
            current_hop += 1
            next_entities: list[tuple[Entity, Chunk]] = []

            # Explore each entity
            for entity, source_chunk in entities_to_explore[:max_results_per_hop]:
                if len(visited_chunks) >= max_total_chunks:
                    break

                # Search for this entity
                try:
                    entity_results = self.backend.search_lexical(entity.name, k=3)
                except Exception as e:
                    logger.debug(f"Entity search failed for {entity.name}: {e}")
                    continue

                # Process results
                for entity_result in entity_results:
                    target_chunk = entity_result.chunk

                    # Skip if already visited
                    if target_chunk.id and target_chunk.id in visited_chunks:
                        continue

                    # Add to results
                    all_chunks.append(target_chunk)
                    if target_chunk.id:
                        visited_chunks.add(target_chunk.id)

                    # Record relationship
                    relationships.append(
                        CodeRelationship(
                            from_entity=source_chunk.symbol,
                            to_entity=target_chunk.symbol,
                            relationship_type=entity.entity_type,
                            from_chunk=source_chunk.id,
                            to_chunk=target_chunk.id,
                        )
                    )

                    # Extract entities from this chunk for next hop
                    if current_hop < self.max_hops:
                        new_entities = self.extractor.extract_from_chunk(target_chunk)
                        for new_entity in new_entities:
                            if new_entity.name not in visited_entities:
                                next_entities.append((new_entity, target_chunk))
                                visited_entities.add(new_entity.name)

            # Prepare for next hop
            entities_to_explore = next_entities

        self._persist_relationships(relationships)

        return ResearchResult(
            question=question,
            chunks=all_chunks,
            relationships=relationships,
            hops_executed=current_hop,
            total_entities_found=len(visited_entities),
        )

    def build_call_graph(self, relationships: list[CodeRelationship]) -> dict[str, list[dict]]:
        """Build call graph from relationships.

        Args:
            relationships: List of code relationships

        Returns:
            Call graph as adjacency list
        """
        graph: dict[str, list[dict]] = {}

        for rel in relationships:
            if rel.from_entity not in graph:
                graph[rel.from_entity] = []

            graph[rel.from_entity].append(
                {"target": rel.to_entity, "type": rel.relationship_type, "chunk_id": rel.to_chunk}
            )

        return graph

    def get_entry_points(self, relationships: list[CodeRelationship]) -> list[str]:
        """Identify entry points (entities with no incoming edges).

        Args:
            relationships: List of code relationships

        Returns:
            List of entry point entity names
        """
        all_entities = set()
        targets = set()

        for rel in relationships:
            all_entities.add(rel.from_entity)
            all_entities.add(rel.to_entity)
            targets.add(rel.to_entity)

        # Entry points are entities that are never targets
        entry_points = all_entities - targets
        return list(entry_points)
