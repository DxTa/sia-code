"""Configuration management for PCI."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def load_gitignore_patterns(root: Path) -> list[str]:
    """Load patterns from all .gitignore files in the tree.

    Handles:
    - Root .gitignore
    - Nested .gitignore files (with relative path prefixing)
    - Comments (#)
    - Empty lines
    - Negation patterns (!)

    Args:
        root: Root directory to search for .gitignore files

    Returns:
        List of patterns compatible with pathspec (gitwildmatch)
    """
    patterns = []

    # Walk the directory tree to find all .gitignore files
    for gitignore_path in root.rglob(".gitignore"):
        try:
            rel_dir = gitignore_path.parent.relative_to(root)
            # For root .gitignore, prefix is empty; for nested, use relative path
            prefix = str(rel_dir) + "/" if str(rel_dir) != "." else ""

            with open(gitignore_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue

                    # Handle negation patterns
                    if line.startswith("!"):
                        # Negation patterns need prefix too
                        if prefix and not line.startswith("!/"):
                            patterns.append(f"!{prefix}{line[1:]}")
                        else:
                            patterns.append(line)
                    else:
                        # Regular patterns
                        if prefix and not line.startswith("/"):
                            patterns.append(f"{prefix}{line}")
                        else:
                            patterns.append(line)
        except (OSError, IOError):
            # Skip gitignore files that can't be read
            continue

    return patterns


class EmbeddingConfig(BaseModel):
    """Embedding configuration.

    Supported models (local via sentence-transformers):
    - BGE: "BAAI/bge-small-en-v1.5" (384d), "BAAI/bge-base-en-v1.5" (768d), "BAAI/bge-large-en-v1.5" (1024d)
    - MiniLM: "sentence-transformers/all-MiniLM-L6-v2" (384d)
    - Other HuggingFace models compatible with sentence-transformers
    """

    enabled: bool = True
    provider: str = "huggingface"  # Deprecated legacy field; ignored by local-only runtime
    model: str = "BAAI/bge-base-en-v1.5"  # Model name (see supported models above)
    api_key_env: str = ""  # Deprecated legacy field; ignored by local-only runtime
    dimensions: int = 768  # Embedding dimensions (auto-detected for most models)
    granularity: Literal["chunk", "budget"] = "chunk"
    max_vectors_per_file: int = 0  # 0 = unlimited
    semantic_chunk_types: list[str] = Field(
        default_factory=lambda: ["class", "function", "method", "definition"]
    )
    persistent_cache: bool = True


class IndexingConfig(BaseModel):
    """Indexing configuration."""

    exclude_patterns: list[str] = Field(
        default_factory=lambda: [
            "node_modules/",
            "__pycache__/",
            ".git/",
            "venv/",
            "*venv",
            "build",
            "dist",
            ".venv/",
            "*.pyc",
            "*.pyo",
            "*.so",
            "*.dylib",
            ".pci/",
        ]
    )
    include_patterns: list[str] = Field(default_factory=lambda: ["**/*"])
    max_file_size_mb: int = 5
    chunk_batch_size: int = 500

    def get_effective_exclude_patterns(self, root: Path) -> list[str]:
        """Get combined exclude patterns from config and .gitignore files.

        Automatically loads patterns from all .gitignore files in the tree
        and merges them with the configured exclude_patterns.

        Args:
            root: Root directory to search for .gitignore files

        Returns:
            Combined list of exclude patterns (deduplicated)
        """
        patterns = list(self.exclude_patterns)
        gitignore_patterns = load_gitignore_patterns(root)
        # Merge without duplicates
        for p in gitignore_patterns:
            if p not in patterns:
                patterns.append(p)
        return patterns


class ChunkingConfig(BaseModel):
    """Chunking configuration."""

    max_chunk_size: int = 1200
    min_chunk_size: int = 50
    merge_threshold: float = 0.8
    greedy_merge: bool = True


class SearchConfig(BaseModel):
    """Search configuration."""

    default_limit: int = 10
    multi_hop_enabled: bool = True
    max_hops: int = 2
    flan_query_rewrite: bool = True  # cached-only FLAN extra rewrite candidate (on by default if model cached)
    vector_weight: float = (
        0.7  # Weight for vector search in hybrid (0.0=lexical only, 1.0=semantic only)
    )
    # Configurable tier boosting for search results
    tier_boost: dict[str, float] = Field(
        default_factory=lambda: {
            "project": 1.0,
            "dependency": 0.7,
            "stdlib": 0.5,
        }
    )
    include_dependencies: bool = True  # Default: deps always included in search
    # ponytail: payload size caps to keep MCP results small for LLM context.
    # Upgrade path: raise per-workspace if a caller needs fuller dumps.
    max_result_bytes: int = 4096
    max_relationships: int = 20
    max_matches: int = 20
    # Disk-backed query->result cache (invalidated by index signature + TTL).
    query_cache_enabled: bool = True
    query_cache_ttl_seconds: int = 3600


class DependencyConfig(BaseModel):
    """Dependency indexing configuration."""

    enabled: bool = True
    index_stubs: bool = True  # Index .pyi, .d.ts
    # Languages to index deps for (Phase 1: python, typescript only)
    languages: list[str] = Field(default_factory=lambda: ["python", "typescript", "javascript"])


class DocumentationConfig(BaseModel):
    """Documentation linking configuration."""

    enabled: bool = True
    link_to_code: bool = True  # Create doc-to-code links
    patterns: list[str] = Field(default_factory=lambda: ["*.md", "*.txt", "*.rst"])


class AdaptiveConfig(BaseModel):
    """Auto-detected project configuration."""

    auto_detect: bool = True
    detected_languages: list[str] = Field(default_factory=list)
    is_multi_language: bool = False
    search_strategy: str = "weighted"  # "weighted" or "non_dominated"


class SummarizationConfig(BaseModel):
    """AI-powered commit summarization configuration."""

    enabled: bool = True
    model: str = "google/flan-t5-base"  # 248MB, best quality/speed balance
    max_commits: int = 20  # Max commits to include in summary


class GitDynamicConfig(BaseModel):
    """Configuration for dynamic git memory system."""

    enabled: bool = True
    lookback_commits: int = 200
    coupling_threshold: float = 0.3
    max_files_per_commit: int = 20  # squash dilution guard
    working_window_days: int = 14
    recency_halflife_days: float = 30.0
    base_branch: str = "main"
    cross_branch_enabled: bool = True
    semantic_weight: float = 0.3  # in combined score: git=0.7, semantic=0.3
    narrative_model: str | None = None  # None = auto-select (large on 16GB+, base otherwise)


class RepoIndexOverride(BaseModel):
    """Repo-specific indexing policy override for multi-repo workspaces."""

    index_first: list[str] = Field(default_factory=list)
    dependency_tier: list[str] = Field(default_factory=list)
    lazy_index: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)


class MultiRepoConfig(BaseModel):
    """Workspace-level multi-repo indexing controls."""

    enabled: bool = True
    fanout_concurrency: int = 2
    heavy_repo_chunk_threshold: int = 4000
    heavy_repo_run_dependency_tier: bool = False
    repo_overrides: dict[str, RepoIndexOverride] = Field(default_factory=dict)


class StorageConfig(BaseModel):
    """Storage backend selection configuration."""

    # sqlite-vec is the default for new indexes.
    # auto remains available for compatibility with legacy usearch indexes.
    backend: Literal["auto", "sqlite-vec", "usearch"] = "sqlite-vec"


class Config(BaseModel):
    """Main PCI configuration."""

    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    # New configuration sections
    dependencies: DependencyConfig = Field(default_factory=DependencyConfig)
    documentation: DocumentationConfig = Field(default_factory=DocumentationConfig)
    adaptive: AdaptiveConfig = Field(default_factory=AdaptiveConfig)
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    git_dynamic: GitDynamicConfig = Field(default_factory=GitDynamicConfig)
    multi_repo: MultiRepoConfig = Field(default_factory=MultiRepoConfig)

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load configuration from JSON file."""
        if not path.exists():
            return cls()
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

    def save(self, path: Path) -> None:
        """Save configuration to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.model_dump(), f, indent=2)

    @classmethod
    def get_default_config_path(cls) -> Path:
        """Get default configuration file path."""
        return Path(".sia-code/config.json")
