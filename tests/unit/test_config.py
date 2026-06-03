"""Unit tests for configuration management, including gitignore support."""

import pytest
from sia_code.config import Config, IndexingConfig, load_gitignore_patterns


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository structure with .gitignore files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


class TestLoadGitignorePatterns:
    """Test loading patterns from .gitignore files."""

    def test_load_root_gitignore(self, temp_repo):
        """Test loading patterns from root .gitignore."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("*.pyc\n__pycache__/\n.venv/\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert "*.pyc" in patterns
        assert "__pycache__/" in patterns
        assert ".venv/" in patterns

    def test_load_gitignore_with_comments(self, temp_repo):
        """Test that comments and empty lines are skipped."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("# Python files\n*.pyc\n\n# Virtual environments\n.venv/\n\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert "*.pyc" in patterns
        assert ".venv/" in patterns
        # Comments and empty lines should not be in patterns
        assert "# Python files" not in patterns
        assert "" not in patterns

    def test_load_nested_gitignore(self, temp_repo):
        """Test loading patterns from nested .gitignore files."""
        # Root gitignore
        root_gitignore = temp_repo / ".gitignore"
        root_gitignore.write_text("*.pyc\n")

        # Nested gitignore in subdir
        subdir = temp_repo / "src"
        subdir.mkdir()
        nested_gitignore = subdir / ".gitignore"
        nested_gitignore.write_text("*.log\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert "*.pyc" in patterns
        assert "src/*.log" in patterns

    def test_load_deeply_nested_gitignore(self, temp_repo):
        """Test loading patterns from deeply nested .gitignore files."""
        # Create nested structure: repo/src/tests/.gitignore
        deep_dir = temp_repo / "src" / "tests"
        deep_dir.mkdir(parents=True)
        deep_gitignore = deep_dir / ".gitignore"
        deep_gitignore.write_text("*.tmp\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert "src/tests/*.tmp" in patterns

    def test_load_gitignore_with_negation(self, temp_repo):
        """Test handling of negation patterns (!)."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("*.log\n!important.log\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert "*.log" in patterns
        assert "!important.log" in patterns

    def test_load_nested_gitignore_with_negation(self, temp_repo):
        """Test negation patterns in nested .gitignore files."""
        subdir = temp_repo / "src"
        subdir.mkdir()
        nested_gitignore = subdir / ".gitignore"
        nested_gitignore.write_text("*.log\n!debug.log\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert "src/*.log" in patterns
        assert "!src/debug.log" in patterns

    def test_no_gitignore(self, temp_repo):
        """Test behavior when no .gitignore exists."""
        patterns = load_gitignore_patterns(temp_repo)

        assert patterns == []

    def test_empty_gitignore(self, temp_repo):
        """Test behavior with empty .gitignore file."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("")

        patterns = load_gitignore_patterns(temp_repo)

        assert patterns == []

    def test_gitignore_only_comments(self, temp_repo):
        """Test .gitignore with only comments."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("# Comment 1\n# Comment 2\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert patterns == []

    def test_gitignore_with_whitespace(self, temp_repo):
        """Test that patterns with surrounding whitespace are trimmed."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("  *.pyc  \n\t__pycache__/\t\n")

        patterns = load_gitignore_patterns(temp_repo)

        assert "*.pyc" in patterns
        assert "__pycache__/" in patterns

    def test_gitignore_absolute_patterns(self, temp_repo):
        """Test patterns starting with / (rooted patterns)."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("/build\n/dist\n")

        patterns = load_gitignore_patterns(temp_repo)

        # Rooted patterns should be preserved as-is
        assert "/build" in patterns
        assert "/dist" in patterns

    def test_nested_gitignore_absolute_patterns(self, temp_repo):
        """Test that absolute patterns in nested gitignore are preserved."""
        subdir = temp_repo / "src"
        subdir.mkdir()
        nested_gitignore = subdir / ".gitignore"
        nested_gitignore.write_text("/temp\n")

        patterns = load_gitignore_patterns(temp_repo)

        # Absolute pattern should be preserved (not prefixed)
        assert "/temp" in patterns

    def test_gitignore_encoding(self, temp_repo):
        """Test reading gitignore with UTF-8 encoding."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("# Fichiers Python\n*.pyc\n", encoding="utf-8")

        patterns = load_gitignore_patterns(temp_repo)

        assert "*.pyc" in patterns


class TestIndexingConfigEffectivePatterns:
    """Test IndexingConfig.get_effective_exclude_patterns() method."""

    def test_effective_patterns_no_gitignore(self, temp_repo):
        """Test that config patterns are used when no .gitignore exists."""
        config = IndexingConfig()

        patterns = config.get_effective_exclude_patterns(temp_repo)

        # Should return only default config patterns
        assert "node_modules/" in patterns
        assert "__pycache__/" in patterns

    def test_effective_patterns_with_gitignore(self, temp_repo):
        """Test that gitignore patterns are merged with config patterns."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("*.log\n*.tmp\n")

        config = IndexingConfig()
        patterns = config.get_effective_exclude_patterns(temp_repo)

        # Should have both config and gitignore patterns
        assert "node_modules/" in patterns  # From config
        assert "*.log" in patterns  # From gitignore
        assert "*.tmp" in patterns  # From gitignore

    def test_effective_patterns_deduplication(self, temp_repo):
        """Test that duplicate patterns are not included twice."""
        gitignore = temp_repo / ".gitignore"
        # Add a pattern that's already in default config
        gitignore.write_text("node_modules/\n__pycache__/\n*.custom\n")

        config = IndexingConfig()
        patterns = config.get_effective_exclude_patterns(temp_repo)

        # Count occurrences
        node_modules_count = patterns.count("node_modules/")
        pycache_count = patterns.count("__pycache__/")

        assert node_modules_count == 1, "node_modules/ should appear only once"
        assert pycache_count == 1, "__pycache__/ should appear only once"

    def test_effective_patterns_with_nested_gitignore(self, temp_repo):
        """Test merging patterns from nested .gitignore files."""
        # Root gitignore
        root_gitignore = temp_repo / ".gitignore"
        root_gitignore.write_text("*.pyc\n")

        # Nested gitignore
        subdir = temp_repo / "src"
        subdir.mkdir()
        nested_gitignore = subdir / ".gitignore"
        nested_gitignore.write_text("*.log\n")

        config = IndexingConfig()
        patterns = config.get_effective_exclude_patterns(temp_repo)

        # Should have config, root gitignore, and nested gitignore patterns
        assert "node_modules/" in patterns  # From config
        assert "*.pyc" in patterns  # From root gitignore
        assert "src/*.log" in patterns  # From nested gitignore

    def test_custom_exclude_patterns(self, temp_repo):
        """Test that custom config patterns are preserved."""
        gitignore = temp_repo / ".gitignore"
        gitignore.write_text("*.log\n")

        custom_patterns = ["*.custom", "my_dir/"]
        config = IndexingConfig(exclude_patterns=custom_patterns)
        patterns = config.get_effective_exclude_patterns(temp_repo)

        # Should have both custom config and gitignore patterns
        assert "*.custom" in patterns
        assert "my_dir/" in patterns
        assert "*.log" in patterns


def test_indexing_config_defaults():
    """Ensure indexing defaults include batching configuration."""
    config = IndexingConfig()

    assert config.chunk_batch_size == 500


class TestConfigLoadAndSave:
    """Test Config loading and saving."""

    def test_config_load_default(self, temp_repo):
        """Test loading default config."""
        config = Config()

        assert config.indexing is not None
        assert config.storage.backend == "sqlite-vec"
        assert isinstance(config.indexing.exclude_patterns, list)

    def test_config_roundtrip(self, temp_repo):
        """Test saving and loading config."""
        config_path = temp_repo / "config.json"

        # Create and save config
        config = Config()
        config.save(config_path)

        # Load config
        loaded_config = Config.load(config_path)

        assert loaded_config.indexing.max_file_size_mb == config.indexing.max_file_size_mb
        assert loaded_config.indexing.exclude_patterns == config.indexing.exclude_patterns

    def test_config_storage_backend_roundtrip(self, temp_repo):
        """Test storage backend setting survives save/load."""
        config_path = temp_repo / "config.json"

        config = Config()
        config.storage.backend = "usearch"
        config.save(config_path)

        loaded_config = Config.load(config_path)
        assert loaded_config.storage.backend == "usearch"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
