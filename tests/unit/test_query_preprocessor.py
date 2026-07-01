"""Unit tests for query preprocessing functionality."""

import pytest
from sia_code.search.query_preprocessor import QueryPreprocessor


class TestQueryPreprocessor:
    """Test query preprocessing for natural language questions."""

    @pytest.fixture
    def preprocessor(self):
        """Create preprocessor instance."""
        return QueryPreprocessor()

    def test_basic_question_preprocessing(self, preprocessor):
        """Test basic question word removal."""
        result = preprocessor.preprocess("How does chip counting work?")
        # "work" is now preserved as it's code-relevant
        assert result == "chip counting work"

    def test_remove_multiple_question_words(self, preprocessor):
        """Test removal of multiple question words."""
        result = preprocessor.preprocess("What is the authentication flow?")
        assert result == "authentication flow"

    def test_preserve_code_identifiers_camelcase(self, preprocessor):
        """Test that CamelCase identifiers are preserved."""
        result = preprocessor.preprocess("Where is ChipCountingService defined?")
        assert "ChipCountingService" in result
        assert "defined" in result

    def test_preserve_code_identifiers_snake_case(self, preprocessor):
        """Test that snake_case identifiers are preserved."""
        result = preprocessor.preprocess("How does load_config work?")
        assert "load_config" in result

    def test_preserve_constants(self, preprocessor):
        """Test that CONSTANT identifiers are preserved."""
        result = preprocessor.preprocess("What is MAX_RETRIES?")
        assert "MAX_RETRIES" in result

    def test_mixed_question(self, preprocessor):
        """Test question with mixed elements."""
        result = preprocessor.preprocess("What calls the process_data function?")
        assert "calls" in result
        assert "process_data" in result

    def test_punctuation_removal(self, preprocessor):
        """Test that punctuation is removed."""
        result = preprocessor.preprocess("How does this work?!")
        assert "?" not in result
        assert "!" not in result

    def test_empty_string(self, preprocessor):
        """Test handling of empty string."""
        result = preprocessor.preprocess("")
        assert result == ""

    def test_all_stop_words(self, preprocessor):
        """Test question with mostly stop words."""
        result = preprocessor.preprocess("How does it work?")
        # "work" is now preserved as it's code-relevant
        assert result == "work"

    def test_single_keyword(self, preprocessor):
        """Test single keyword passes through."""
        result = preprocessor.preprocess("healthcheck")
        assert result == "healthcheck"

    def test_keyword_only_no_processing_needed(self, preprocessor):
        """Test that pure keyword queries pass through unchanged."""
        result = preprocessor.preprocess("chip counting")
        assert result == "chip counting"

    def test_preserve_underscores_in_identifiers(self, preprocessor):
        """Test that underscores in snake_case are preserved."""
        result = preprocessor.preprocess("Where is get_api_url?")
        assert "get_api_url" in result

    def test_mixed_case_identifier(self, preprocessor):
        """Test mixed case like getAPIUrl."""
        result = preprocessor.preprocess("What is getAPIUrl?")
        assert "getAPIUrl" in result

    def test_multiple_code_identifiers(self, preprocessor):
        """Test multiple code identifiers in one question."""
        result = preprocessor.preprocess("How does ChipCountingService use load_config?")
        assert "ChipCountingService" in result
        assert "load_config" in result
        assert "use" in result

    def test_expand_variants_returns_2_to_3_unique_forms(self, preprocessor):
        """Expanded variants should keep raw + focused forms without duplicates."""
        variants = preprocessor.expand_variants(
            "How does ChipCountingService use load_config in authentication flow?"
        )
        assert 2 <= len(variants) <= 3
        assert variants[0] == "How does ChipCountingService use load_config in authentication flow?"
        assert any("ChipCountingService" in v for v in variants)
        assert any("load_config" in v for v in variants)
        assert len({v.lower() for v in variants}) == len(variants)

    def test_expand_variants_synthesizes_code_forms(self, preprocessor):
        variants = preprocessor.expand_variants(
            "How does stable diffusion trainer config work?"
        )
        combined = " ".join(variants)
        assert "StableDiffusionTrainer" in combined or "stable_diffusion_trainer" in combined

    def test_expand_variants_synthesizes_api_forms(self, preprocessor):
        variants = preprocessor.expand_variants("How does task REST API work?")
        combined = " ".join(variants)
        assert any(x in combined for x in ["TaskViewSet", "TaskSerializer", "TaskAPIView"])


class TestExtractKeywords:
    """Test keyword extraction specifically."""

    @pytest.fixture
    def preprocessor(self):
        """Create preprocessor instance."""
        return QueryPreprocessor()

    def test_extract_keywords_basic(self, preprocessor):
        """Test basic keyword extraction."""
        keywords = preprocessor.extract_keywords("How does chip counting work?")
        assert "chip" in keywords
        assert "counting" in keywords
        assert "how" not in keywords
        assert "does" not in keywords
        assert "work" in keywords  # "work" is now preserved (code-relevant)

    def test_extract_keywords_preserves_order(self, preprocessor):
        """Test that keyword order is preserved."""
        keywords = preprocessor.extract_keywords("What is authentication flow?")
        # Order should be preserved
        assert keywords == ["authentication", "flow"]

    def test_extract_keywords_code_identifier(self, preprocessor):
        """Test that code identifiers are extracted."""
        keywords = preprocessor.extract_keywords("ChipCountingService is a class")
        assert "ChipCountingService" in keywords
        # "is", "a", "class" are stop words


class TestCodeIdentifierDetection:
    """Test code identifier detection logic."""

    @pytest.fixture
    def preprocessor(self):
        """Create preprocessor instance."""
        return QueryPreprocessor()

    def test_detect_snake_case(self, preprocessor):
        """Test detection of snake_case."""
        assert preprocessor._is_code_identifier("load_config") is True
        assert preprocessor._is_code_identifier("get_api_url") is True

    def test_detect_camel_case(self, preprocessor):
        """Test detection of CamelCase."""
        assert preprocessor._is_code_identifier("ChipCountingService") is True
        assert preprocessor._is_code_identifier("BaccaratAPI") is True
        assert preprocessor._is_code_identifier("loadConfig") is True

    def test_detect_constant(self, preprocessor):
        """Test detection of CONSTANTS."""
        assert preprocessor._is_code_identifier("MAX_RETRIES") is True
        assert preprocessor._is_code_identifier("API_URL") is True

    def test_not_code_identifier_lowercase(self, preprocessor):
        """Test that simple lowercase words are not code identifiers."""
        assert preprocessor._is_code_identifier("hello") is False
        assert preprocessor._is_code_identifier("world") is False

    def test_not_code_identifier_single_char(self, preprocessor):
        """Test single character is not treated as constant."""
        assert preprocessor._is_code_identifier("A") is False
        assert preprocessor._is_code_identifier("I") is False

    def test_empty_string(self, preprocessor):
        """Test empty string is not code identifier."""
        assert preprocessor._is_code_identifier("") is False


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    @pytest.fixture
    def preprocessor(self):
        """Create preprocessor instance."""
        return QueryPreprocessor()

    def test_whitespace_only(self, preprocessor):
        """Test whitespace-only input."""
        result = preprocessor.preprocess("   ")
        assert result == ""

    def test_multiple_spaces(self, preprocessor):
        """Test multiple spaces between words."""
        result = preprocessor.preprocess("chip    counting    service")
        assert result == "chip counting service"

    def test_special_characters(self, preprocessor):
        """Test various special characters are removed."""
        result = preprocessor.preprocess("chip@counting#service$")
        # Should only keep alphanumeric
        assert "chip" in result
        assert "counting" in result
        assert "service" in result
        assert "@" not in result
        assert "#" not in result
        assert "$" not in result

    def test_numbers_preserved(self, preprocessor):
        """Test that numbers are preserved."""
        result = preprocessor.preprocess("What is API version 2?")
        assert "API" in result
        assert "version" in result
        assert "2" in result

    def test_mixed_punctuation(self, preprocessor):
        """Test mixed punctuation handling."""
        result = preprocessor.preprocess("How does this work?!?")
        assert "?" not in result
        assert "!" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
