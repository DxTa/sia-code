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

logger = logging.getLogger(__name__)


@dataclass
class RepoEntry:
    """A registered sub-repo in a multi-repo workspace."""

    name: str
    path: str  # relative to workspace root
    index_dir: str  # relative path to .sia-code/ dir
    indexed_at: str | None = None
    file_count: int = 0


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
                    "indexed_at": r.indexed_at,
                    "file_count": r.file_count,
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
                        indexed_at=r.get("indexed_at"),
                        file_count=r.get("file_count", 0),
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
            )
        )
    return MultiRepoRegistry(
        workspace_root=str(workspace_root),
        created_at=datetime.now(timezone.utc).isoformat(),
        repos=entries,
    )


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


def recommend_repo_timeout_seconds(file_count: int) -> int:
    """Compute per-repo timeout from estimated file count.

    Small repos keep 5m floor. Large repos scale up but stay bounded.
    """
    if file_count <= 0:
        return 300
    # ~0.8s per file plus 60s overhead, bounded 5m..30m
    seconds = int(60 + file_count * 0.8)
    return max(300, min(1800, seconds))
