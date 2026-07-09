"""Shared runtime helpers for CLI and MCP entrypoints."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import Config

console = Console()
err_console = Console(stderr=True)


@dataclass(frozen=True)
class WorkspaceContext:
    """Resolved workspace and index locations for a request."""

    workspace_root: Path
    index_dir: Path


def create_backend(
    index_path: Path,
    config: Config,
    valid_chunks=None,
    suppress_stdout_notices: bool = False,
):
    """Create storage backend with config-based embedding settings."""
    from .storage import factory

    configured_backend = config.storage.backend
    detected_backend = factory.get_backend_type(index_path)
    storage_fields_set = getattr(config.storage, "model_fields_set", set())
    backend_explicitly_set = "backend" in storage_fields_set
    effective_backend = configured_backend

    is_implicit_sqlite_default_on_legacy_usearch = (
        configured_backend == "sqlite-vec"
        and detected_backend == "usearch"
        and not backend_explicitly_set
    )
    if is_implicit_sqlite_default_on_legacy_usearch:
        if not suppress_stdout_notices:
            err_console.print(
                "[yellow]Detected legacy usearch index with implicit storage backend.[/yellow] "
                "Using legacy backend for compatibility."
            )
            err_console.print(
                "[dim]Set 'storage.backend=sqlite-vec' and run 'sia-code index --clean .' "
                "to migrate when ready.[/dim]"
            )
        effective_backend = "auto"

    if (
        effective_backend != "auto"
        and detected_backend != "none"
        and effective_backend != detected_backend
    ):
        console.print(
            "[red]Backend mismatch:[/red] "
            f"config requests '{effective_backend}' but index contains '{detected_backend}'."
        )
        console.print(
            "[dim]Option 1: keep existing index with "
            f"'sia-code config set storage.backend {detected_backend}'[/dim]"
        )
        console.print("[dim]Option 2: migrate by running 'sia-code index --clean .'[/dim]")
        sys.exit(1)

    if effective_backend == "auto" and detected_backend == "usearch":
        if not suppress_stdout_notices:
            err_console.print(
                "[yellow]Detected legacy usearch index.[/yellow] Using it for compatibility."
            )
            err_console.print(
                "[dim]Set 'storage.backend=usearch' to pin legacy mode, "
                "or run 'sia-code index --clean .' to migrate to sqlite-vec.[/dim]"
            )

    return factory.create_backend(
        path=index_path,
        backend_type=effective_backend,
        embedding_enabled=config.embedding.enabled,
        embedding_model=config.embedding.model,
        ndim=config.embedding.dimensions,
        valid_chunks=valid_chunks,
    )


def resolve_git_common_dir(base_dir: Path) -> Path | None:
    """Return git common dir path if available for a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    output = result.stdout.strip()
    if not output:
        return None

    common_dir = Path(output)
    if not common_dir.is_absolute():
        common_dir = (base_dir / common_dir).resolve()
    return common_dir


def is_git_worktree(base_dir: Path) -> bool:
    """Return True when base_dir is inside a git worktree."""
    try:
        git_dir_result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        common_dir_result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False

    git_dir_raw = git_dir_result.stdout.strip()
    common_dir_raw = common_dir_result.stdout.strip()
    if not git_dir_raw or not common_dir_raw:
        return False

    git_dir = Path(git_dir_raw)
    if not git_dir.is_absolute():
        git_dir = (base_dir / git_dir).resolve()

    common_dir = Path(common_dir_raw)
    if not common_dir.is_absolute():
        common_dir = (base_dir / common_dir).resolve()

    return git_dir != common_dir


def get_git_commit_context(base_dir: Path) -> tuple[str | None, datetime | None]:
    """Return the current git commit hash and commit time for a directory."""
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        time_result = subprocess.run(
            ["git", "show", "-s", "--format=%cI", "HEAD"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None, None

    commit_hash = commit_result.stdout.strip() or None
    commit_time_raw = time_result.stdout.strip()
    commit_time = datetime.fromisoformat(commit_time_raw) if commit_time_raw else None
    return commit_hash, commit_time


def get_git_branch_context(base_dir: Path) -> str:
    """Return the current branch name, or a stable workspace label."""
    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return base_dir.resolve().name

    branch = branch_result.stdout.strip()
    if branch and branch != "HEAD":
        return branch
    return base_dir.resolve().name


def build_working_memory_payload(
    backend,
    query: str,
    agent: str | None,
    session_id: str | None,
    base_dir: Path,
) -> dict[str, object]:
    """Build a shared working-memory payload for agent handoff."""
    commit_hash, commit_time = get_git_commit_context(base_dir)
    context = backend.generate_context(query=query)

    return {
        "working_memory": {
            "generated_at": datetime.now().isoformat(),
            "agent": agent,
            "session_id": session_id,
            "query": query,
            "git": {
                "branch": get_git_branch_context(base_dir),
                "commit_hash": commit_hash,
                "commit_time": commit_time.isoformat() if commit_time else None,
            },
            "project_memory": context["project_memory"],
        }
    }


def resolve_index_dir(project_dir: Path | None = None) -> Path:
    """Resolve the index directory, honoring environment overrides."""
    base_dir = project_dir or Path(".")
    override = os.environ.get("SIA_CODE_INDEX_DIR")
    if override:
        override_path = Path(override)
        if override_path.is_absolute():
            return override_path
        return base_dir / override_path

    scope = os.environ.get("SIA_CODE_INDEX_SCOPE")
    if not scope or scope == "auto":
        scope = "shared" if is_git_worktree(base_dir) else "worktree"
    if scope == "shared":
        common_dir = resolve_git_common_dir(base_dir)
        if common_dir is not None:
            return common_dir / "sia-code"

    return base_dir / ".sia-code"


def resolve_workspace_context(
    workspace_root: str | Path | None = None,
    index_dir: str | Path | None = None,
) -> WorkspaceContext:
    """Resolve explicit workspace/index context for CLI-like consumers."""
    if workspace_root is None and index_dir is None:
        raise ValueError("workspace_root or index_dir is required")

    resolved_root = Path(workspace_root).resolve() if workspace_root is not None else None
    resolved_index = None

    if index_dir is not None:
        index_path = Path(index_dir)
        if index_path.is_absolute() or resolved_root is None:
            resolved_index = index_path.resolve()
        else:
            resolved_index = (resolved_root / index_path).resolve()

    if resolved_root is None:
        resolved_root = resolved_index.parent if resolved_index is not None else None
    if resolved_root is None:
        raise ValueError("Unable to resolve workspace_root")

    if resolved_index is None:
        resolved_index = resolve_index_dir(resolved_root)

    return WorkspaceContext(workspace_root=resolved_root, index_dir=resolved_index)
