"""Multi-repo detection and fan-out indexing support.

Detects git repositories as immediate sub-directories and indexes
each independently, then registers them for aggregated search.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pathspec

from ..config import Config, RepoIndexOverride

logger = logging.getLogger(__name__)


def is_model_cached(model_name: str) -> bool:
    """Return True if HuggingFace model appears cached locally."""
    hub_name = model_name.replace('/', '--')
    return (Path.home() / '.cache' / 'huggingface' / 'hub' / f'models--{hub_name}').exists()


@dataclass
class RepoEntry:
    """A registered sub-repo in a multi-repo workspace."""

    name: str
    path: str  # relative to workspace root
    index_dir: str  # relative path to .sia-code/ dir
    profile: str = "general"
    indexed_at: str | None = None
    file_count: int = 0
    estimated_chunks: int = 0
    status: str = "pending"
    last_error: str | None = None


@dataclass
class MultiRepoRegistry:
    """Registry of sub-repos in a multi-repo workspace."""

    repos: list[RepoEntry] = field(default_factory=list)
    created_at: str = ""
    workspace_root: str = ""

    def save(self, registry_path: Path) -> None:
        """Save registry to .sia-code/multi-repo.json."""
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "workspace_root": self.workspace_root,
            "created_at": self.created_at,
            "repos": [
                {
                    "name": r.name,
                    "path": r.path,
                    "index_dir": r.index_dir,
                    "profile": r.profile,
                    "indexed_at": r.indexed_at,
                    "file_count": r.file_count,
                    "estimated_chunks": r.estimated_chunks,
                    "status": r.status,
                    "last_error": r.last_error,
                }
                for r in self.repos
            ],
        }
        registry_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, registry_path: Path) -> MultiRepoRegistry | None:
        """Load registry from file. Returns None if not found."""
        if not registry_path.exists():
            return None
        try:
            data = json.loads(registry_path.read_text())
            return cls(
                workspace_root=data.get("workspace_root", ""),
                created_at=data.get("created_at", ""),
                repos=[
                    RepoEntry(
                        name=r["name"],
                        path=r["path"],
                        index_dir=r["index_dir"],
                        profile=r.get("profile", "general"),
                        indexed_at=r.get("indexed_at"),
                        file_count=r.get("file_count", 0),
                        estimated_chunks=r.get("estimated_chunks", 0),
                        status=r.get("status", "pending"),
                        last_error=r.get("last_error"),
                    )
                    for r in data.get("repos", [])
                ],
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to load multi-repo registry: {e}")
            return None


def detect_sub_repos(directory: Path) -> list[Path]:
    """Detect git repositories as immediate sub-directories.

    Args:
        directory: Parent directory to scan

    Returns:
        Sorted list of paths to sub-directories that are git repos
    """
    repos = []
    try:
        for child in sorted(directory.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                # Check for .git/ directory (is a git repo)
                if (child / ".git").exists():
                    repos.append(child)
    except PermissionError:
        pass
    return repos


def is_multi_repo_workspace(directory: Path) -> bool:
    """Check if directory is a multi-repo workspace (has git sub-repos but is not itself a repo)."""
    # If directory IS a git repo, it's not a multi-repo workspace
    if (directory / ".git").exists():
        return False
    # Check if it has at least 2 sub-repos
    sub_repos = detect_sub_repos(directory)
    return len(sub_repos) >= 2


def get_registry_path(workspace_root: Path) -> Path:
    """Get path to multi-repo registry file."""
    return workspace_root / ".sia-code" / "multi-repo.json"


def get_repo_profile(repo_name: str) -> str:
    """Classify known repo families for indexing-aware policy decisions."""
    if repo_name == "ai.platform.forks.ai-toolkit":
        return "data_science"
    if repo_name == "ai.platform.annotation-suite.cvat":
        return "annotation_platform"
    return "general"


def build_registry(workspace_root: Path, repos: list[Path]) -> MultiRepoRegistry:
    """Build a fresh registry from detected repos."""
    entries = []
    for repo_path in repos:
        rel_path = str(repo_path.relative_to(workspace_root))
        entries.append(
            RepoEntry(
                name=repo_path.name,
                path=rel_path,
                index_dir=f".sia-code/repos/{repo_path.name}",
                profile=get_repo_profile(repo_path.name),
            )
        )
    return MultiRepoRegistry(
        workspace_root=str(workspace_root),
        created_at=datetime.now(timezone.utc).isoformat(),
        repos=entries,
    )


def get_repo_override(config: Config, repo_name: str) -> RepoIndexOverride | None:
    """Return repo-specific indexing override, including built-in defaults."""
    override = config.multi_repo.repo_overrides.get(repo_name)
    if override:
        return override

    # Built-in performance policy for known heavy mixed repo.
    if repo_name == "ai.platform.forks.ai-toolkit":
        return RepoIndexOverride(
            index_first=[
                "toolkit/**",
                "jobs/**",
                "ui/src/**",
                "ui/cron/**",
                "ui/prisma/schema.prisma",
                "run.py",
                "run_modal.py",
                "flux_train_ui.py",
            ],
            dependency_tier=[
                "extensions_built_in/diffusion_models/**/src/**",
            ],
            lazy_index=[
                "config/examples/**",
                "scripts/**",
                "testing/**",
                "docker/**",
                "extensions/example/**",
            ],
            skip=[
                "output/**",
                "assets/**",
                "notebooks/**",
                "ui/public/**",
                "ui/package-lock.json",
                "toolkit/keymaps/**",
                ".github/**",
                ".vscode/**",
            ],
        )

    if repo_name == "ai.platform.annotation-suite.cvat":
        return RepoIndexOverride(
            index_first=[
                "cvat/apps/**",
                "cvat/settings/**",
                "cvat/utils/**",
                "cvat-core/src/**",
                "cvat-canvas/src/**",
                "cvat-canvas3d/src/**",
                "cvat-data/src/**",
                "cvat-ui/src/**",
                "cvat-sdk/cvat_sdk/**",
            ],
            dependency_tier=[
                "serverless/**",
                "utils/**",
                "cvat-cli/**",
            ],
            lazy_index=[
                "tests/**",
                "**/tests/**",
                "site/**",
                "helm-chart/**",
                "ai-models/**",
                "backend_entrypoint.d/**",
                "changelog.d/**",
            ],
            skip=[
                "cvat-ui/dist/**",
                "cvat-sdk/gen/**",
                ".github/**",
                ".vscode/**",
                ".regal/**",
            ],
        )
    return None


def build_repo_config(base_config: Config, repo_name: str) -> Config:
    """Clone config and apply fast indexing policy by default.

    Baseline fast policy applies to any repo, then known profiles add stronger
    tuning and repo-specific include/exclude overrides.
    """
    config = base_config.model_copy(deep=True)
    profile = get_repo_profile(repo_name)
    override = get_repo_override(config, repo_name)

    # Baseline fast policy for ALL repos.
    config.chunking.max_chunk_size = max(config.chunking.max_chunk_size, 1400)
    config.chunking.min_chunk_size = max(config.chunking.min_chunk_size, 80)
    config.chunking.merge_threshold = max(config.chunking.merge_threshold, 0.85)
    config.embedding.granularity = "budget"
    if config.embedding.max_vectors_per_file <= 0:
        config.embedding.max_vectors_per_file = 32
    else:
        config.embedding.max_vectors_per_file = min(config.embedding.max_vectors_per_file, 32)

    if override:
        if override.index_first:
            config.indexing.include_patterns = override.index_first

        merged_excludes = list(config.indexing.exclude_patterns)
        for group in (override.dependency_tier, override.lazy_index, override.skip):
            for pattern in group:
                if pattern not in merged_excludes:
                    merged_excludes.append(pattern)
        config.indexing.exclude_patterns = merged_excludes

    # Profile-specific chunking + embedding for faster first-pass indexing.
    # Heavy data-science / annotation repos benefit from fewer, larger chunks
    # plus a semantic budget and smaller embedding model when cached locally.
    if profile == "data_science":
        config.chunking.max_chunk_size = max(config.chunking.max_chunk_size, 1800)
        config.chunking.min_chunk_size = max(config.chunking.min_chunk_size, 120)
        config.chunking.merge_threshold = max(config.chunking.merge_threshold, 0.9)
        config.embedding.max_vectors_per_file = 24
        if is_model_cached("BAAI/bge-small-en-v1.5"):
            config.embedding.model = "BAAI/bge-small-en-v1.5"
            config.embedding.dimensions = 384
    elif profile == "annotation_platform":
        config.chunking.max_chunk_size = max(config.chunking.max_chunk_size, 2200)
        config.chunking.min_chunk_size = max(config.chunking.min_chunk_size, 140)
        config.chunking.merge_threshold = max(config.chunking.merge_threshold, 0.92)
        config.embedding.max_vectors_per_file = 16
        if is_model_cached("BAAI/bge-small-en-v1.5"):
            config.embedding.model = "BAAI/bge-small-en-v1.5"
            config.embedding.dimensions = 384
    return config


def estimate_indexable_files(directory: Path, config) -> int:
    """Estimate how many files will be indexed for timeout sizing.

    Mirrors IndexingCoordinator._discover_files() but counts only.
    """
    effective_patterns = config.indexing.get_effective_exclude_patterns(directory)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", effective_patterns)

    count = 0
    seen: set[Path] = set()
    max_bytes = config.indexing.max_file_size_mb * 1024 * 1024
    for pattern in config.indexing.include_patterns:
        glob_pattern = pattern if "*" in pattern else f"**/*{pattern}"
        for file_path in directory.rglob(glob_pattern):
            if not file_path.is_file() or file_path in seen:
                continue
            rel_path = file_path.relative_to(directory)
            if spec.match_file(str(rel_path)):
                continue
            try:
                file_size = file_path.stat().st_size
            except OSError:
                continue
            if file_size == 0 or file_size > max_bytes:
                continue
            seen.add(file_path)
            count += 1
    return count


def estimate_chunks(directory: Path, config: Config) -> int:
    """Estimate raw chunk count for visibility / status."""
    return estimate_semantic_vectors(directory, config, raw_chunks_only=True)


def estimate_semantic_vectors(
    directory: Path, config: Config, raw_chunks_only: bool = False
) -> int:
    """Estimate semantic vectors to be embedded for timeout sizing.

    In budget mode, this counts vectors after per-file cap is applied.
    """
    from ..core.types import Language
    from ..parser.chunker import CASTChunker

    effective_patterns = config.indexing.get_effective_exclude_patterns(directory)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", effective_patterns)
    max_bytes = config.indexing.max_file_size_mb * 1024 * 1024
    chunker = CASTChunker(config.chunking)
    total = 0
    seen: set[Path] = set()

    for pattern in config.indexing.include_patterns:
        glob_pattern = pattern if "*" in pattern else f"**/*{pattern}"
        for file_path in directory.rglob(glob_pattern):
            if not file_path.is_file() or file_path in seen:
                continue
            rel_path = file_path.relative_to(directory)
            if spec.match_file(str(rel_path)):
                continue
            try:
                file_size = file_path.stat().st_size
            except OSError:
                continue
            if file_size == 0 or file_size > max_bytes:
                continue
            seen.add(file_path)
            try:
                language = Language.from_extension(file_path.suffix)
                count = len(chunker.chunk_file(file_path, language))
                if raw_chunks_only or config.embedding.granularity != "budget" or config.embedding.max_vectors_per_file <= 0:
                    total += count
                else:
                    total += min(count, config.embedding.max_vectors_per_file)
            except Exception:
                continue
    return total


def recommend_repo_timeout_seconds(file_count: int, estimated_chunks: int = 0) -> int:
    """Compute per-repo timeout.

    Prefer chunk-based sizing because embedding/storage dominates runtime for
    large monolithic repos. Bounded to 5m..45m.
    """
    if estimated_chunks > 0:
        seconds = int(120 + (estimated_chunks / 20.0))
        return max(300, min(2700, seconds))
    if file_count <= 0:
        return 300
    seconds = int(60 + file_count * 0.8)
    return max(300, min(1800, seconds))
