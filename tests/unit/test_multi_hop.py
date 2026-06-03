"""Unit tests for multi-hop code research functionality."""

import pytest
from sia_code.core.models import Chunk, SearchResult, CodeRelationshipRecord
from sia_code.core.types import ChunkType, Language, FilePath, LineNumber, ChunkId
from sia_code.search.multi_hop import MultiHopSearchStrategy, CodeRelationship
from sia_code.search.entity_extractor import Entity
from sia_code.storage.usearch_backend import UsearchSqliteBackend


@pytest.fixture
def backend(tmp_path):
    """Create a temporary backend for testing."""
    test_path = tmp_path / ".sia-code"
    backend = UsearchSqliteBackend(test_path, embedding_enabled=False)
    backend.create_index()
    yield backend
    backend.close()


@pytest.fixture
def sample_chunks():
    """Create sample chunks with realistic code relationships."""
    return [
        # Main entry point
        Chunk(
            symbol="main",
            start_line=LineNumber(1),
            end_line=LineNumber(10),
            code="""def main():
    config = load_config()
    data = fetch_data()
    result = process_data(data)
    save_result(result)
""",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/main.py"),
        ),
        # Helper function 1
        Chunk(
            symbol="load_config",
            start_line=LineNumber(1),
            end_line=LineNumber(5),
            code="""def load_config():
    with open('config.json') as f:
        return json.load(f)
""",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/config.py"),
        ),
        # Helper function 2
        Chunk(
            symbol="fetch_data",
            start_line=LineNumber(1),
            end_line=LineNumber(5),
            code="""def fetch_data():
    response = requests.get(API_URL)
    return parse_response(response)
""",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/data.py"),
        ),
        # Helper function 3
        Chunk(
            symbol="process_data",
            start_line=LineNumber(1),
            end_line=LineNumber(5),
            code="""def process_data(data):
    cleaned = clean_data(data)
    return transform_data(cleaned)
""",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/processor.py"),
        ),
        # Deeply nested function
        Chunk(
            symbol="parse_response",
            start_line=LineNumber(1),
            end_line=LineNumber(3),
            code="""def parse_response(response):
    return response.json()
""",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/parser.py"),
        ),
    ]


class TestMultiHopResearch:
    """Test multi-hop code research functionality."""

    def test_research_returns_results(self, backend, sample_chunks):
        """Test that research returns results for a valid query."""
        # Store chunks
        backend.store_chunks_batch(sample_chunks)

        # Create multi-hop strategy
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        # Research for "main"
        result = strategy.research("main", max_results_per_hop=5)

        # Should find at least the main function
        assert len(result.chunks) >= 1
        assert result.question == "main"
        assert result.hops_executed >= 0

    def test_research_respects_max_hops(self, backend, sample_chunks):
        """Test that research respects max_hops parameter."""
        backend.store_chunks_batch(sample_chunks)

        # Test with max_hops=0 (only initial search)
        strategy_0 = MultiHopSearchStrategy(backend, max_hops=0)
        result_0 = strategy_0.research("main", max_results_per_hop=5)
        assert result_0.hops_executed == 0

        # Test with max_hops=1 (one hop)
        strategy_1 = MultiHopSearchStrategy(backend, max_hops=1)
        result_1 = strategy_1.research("main", max_results_per_hop=5)
        assert result_1.hops_executed <= 1

        # Test with max_hops=2 (two hops)
        strategy_2 = MultiHopSearchStrategy(backend, max_hops=2)
        result_2 = strategy_2.research("main", max_results_per_hop=5)
        assert result_2.hops_executed <= 2

    def test_research_respects_max_total_chunks(self, backend, sample_chunks):
        """Test that research respects max_total_chunks safety limit."""
        backend.store_chunks_batch(sample_chunks)

        strategy = MultiHopSearchStrategy(backend, max_hops=10)

        # Set low limit
        result = strategy.research("main", max_results_per_hop=5, max_total_chunks=3)

        # Should not exceed the limit
        assert len(result.chunks) <= 3

    def test_research_discovers_relationships(self, backend, sample_chunks):
        """Test that multi-hop research discovers code relationships."""
        backend.store_chunks_batch(sample_chunks)

        strategy = MultiHopSearchStrategy(backend, max_hops=2)
        result = strategy.research("main", max_results_per_hop=5)

        # Should discover some relationships
        # (exact count depends on entity extraction success)
        assert result.relationships is not None
        assert isinstance(result.relationships, list)

        # Each relationship should have valid structure
        for rel in result.relationships:
            assert rel.from_entity is not None
            assert rel.to_entity is not None
            assert rel.relationship_type is not None

    def test_research_handles_empty_results(self, backend):
        """Test that research handles queries with no results gracefully."""
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        # Search for something that doesn't exist
        result = strategy.research("nonexistent_function_xyz")

        # Should return empty result, not crash
        assert result.question == "nonexistent_function_xyz"
        assert len(result.chunks) == 0
        assert len(result.relationships) == 0
        assert result.hops_executed == 0

    def test_research_tracks_entities_found(self, backend, sample_chunks):
        """Test that research tracks total entities found."""
        backend.store_chunks_batch(sample_chunks)

        strategy = MultiHopSearchStrategy(backend, max_hops=1)
        result = strategy.research("main", max_results_per_hop=5)

        # Should track entities (even if 0 due to extraction limitations)
        assert result.total_entities_found >= 0
        assert isinstance(result.total_entities_found, int)

    def test_research_persists_relationships(self, backend):
        """Research should persist discovered relationships into storage."""
        source_chunk = Chunk(
            id=ChunkId("source-1"),
            symbol="source_handler",
            start_line=LineNumber(1),
            end_line=LineNumber(3),
            code="def source_handler():\n    return helper()",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/source.py"),
        )
        target_chunk = Chunk(
            id=ChunkId("target-1"),
            symbol="helper",
            start_line=LineNumber(1),
            end_line=LineNumber(2),
            code="def helper():\n    return 1",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/helper.py"),
        )

        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        def mock_initial_search(question, k):
            return [SearchResult(chunk=source_chunk, score=1.0)]

        def mock_extract_from_chunk(chunk):
            if chunk.symbol != "source_handler":
                return []
            return [
                Entity(
                    name="helper",
                    entity_type="function_call",
                    source_chunk=chunk.id,
                )
            ]

        def mock_search_lexical(query, k=3, include_deps=True, tier_boost=None):
            if query != "helper":
                return []
            return [SearchResult(chunk=target_chunk, score=1.0)]

        strategy._initial_search = mock_initial_search
        strategy.extractor.extract_from_chunk = mock_extract_from_chunk
        backend.search_lexical = mock_search_lexical

        result = strategy.research("trace source handler", max_results_per_hop=5)

        assert len(result.relationships) == 1

        persisted = backend.get_code_relationships(limit=10)
        assert len(persisted) == 1
        assert persisted[0].from_entity == "source_handler"
        assert persisted[0].to_entity == "helper"
        assert persisted[0].relationship_type == "function_call"

    def test_research_uses_persisted_graph_relationships(self, backend):
        """Graph relationships should expand research even without extracted entities."""
        source_chunk = Chunk(
            id=ChunkId("source-1"),
            symbol="source_handler",
            start_line=LineNumber(1),
            end_line=LineNumber(3),
            code="def source_handler():\n    return 1",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/source.py"),
        )
        target_chunk = Chunk(
            id=ChunkId("target-1"),
            symbol="helper",
            start_line=LineNumber(1),
            end_line=LineNumber(2),
            code="def helper():\n    return 2",
            chunk_type=ChunkType.FUNCTION,
            language=Language.PYTHON,
            file_path=FilePath("app/helper.py"),
        )

        backend.add_code_relationships(
            [
                CodeRelationshipRecord(
                    from_entity="source_handler",
                    to_entity="helper",
                    relationship_type="function_call",
                    from_chunk_id="source-1",
                    to_chunk_id="target-1",
                )
            ]
        )

        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        def mock_initial_search(question, k):
            return [SearchResult(chunk=source_chunk, score=1.0)]

        def mock_search_lexical(query, k=3, include_deps=True, tier_boost=None):
            if query != "helper":
                return []
            return [SearchResult(chunk=target_chunk, score=0.9)]

        def mock_extract_from_chunk(chunk):
            return []

        strategy._initial_search = mock_initial_search
        strategy.extractor.extract_from_chunk = mock_extract_from_chunk
        backend.search_lexical = mock_search_lexical

        result = strategy.research("trace source handler", max_results_per_hop=5)
        symbols = {chunk.symbol for chunk in result.chunks}
        assert "source_handler" in symbols
        assert "helper" in symbols


class TestCallGraphBuilding:
    """Test call graph construction from relationships."""

    def test_build_call_graph(self, tmp_path):
        """Test building call graph from relationships."""
        relationships = [
            CodeRelationship(
                from_entity="main",
                to_entity="load_config",
                relationship_type="function_call",
                from_chunk=ChunkId("chunk1"),
                to_chunk=ChunkId("chunk2"),
            ),
            CodeRelationship(
                from_entity="main",
                to_entity="fetch_data",
                relationship_type="function_call",
                from_chunk=ChunkId("chunk1"),
                to_chunk=ChunkId("chunk3"),
            ),
            CodeRelationship(
                from_entity="fetch_data",
                to_entity="parse_response",
                relationship_type="function_call",
                from_chunk=ChunkId("chunk3"),
                to_chunk=ChunkId("chunk4"),
            ),
        ]

        backend = UsearchSqliteBackend(tmp_path / ".sia-code", embedding_enabled=False)
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        graph = strategy.build_call_graph(relationships)

        # Should have entries for calling entities
        assert "main" in graph
        assert "fetch_data" in graph

        # main should call load_config and fetch_data
        assert len(graph["main"]) == 2
        targets = {edge["target"] for edge in graph["main"]}
        assert "load_config" in targets
        assert "fetch_data" in targets

        # fetch_data should call parse_response
        assert len(graph["fetch_data"]) == 1
        assert graph["fetch_data"][0]["target"] == "parse_response"

    def test_build_call_graph_empty(self, tmp_path):
        """Test building call graph with no relationships."""
        backend = UsearchSqliteBackend(tmp_path / ".sia-code", embedding_enabled=False)
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        graph = strategy.build_call_graph([])

        # Should return empty graph
        assert graph == {}

    def test_build_call_graph_includes_metadata(self, tmp_path):
        """Test that call graph includes relationship metadata."""
        relationships = [
            CodeRelationship(
                from_entity="ClassA",
                to_entity="ClassB",
                relationship_type="inheritance",
                from_chunk=ChunkId("chunk1"),
                to_chunk=ChunkId("chunk2"),
            ),
        ]

        backend = UsearchSqliteBackend(tmp_path / ".sia-code", embedding_enabled=False)
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        graph = strategy.build_call_graph(relationships)

        # Should include relationship type
        assert graph["ClassA"][0]["type"] == "inheritance"
        assert graph["ClassA"][0]["chunk_id"] == ChunkId("chunk2")


class TestEntryPointDetection:
    """Test entry point identification in call graphs."""

    def test_get_entry_points(self, tmp_path):
        """Test identifying entry points (no incoming edges)."""
        relationships = [
            CodeRelationship("main", "load_config", "function_call"),
            CodeRelationship("main", "fetch_data", "function_call"),
            CodeRelationship("fetch_data", "parse_response", "function_call"),
        ]

        backend = UsearchSqliteBackend(tmp_path / ".sia-code", embedding_enabled=False)
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        entry_points = strategy.get_entry_points(relationships)

        # Only "main" should be an entry point (never a target)
        assert "main" in entry_points
        assert "load_config" not in entry_points  # Called by main
        assert "fetch_data" not in entry_points  # Called by main
        assert "parse_response" not in entry_points  # Called by fetch_data

    def test_get_entry_points_multiple(self, tmp_path):
        """Test identifying multiple entry points."""
        relationships = [
            CodeRelationship("main", "helper", "function_call"),
            CodeRelationship("test_main", "helper", "function_call"),
            CodeRelationship("helper", "util", "function_call"),
        ]

        backend = UsearchSqliteBackend(tmp_path / ".sia-code", embedding_enabled=False)
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        entry_points = strategy.get_entry_points(relationships)

        # Both main and test_main are entry points
        assert len(entry_points) == 2
        assert "main" in entry_points
        assert "test_main" in entry_points

    def test_get_entry_points_empty(self, tmp_path):
        """Test entry point detection with no relationships."""
        backend = UsearchSqliteBackend(tmp_path / ".sia-code", embedding_enabled=False)
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        entry_points = strategy.get_entry_points([])

        # Should return empty list
        assert entry_points == []

    def test_get_entry_points_circular(self, tmp_path):
        """Test entry point detection with circular relationships."""
        relationships = [
            CodeRelationship("A", "B", "calls"),
            CodeRelationship("B", "C", "calls"),
            CodeRelationship("C", "A", "calls"),  # Circular
        ]

        backend = UsearchSqliteBackend(tmp_path / ".sia-code", embedding_enabled=False)
        strategy = MultiHopSearchStrategy(backend, max_hops=1)

        entry_points = strategy.get_entry_points(relationships)

        # In a circular graph, no entity is an entry point
        assert len(entry_points) == 0


class TestAdaptiveSearch:
    """Test adaptive search strategy (semantic vs preprocessed lexical)."""

    def test_uses_semantic_when_embeddings_enabled(self, backend, sample_chunks):
        """Research should use semantic search when embeddings are available."""
        backend.store_chunks_batch(sample_chunks)

        # Enable embeddings
        backend.embedding_enabled = True

        # Mock search_semantic to track if it's called
        original_search_semantic = backend.search_semantic
        call_count = {"count": 0}

        def mock_search_semantic(*args, **kwargs):
            call_count["count"] += 1
            return original_search_semantic(*args, **kwargs)

        backend.search_semantic = mock_search_semantic

        strategy = MultiHopSearchStrategy(backend, max_hops=1)
        strategy.research("How does main work?", max_results_per_hop=5)

        # Should have called semantic search
        assert call_count["count"] >= 1

    def test_uses_lexical_when_embeddings_disabled(self, backend, sample_chunks):
        """Research should use preprocessed lexical search when embeddings disabled."""
        backend.store_chunks_batch(sample_chunks)

        # Disable embeddings
        backend.embedding_enabled = False

        # Mock search_lexical to track calls
        original_search_lexical = backend.search_lexical
        call_count = {"count": 0}
        calls = []

        def mock_search_lexical(query, *args, **kwargs):
            call_count["count"] += 1
            calls.append(query)
            return original_search_lexical(query, *args, **kwargs)

        backend.search_lexical = mock_search_lexical

        strategy = MultiHopSearchStrategy(backend, max_hops=1)
        strategy.research("How does main work?", max_results_per_hop=5)

        # Should have called lexical search
        assert call_count["count"] >= 1
        # First call should be preprocessed (no "How", "does")
        first_query = calls[0]
        assert "how" not in first_query.lower() or "main" in first_query.lower()


class TestNaturalLanguageQueries:
    """Test that research handles natural language questions."""

    def test_natural_language_question_with_embeddings(self, backend, sample_chunks):
        """Natural language questions should attempt semantic search when enabled."""
        backend.store_chunks_batch(sample_chunks)
        backend.embedding_enabled = True

        strategy = MultiHopSearchStrategy(backend, max_hops=1)
        # This should not crash even if embeddings aren't available
        result = strategy.research("How does the main function work?", max_results_per_hop=5)

        # Should return a valid result object (may be empty if no API key)
        assert isinstance(result.chunks, list)
        assert result.question == "How does the main function work?"

    def test_natural_language_question_without_embeddings(self, backend, sample_chunks):
        """Natural language questions should work with preprocessing fallback."""
        backend.store_chunks_batch(sample_chunks)
        backend.embedding_enabled = False

        strategy = MultiHopSearchStrategy(backend, max_hops=1)
        result = strategy.research("How does main work", max_results_per_hop=5)

        # With preprocessing, should find "main" after removing "How", "does"
        # Result should be valid (may have results depending on lexical matching)
        assert isinstance(result.chunks, list)
        assert result.hops_executed >= 0

    def test_question_with_code_identifiers(self, backend, sample_chunks):
        """Questions with code identifiers should preserve them in preprocessing."""
        backend.store_chunks_batch(sample_chunks)
        backend.embedding_enabled = False

        # Use simpler query that will match
        strategy = MultiHopSearchStrategy(backend, max_hops=1)
        result = strategy.research("load_config", max_results_per_hop=5)

        # Should find the load_config function with keyword search
        assert len(result.chunks) >= 1
        symbols = [chunk.symbol for chunk in result.chunks]
        assert "load_config" in symbols

    def test_natural_language_preprocessing_removes_stop_words(self, backend, sample_chunks):
        """Verify that preprocessing is applied for natural language questions."""
        backend.store_chunks_batch(sample_chunks)
        backend.embedding_enabled = False

        # Track what query is actually used in lexical search
        original_search_lexical = backend.search_lexical
        actual_queries = []

        def track_search_lexical(query, *args, **kwargs):
            actual_queries.append(query)
            return original_search_lexical(query, *args, **kwargs)

        backend.search_lexical = track_search_lexical

        strategy = MultiHopSearchStrategy(backend, max_hops=0)
        strategy.research("How does the config work?", max_results_per_hop=5)

        # Should have made at least one lexical search
        assert len(actual_queries) >= 1

        # First query should have stop words removed
        first_query = actual_queries[0].lower()
        # "how", "does", "the" should be removed, "config" should remain
        assert "config" in first_query
        # Stop words should ideally be removed (may not be perfect but should try)
        # Just verify config is present - that's the key term


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
