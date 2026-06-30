"""CLI entry point for Sia Code."""

import os
import sys
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import __version__
from .config import Config
from .indexer.coordinator import IndexingCoordinator

console = Console()


def _display_skip_summary(
    console: Console, skipped: dict, verbose: bool = False, max_file_size_mb: float = 5.0
):
    """Display skip summary with optional file details.

    Args:
        console: Rich console for output
        skipped: Dictionary with skip categories
        verbose: Whether to show file names
        max_file_size_mb: Maximum file size threshold for display
    """
    total_skipped = sum(len(v) for v in skipped.values())
    if total_skipped == 0:
        return

    console.print(f"  Files skipped: {total_skipped}")

    categories = [
        ("unsupported_language", "Unsupported language"),
        ("empty_content", "Empty/minimal content"),
        ("parse_errors", "Parse errors"),
        ("too_large", f"Too large (>{max_file_size_mb}MB)"),
    ]

    for key, label in categories:
        items = skipped.get(key, [])
        if not items:
            continue
        console.print(f"    - {label}: {len(items)}")

        if verbose and items:
            # Show first 3 files
            display_items = items[:3]
            for item in display_items:
                if isinstance(item, tuple):
                    path, detail = item
                    # Shorten error messages
                    short_detail = detail if len(detail) < 50 else detail[:47] + "..."
                    console.print(f"        [dim]{Path(path).name}: {short_detail}[/dim]")
                else:
                    console.print(f"        [dim]{Path(item).name}[/dim]")
            if len(items) > 3:
                console.print(f"        [dim]... and {len(items) - 3} more[/dim]")


def setup_logging(verbose: bool = False):
    """Configure logging based on verbosity."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def _parse_alternatives(alternatives: str | None) -> list[dict[str, str]]:
    """Convert comma-separated alternatives into decision records."""
    if not alternatives:
        return []
    return [{"option": alternative.strip()} for alternative in alternatives.split(",")]


def _build_conceptual_links(
    link_files: tuple[str, ...],
    link_symbols: tuple[str, ...],
    link_timeline_refs: tuple[str, ...],
    link_changelog_tags: tuple[str, ...],
) -> list[dict[str, str]]:
    """Build conceptual link payload for decision storage."""
    conceptual_links: list[dict[str, str]] = []
    for link_type, refs in (
        ("file", link_files),
        ("symbol", link_symbols),
        ("timeline", link_timeline_refs),
        ("changelog", link_changelog_tags),
    ):
        conceptual_links.extend({"type": link_type, "ref": ref} for ref in refs)
    return conceptual_links


def create_backend(
    index_path: Path,
    config: Config,
    valid_chunks=None,
    suppress_stdout_notices: bool = False,
):
    """Create storage backend with config-based embedding settings.

    Args:
        index_path: Path to .sia-code directory
        config: Sia Code configuration
        valid_chunks: Optional set of valid chunk IDs for filtering

    Returns:
        Configured StorageBackend instance
    """
    from .storage import factory

    configured_backend = config.storage.backend
    detected_backend = factory.get_backend_type(index_path)
    storage_fields_set = getattr(config.storage, "model_fields_set", set())
    backend_explicitly_set = "backend" in storage_fields_set
    effective_backend = configured_backend

    # Compatibility path: older config files without storage.backend now default to
    # sqlite-vec, but existing repositories may still carry legacy usearch indexes.
    is_implicit_sqlite_default_on_legacy_usearch = (
        configured_backend == "sqlite-vec"
        and detected_backend == "usearch"
        and not backend_explicitly_set
    )
    if is_implicit_sqlite_default_on_legacy_usearch:
        if not suppress_stdout_notices:
            console.print(
                "[yellow]Detected legacy usearch index with implicit storage backend.[/yellow] "
                "Using legacy backend for compatibility."
            )
            console.print(
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
            console.print(
                "[yellow]Detected legacy usearch index.[/yellow] Using it for compatibility."
            )
            console.print(
                "[dim]Set 'storage.backend=usearch' to pin legacy mode, "
                "or run 'sia-code index --clean .' to migrate to sqlite-vec.[/dim]"
            )

    # Use configured backend; auto-detection defaults to sqlite-vec for new indexes.
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
    """Return True when base_dir is inside a git worktree.

    In a normal repo, `--git-dir` and `--git-common-dir` resolve to the same path.
    In a linked worktree, the worktree's git dir differs from the common dir.
    """

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


def require_initialized() -> tuple[Path, Config]:
    """Ensure Sia Code is initialized, return sia_dir and config.

    Returns:
        Tuple of (sia_dir, config)

    Raises:
        SystemExit: If .sia-code directory doesn't exist
    """
    sia_dir = resolve_index_dir()
    if not sia_dir.exists():
        console.print("[red]Error: Sia Code not initialized. Run 'sia-code init' first.[/red]")
        sys.exit(1)
    config = Config.load(sia_dir / "config.json")
    return sia_dir, config


def get_multi_repo_backends(cwd: Path | None = None):
    """Check if in a multi-repo workspace and return all backends.

    Returns:
        list of (repo_name, backend) tuples, or empty list if not multi-repo
    """
    from .storage.multi_repo import MultiRepoRegistry, get_registry_path

    workspace = cwd or Path.cwd()
    registry_path = get_registry_path(workspace)
    registry = MultiRepoRegistry.load(registry_path)

    entries = []
    if registry and registry.repos:
        entries = [(entry.name, workspace / entry.index_dir) for entry in registry.repos]
    else:
        # Fallback: infer from workspace-level repos dir even if registry missing/interrupted
        repos_root = workspace / ".sia-code" / "repos"
        if repos_root.exists():
            entries = [
                (child.name, child)
                for child in sorted(repos_root.iterdir())
                if child.is_dir() and (child / "index.db").exists()
            ]
        if not entries:
            return []

    backends = []
    for repo_name, repo_sia_dir in entries:
        if (repo_sia_dir / "index.db").exists():
            try:
                config = Config.load(repo_sia_dir / "config.json")
                backend = create_backend(repo_sia_dir, config, suppress_stdout_notices=True)
                backend.open_index()
                backends.append((repo_name, backend))
            except Exception:
                pass
    return backends


@click.group()
@click.version_option(version=__version__)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(verbose: bool):
    """Sia Code - Local-first codebase intelligence

    Semantic search, multi-hop research, and 12-language AST support.
    """
    setup_logging(verbose)


@main.command()
@click.option("--path", type=click.Path(), default=".", help="Directory to initialize")
@click.option("--dry-run", is_flag=True, help="Preview project analysis without creating index")
def init(path: str, dry_run: bool):
    """Initialize Sia Code in the current directory."""
    from .indexer.project_analyzer import ProjectAnalyzer

    project_dir = Path(path)
    sia_dir = resolve_index_dir(project_dir)

    if sia_dir.exists() and not dry_run:
        console.print(f"[yellow]Sia Code already initialized at {sia_dir}[/yellow]")
        return

    # Run project analysis
    analyzer = ProjectAnalyzer(project_dir)
    profile = analyzer.analyze()

    # Display analysis results
    console.print("\n[bold]Project Analysis[/bold]")
    console.print(f"  Languages: {', '.join(profile.primary_languages) or 'none detected'}")
    console.print(f"  Multi-language: {'yes' if profile.is_multi_language else 'no'}")
    console.print(f"  Has dependencies: {'yes' if profile.has_dependencies else 'no'}")
    console.print(f"  Has documentation: {'yes' if profile.has_documentation else 'no'}")
    console.print(f"  Recommended strategy: {profile.recommended_strategy}")

    if dry_run:
        console.print("\n[dim]Language detections:[/dim]")
        for detection in profile.detections[:5]:
            console.print(f"  {detection.language}: {detection.confidence:.0%} confidence")
            console.print(f"    Evidence: {', '.join(detection.evidence[:3])}")
        console.print("\n[yellow]Dry run complete. No index created.[/yellow]")
        return

    # Create index directory
    sia_dir.mkdir(parents=True, exist_ok=True)
    (sia_dir / "cache").mkdir(exist_ok=True)

    # Create config with auto-detected settings
    config = Config()

    # Apply auto-detected tier boost settings
    config.search.tier_boost = profile.tier_boost
    config.search.include_dependencies = profile.has_dependencies

    config.save(sia_dir / "config.json")

    # Create empty index with embedding support
    backend = create_backend(sia_dir, config)
    backend.create_index()
    backend.close()  # Persist vector index to disk

    console.print(f"\n[green]✓[/green] Initialized Sia Code at {sia_dir}")
    console.print("[dim]Next: sia-code index [path][/dim]")


@main.command()
@click.argument("path", type=click.Path(exists=True), default=".")
@click.option("--update", is_flag=True, help="Re-index changed files only")
@click.option(
    "--clean", is_flag=True, help="Delete existing index and cache, then rebuild from scratch"
)
@click.option(
    "--parallel/--no-parallel",
    default=False,
    help="Use parallel processing (experimental, best for 100+ files)",
)
@click.option(
    "--workers", type=int, default=None, help="Number of worker processes (default: CPU count)"
)
@click.option(
    "--watch", is_flag=True, help="Watch for file changes and auto-reindex (Ctrl+C to stop)"
)
@click.option(
    "--debounce",
    type=float,
    default=2.0,
    help="Seconds to wait before reindexing after changes (default: 2.0)",
)
@click.option(
    "--no-git-sync",
    is_flag=True,
    help="Skip git history sync (imports timeline events and changelogs)",
)
@click.pass_context
def index(
    ctx: click.Context,
    path: str,
    update: bool,
    clean: bool,
    parallel: bool,
    workers: int | None,
    watch: bool,
    debounce: float,
    no_git_sync: bool,
):
    """Index codebase for search."""
    # Get verbose flag from parent context
    verbose = ctx.parent.params.get("verbose", False) if ctx.parent else False

    sia_dir, config = require_initialized()

    # Handle --clean flag
    if clean:
        console.print("[yellow]Cleaning existing index and cache...[/yellow]")

        # Remove index file
        index_path = sia_dir / "index.db"
        if index_path.exists():
            index_path.unlink()
            console.print(f"  [dim]✓ Deleted: {index_path}[/dim]")

        # Remove cache file
        cache_path = sia_dir / "cache" / "file_hashes.json"
        if cache_path.exists():
            cache_path.unlink()
            console.print(f"  [dim]✓ Deleted: {cache_path}[/dim]")

        # Remove legacy usearch vector file to allow backend migration on clean rebuild
        usearch_path = sia_dir / "vectors.usearch"
        if usearch_path.exists():
            usearch_path.unlink()
            console.print(f"  [dim]✓ Deleted: {usearch_path}[/dim]")

        console.print("[green]Clean complete. Performing full reindex...[/green]\n")

        # Force full reindex by ensuring update=False
        update = False

        # Re-detect backend after cleanup (auto now defaults to sqlite-vec)
        backend = create_backend(sia_dir, config)
        # Recreate index
        backend.create_index()
    else:
        backend = create_backend(sia_dir, config)
        # Open existing index
        backend.open_index()

    # Create coordinator
    coordinator = IndexingCoordinator(config, backend)

    # Index directory
    directory = Path(path).resolve()

    # Auto-detect multi-repo workspace (multiple git sub-repos)
    from .storage.multi_repo import (
        build_registry,
        detect_sub_repos,
        estimate_indexable_files,
        get_registry_path,
        is_multi_repo_workspace,
        recommend_repo_timeout_seconds,
    )

    if is_multi_repo_workspace(directory):
        sub_repos = detect_sub_repos(directory)
        console.print(
            f"[cyan]Detected multi-repo workspace with {len(sub_repos)} repos[/cyan]"
        )
        for repo in sub_repos:
            console.print(f"  [dim]• {repo.name}[/dim]")
        console.print()

        # Fan-out: index each repo independently
        # All indexes stored under workspace .sia-code/repos/<name>/ (no pollution in sub-repos)
        registry = build_registry(directory, sub_repos)
        all_stats = []
        workspace_sia = directory / ".sia-code"
        registry_path = get_registry_path(directory)
        registry.save(registry_path)

        # Pre-warm embed daemon (shared across all repos)
        try:
            backend_tmp = create_backend(
                workspace_sia, config, suppress_stdout_notices=True
            )
            backend_tmp._get_embedder()
            console.print("[dim]Embedding daemon ready[/dim]")
        except Exception:
            pass

        import time as _time
        import subprocess as _sp
        import json as _json

        for i, repo_path in enumerate(sub_repos, 1):
            console.print(
                f"[cyan][{i}/{len(sub_repos)}] Indexing {repo_path.name}...[/cyan]"
            )
            _t0 = _time.monotonic()

            # Store index under workspace: .sia-code/repos/<name>/
            repo_index_dir = workspace_sia / "repos" / repo_path.name
            repo_index_dir.mkdir(parents=True, exist_ok=True)

            # Write config for this sub-index
            repo_config_path = repo_index_dir / "config.json"
            if not repo_config_path.exists():
                config.save(repo_config_path)

            # Update registry to point to workspace-level index dir
            for entry in registry.repos:
                if entry.name == repo_path.name:
                    entry.index_dir = str(
                        (workspace_sia / "repos" / repo_path.name).relative_to(directory)
                    )
                    break

            # Estimate size for timeout sizing and visibility
            estimated_files = estimate_indexable_files(repo_path, config)
            repo_timeout = recommend_repo_timeout_seconds(estimated_files)
            console.print(
                f"    [dim]~{estimated_files} files, timeout {repo_timeout}s[/dim]"
            )

            # Index in subprocess (crash-isolated, dynamic timeout per repo)
            clean_flag = "True" if clean else "False"
            index_script = (
                "import sys, json, os\n"
                "os.environ.setdefault('HF_HUB_OFFLINE', '1')\n"
                "from pathlib import Path\n"
                "from sia_code.config import Config\n"
                "from sia_code.cli import create_backend\n"
                "from sia_code.indexer.coordinator import IndexingCoordinator\n"
                f"sia_dir = Path({str(repo_index_dir)!r})\n"
                f"repo_dir = Path({str(repo_path)!r})\n"
                f"do_clean = {clean_flag}\n"
                "config = Config.load(sia_dir / 'config.json')\n"
                "backend = create_backend(sia_dir, config, suppress_stdout_notices=True)\n"
                "if do_clean:\n"
                "    idx = sia_dir / 'index.db'\n"
                "    if idx.exists(): idx.unlink()\n"
                "    backend.create_index()\n"
                "else:\n"
                "    try:\n"
                "        backend.open_index()\n"
                "    except Exception:\n"
                "        backend.create_index()\n"
                "coord = IndexingCoordinator(config, backend)\n"
                "stats = coord.index_directory(repo_dir)\n"
                "backend.close()\n"
                "print(json.dumps(stats))\n"
            )
            try:
                result = _sp.run(
                    [sys.executable, "-c", index_script],
                    capture_output=True,
                    text=True,
                    timeout=repo_timeout,
                )
                _elapsed = _time.monotonic() - _t0
                if result.returncode == 0:
                    stdout_lines = result.stdout.strip().splitlines()
                    stats_line = stdout_lines[-1] if stdout_lines else '{}'
                    try:
                        stats = _json.loads(stats_line)
                    except _json.JSONDecodeError:
                        stats = {"indexed_files": 0, "total_chunks": 0}
                    all_stats.append(stats)
                    for entry in registry.repos:
                        if entry.name == repo_path.name:
                            entry.indexed_at = datetime.now(timezone.utc).isoformat()
                            entry.file_count = stats.get("indexed_files", 0)
                            break
                    registry.save(registry_path)
                    console.print(
                        f"    [green]✓[/green] {stats.get('indexed_files', 0)} files, "
                        f"{stats.get('total_chunks', 0)} chunks "
                        f"[dim]({_elapsed:.1f}s)[/dim]"
                    )
                else:
                    err_lines = result.stderr.strip().splitlines()
                    err_msg = err_lines[-1][:80] if err_lines else "unknown"
                    console.print(
                        f"    [red]✗ Failed ({_elapsed:.1f}s): {err_msg}[/red]"
                    )
            except _sp.TimeoutExpired:
                _elapsed = _time.monotonic() - _t0
                console.print(
                    f"    [yellow]⚠ Timeout ({_elapsed:.0f}s/{repo_timeout}s) — skipped[/yellow]"
                )
            except Exception as e:
                console.print(f"    [red]✗ Failed: {e}[/red]")

        # Save registry
        registry.save(registry_path)
        console.print("\n[green]✓ Multi-repo indexing complete[/green]")
        console.print(f"  Registry: {registry_path}")
        total_files = sum(s.get("indexed_files", 0) for s in all_stats)
        total_chunks = sum(s.get("total_chunks", 0) for s in all_stats)
        console.print(f"  Total: {total_files} files, {total_chunks} chunks across {len(all_stats)} repos")
        return

    # --- Single-repo indexing (original flow) ---

    if update:
        console.print(f"[cyan]Incremental indexing {directory}...[/cyan]")
        console.print("[dim]Checking for changes...[/dim]")
    else:
        console.print(f"[cyan]Indexing {directory}...[/cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Discovering files...", total=None)

        def update_progress(stage: str, current: int, total: int, desc: str):
            """Update progress display based on indexing stage."""
            if stage == "discovering":
                progress.update(task, description="Discovering files...")
            elif stage == "checking":
                # For incremental mode, show checking phase
                if progress.tasks[task].total is None:
                    progress.update(task, total=total, completed=0)
                progress.update(task, completed=current, description=f"Checking: {desc}")
            elif stage == "indexing":
                # Switch to progress bar when indexing starts
                if progress.tasks[task].total is None:
                    progress.update(task, total=total, completed=0)
                progress.update(task, completed=current, description=f"Indexing: {desc}")

        try:
            if update:
                # Incremental indexing with hash cache and chunk index (v2.0)
                from .indexer.hash_cache import HashCache
                from .indexer.chunk_index import ChunkIndex

                cache = HashCache(sia_dir / "cache" / "file_hashes.json")
                chunk_index = ChunkIndex(sia_dir / "chunk_index.json")

                stats = coordinator.index_directory_incremental_v2(
                    directory, cache, chunk_index, progress_callback=update_progress
                )

                console.print("\n[green]✓ Incremental indexing complete (v2.0)[/green]")
                console.print(f"  Changed files: {stats['changed_files']}")
                console.print(f"  Unchanged files: {stats['skipped_files']}")
                console.print(f"  Indexed files: {stats['indexed_files']}/{stats['total_files']}")
                _display_skip_summary(
                    console,
                    stats.get("skipped", {}),
                    verbose=verbose,
                    max_file_size_mb=config.indexing.max_file_size_mb,
                )
                console.print(f"  Total chunks: {stats['total_chunks']}")

                # Show staleness info
                summary = chunk_index.get_staleness_summary()
                if summary.total_chunks > 0:
                    console.print(
                        f"  Index health: {summary.valid_chunks:,} valid, "
                        f"{summary.stale_chunks:,} stale ({summary.staleness_ratio:.1%})"
                    )
            else:
                # Full indexing (parallel by default)
                if parallel:
                    stats = coordinator.index_directory_parallel(
                        directory, max_workers=workers, progress_callback=update_progress
                    )
                else:
                    stats = coordinator.index_directory(
                        directory, progress_callback=update_progress
                    )
                progress.update(task, completed=True)

                console.print("\n[green]✓ Indexing complete[/green]")
                console.print(f"  Files indexed: {stats['indexed_files']}")
                _display_skip_summary(
                    console,
                    stats.get("skipped", {}),
                    verbose=verbose,
                    max_file_size_mb=config.indexing.max_file_size_mb,
                )
                console.print(f"  Total chunks: {stats['total_chunks']}")

            # Show performance metrics if available
            if stats.get("metrics"):
                m = stats["metrics"]
                console.print("\n[dim]Performance:[/dim]")
                console.print(f"  Duration: {m['duration_seconds']}s")
                console.print(
                    f"  Throughput: {m['files_per_second']:.1f} files/s, {m['chunks_per_second']:.1f} chunks/s"
                )
                console.print(f"  Processed: {m['mb_per_second']:.2f} MB/s")

            if stats["errors"]:
                console.print("\n[yellow]Warnings:[/yellow]")
                for error in stats["errors"][:5]:  # Show first 5 errors
                    console.print(f"  {error}")
                if len(stats["errors"]) > 5:
                    console.print(f"  ... and {len(stats['errors']) - 5} more")

            # Close backend to persist vectors to disk
            backend.close()

            # Auto-sync git history (unless disabled or in watch mode)
            if not no_git_sync and not watch:
                try:
                    from .memory.git_sync import GitSyncService

                    # Reopen backend for git sync
                    backend.open_index()
                    sync_service = GitSyncService(backend, Path(path), config=config)
                    sync_stats = sync_service.sync(since="HEAD~100", limit=50)

                    # Display brief sync summary
                    if sync_stats["total_added"] > 0:
                        console.print(
                            f"\n[dim]Git sync: +{sync_stats['changelogs_added']} changelogs, "
                            f"+{sync_stats['timeline_added']} timeline events[/dim]"
                        )
                    backend.close()
                except Exception as e:
                    console.print(f"[dim][yellow]Git sync skipped: {e}[/yellow][/dim]")

        except Exception as e:
            console.print(f"[red]Error during indexing: {e}[/red]")
            sys.exit(1)

    # Watch mode
    if watch:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            import time
            from threading import Timer
        except ImportError:
            console.print("[red]Error: watchdog not installed[/red]")
            console.print("[dim]Install with: pip install watchdog>=3.0[/dim]")
            sys.exit(1)

        class CodeFileHandler(FileSystemEventHandler):
            def __init__(self, debounce_seconds):
                self.debounce_seconds = debounce_seconds
                self.timer = None
                self.pending_changes = set()

            def on_any_event(self, event):
                if event.is_directory:
                    return

                # Only track relevant file changes
                if any(
                    event.src_path.endswith(ext)
                    for ext in [
                        ".py",
                        ".js",
                        ".ts",
                        ".go",
                        ".rs",
                        ".java",
                        ".c",
                        ".cpp",
                        ".cs",
                        ".rb",
                        ".php",
                    ]
                ):
                    self.pending_changes.add(event.src_path)

                    # Reset timer
                    if self.timer:
                        self.timer.cancel()

                    self.timer = Timer(self.debounce_seconds, self.reindex)
                    self.timer.start()

            def reindex(self):
                if not self.pending_changes:
                    return

                console.print(
                    f"\n[yellow]File changes detected: {len(self.pending_changes)} files[/yellow]"
                )
                console.print("[dim]Re-indexing...[/dim]")

                try:
                    # Perform incremental reindex using v2
                    from .indexer.hash_cache import HashCache
                    from .indexer.chunk_index import ChunkIndex

                    cache_path = sia_dir / "cache" / "file_hashes.json"
                    cache = HashCache(cache_path)

                    chunk_index_path = sia_dir / "chunk_index.json"
                    chunk_index = ChunkIndex(chunk_index_path)
                    chunk_index.load()

                    coordinator = IndexingCoordinator(backend=backend, config=config)
                    stats = coordinator.index_directory_incremental_v2(
                        Path(path), cache, chunk_index, progress_callback=None
                    )

                    # Save updated chunk index
                    chunk_index.save()

                    console.print(
                        f"[green]✓[/green] Re-indexed {stats['files_indexed']} files, {stats['chunks_indexed']} chunks"
                    )

                except Exception as e:
                    console.print(f"[red]Error during re-indexing: {e}[/red]")
                finally:
                    self.pending_changes.clear()

        console.print("\n[bold cyan]Watch Mode Active[/bold cyan]")
        console.print(f"[dim]Monitoring: {Path(path).absolute()}[/dim]")
        console.print(f"[dim]Debounce: {debounce}s[/dim]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        event_handler = CodeFileHandler(debounce)
        observer = Observer()
        observer.schedule(event_handler, path, recursive=True)
        observer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping watch mode...[/yellow]")
            observer.stop()
            observer.join()
            console.print("[green]✓[/green] Watch mode stopped")
            sys.exit(0)


@main.command()
@click.argument("query")
@click.option("--regex", is_flag=True, help="Use regex/lexical search instead of hybrid")
@click.option("--semantic-only", is_flag=True, help="Use semantic-only search (no BM25)")
@click.option("-k", "--limit", type=int, default=10, help="Number of results")
@click.option("--no-filter", is_flag=True, help="Disable stale chunk filtering")
@click.option("--no-deps", is_flag=True, help="Exclude dependency code from results")
@click.option("--deps-only", is_flag=True, help="Show only dependency code (no project code)")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "table", "csv"]),
    default="text",
    help="Output format (default: text)",
)
@click.option("-o", "--output", type=click.Path(), help="Save results to file instead of stdout")
def search(
    query: str,
    regex: bool,
    semantic_only: bool,
    limit: int,
    no_filter: bool,
    no_deps: bool,
    deps_only: bool,
    output_format: str,
    output: str | None,
):
    """Search the codebase (default: hybrid BM25 + semantic)."""
    from .indexer.chunk_index import ChunkIndex

    sia_dir, config = require_initialized()

    # Load chunk index for filtering (if available and not disabled)
    valid_chunks = None
    if not no_filter:
        chunk_index_path = sia_dir / "chunk_index.json"
        if chunk_index_path.exists():
            try:
                chunk_index = ChunkIndex(chunk_index_path)
                valid_chunks = chunk_index.get_valid_chunks()
            except Exception:
                pass  # Silently fall back to no filtering

    # Handle mutually exclusive dependency flags
    if no_deps and deps_only:
        console.print("[red]Error: --no-deps and --deps-only are mutually exclusive[/red]")
        sys.exit(1)

    # Multi-repo: if in workspace with registry, search all sub-repos
    multi_backends = get_multi_repo_backends()
    _multi_repo_mode = bool(multi_backends)
    if _multi_repo_mode:
        console.print(
            f"[dim]Multi-repo: searching across {len(multi_backends)} repos[/dim]"
        )
        # Use primary backend for config-based settings; actual search aggregates all
        backend = multi_backends[0][1]
    else:
        backend = create_backend(sia_dir, config, valid_chunks=valid_chunks)
        backend.open_index()

    # Determine dependency filtering
    # Default: include deps (from config or True)
    # --no-deps: exclude deps
    # --deps-only: show only deps (include_deps=True, then filter results)
    include_deps = not no_deps  # Exclude deps if --no-deps is set
    tier_boost = config.search.tier_boost if hasattr(config.search, "tier_boost") else None

    # Determine search mode (NEW: hybrid by default)
    if regex:
        mode = "lexical"
    elif semantic_only:
        mode = "semantic"
    else:
        mode = "hybrid"  # NEW DEFAULT: BM25 + semantic

    filter_status = "" if no_filter or not valid_chunks else " [filtered]"
    deps_status = " [no-deps]" if no_deps else " [deps-only]" if deps_only else ""

    # Suppress progress messages for structured output formats
    if output_format not in ("json", "csv"):
        console.print(f"[dim]Searching ({mode}{filter_status}{deps_status})...[/dim]")

    # Execute search based on mode
    def _search_one_backend(be):
        if regex:
            return be.search_lexical(
                query, k=limit, include_deps=include_deps, tier_boost=tier_boost
            )
        elif semantic_only:
            return be.search_semantic(
                query, k=limit, include_deps=include_deps, tier_boost=tier_boost
            )
        else:
            return be.search_hybrid(
                query,
                k=limit,
                vector_weight=config.search.vector_weight,
                include_deps=include_deps,
                tier_boost=tier_boost,
            )

    if _multi_repo_mode:
        # Aggregate results from all repos
        all_results = []
        for repo_name, be in multi_backends:
            try:
                repo_results = _search_one_backend(be)
                # Prefix file paths with repo name for disambiguation
                for r in repo_results:
                    if hasattr(r, "chunk") and hasattr(r.chunk, "file_path"):
                        r.chunk.file_path = f"{repo_name}/{r.chunk.file_path}"
                all_results.extend(repo_results)
            except Exception:
                pass
        # Sort by score descending, take top `limit`
        results = sorted(all_results, key=lambda r: r.score, reverse=True)[:limit]
    else:
        results = _search_one_backend(backend)

    # Filter for --deps-only after search
    if deps_only and results:
        results = [r for r in results if r.chunk.metadata.get("tier") == "dependency"]

    if not results:
        # Handle empty results based on output format
        if output_format == "json":
            import json

            empty_output = {"query": query, "mode": mode, "results": []}
            print(json.dumps(empty_output, indent=2))
        elif output_format == "csv":
            # CSV header only for empty results
            print("File,Start Line,End Line,Symbol,Score,Preview")
        else:
            console.print("[yellow]No results found[/yellow]")
        return

    # Format results based on output_format
    if output_format == "json":
        import json

        output_data = {"query": query, "mode": mode, "results": [r.to_dict() for r in results]}
        formatted_output = json.dumps(output_data, indent=2)
    elif output_format == "csv":
        import csv
        import io

        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        # Write header
        csv_writer.writerow(["File", "Start Line", "End Line", "Symbol", "Score", "Preview"])
        # Write rows
        for result in results:
            chunk = result.chunk
            preview = (result.snippet or chunk.code)[:100].replace("\n", " ").replace("\r", "")
            csv_writer.writerow(
                [
                    chunk.file_path,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.symbol,
                    f"{result.score:.3f}",
                    preview,
                ]
            )
        formatted_output = csv_buffer.getvalue()
    elif output_format == "table":
        table = Table(title=f"Search Results: {query}")
        table.add_column("File", style="cyan")
        table.add_column("Line", style="dim")
        table.add_column("Symbol", style="bold")
        table.add_column("Score", justify="right")
        table.add_column("Preview", style="dim")

        for result in results:
            chunk = result.chunk
            preview = (result.snippet or chunk.code)[:80].replace("\n", " ")
            table.add_row(
                str(chunk.file_path),
                f"{chunk.start_line}-{chunk.end_line}",
                chunk.symbol,
                f"{result.score:.3f}",
                preview + "..." if len(preview) == 80 else preview,
            )
        formatted_output = table
    else:  # text format (default)
        formatted_output = None
        for i, result in enumerate(results, 1):
            chunk = result.chunk
            console.print(f"\n[bold cyan]{i}. {chunk.symbol}[/bold cyan]")
            console.print(f"[dim]{chunk.file_path}:{chunk.start_line}-{chunk.end_line}[/dim]")
            console.print(f"Score: {result.score:.3f}")
            if result.snippet:
                console.print(f"\n{result.snippet}\n")

    # Save to file or print to console
    if output:
        try:
            output_path = Path(output)
            if output_format == "json" or output_format == "csv":
                assert isinstance(formatted_output, str)
                output_path.write_text(formatted_output)
            elif output_format == "table":
                from rich.console import Console as FileConsole

                with open(output_path, "w") as f:
                    file_console = FileConsole(file=f, width=120)
                    file_console.print(formatted_output)
            else:  # text format
                # Re-format as plain text for file output
                lines = []
                for i, result in enumerate(results, 1):
                    chunk = result.chunk
                    lines.append(f"{i}. {chunk.symbol}")
                    lines.append(f"   {chunk.file_path}:{chunk.start_line}-{chunk.end_line}")
                    lines.append(f"   Score: {result.score:.3f}")
                    if result.snippet:
                        lines.append(f"\n{result.snippet}\n")
                output_path.write_text("\n".join(lines))
            console.print(f"[green]✓[/green] Results saved to {output}")
        except Exception as e:
            console.print(f"[red]Error saving to file: {e}[/red]")
            sys.exit(1)
    elif formatted_output is not None:
        if output_format == "json" or output_format == "csv":
            # Use print() for JSON/CSV to avoid rich console formatting
            print(formatted_output)
        else:  # table
            console.print(formatted_output)


@main.command()
@click.option("--regex", is_flag=True, help="Use regex/lexical search instead of semantic")
@click.option("-k", "--limit", type=int, default=10, help="Number of results per query")
def interactive(regex: bool, limit: int):
    """Interactive search mode with live query and result navigation.

    Features:
    - Live search as you type
    - Navigate results with arrow keys
    - Preview code chunks
    - Export results to file
    - Press Ctrl+C or Ctrl+D to exit
    """
    try:
        from prompt_toolkit import PromptSession
    except ImportError:
        console.print("[red]Error: prompt-toolkit not installed[/red]")
        console.print("[dim]Install with: pip install prompt-toolkit>=3.0[/dim]")
        sys.exit(1)

    from .indexer.chunk_index import ChunkIndex

    sia_dir, config = require_initialized()

    # Load chunk index for filtering
    valid_chunks = None
    chunk_index_path = sia_dir / "chunk_index.json"
    if chunk_index_path.exists():
        try:
            chunk_index = ChunkIndex(chunk_index_path)
            valid_chunks = chunk_index.get_valid_chunks()
        except Exception:
            pass

    backend = create_backend(sia_dir, config, valid_chunks=valid_chunks)
    backend.open_index()

    mode = "lexical" if regex else "semantic"
    console.print(f"[bold cyan]Sia Code Interactive Search[/bold cyan] ({mode} mode)")
    console.print("[dim]Type your query and press Enter. Ctrl+C or Ctrl+D to exit.[/dim]\n")

    session = PromptSession()
    current_results = []
    current_query = ""

    while True:
        try:
            # Get query from user
            query = session.prompt("Search> ")
            if not query.strip():
                continue

            current_query = query
            console.print(f"[dim]Searching for: {query}[/dim]")

            # Perform search
            if regex:
                results = backend.search_lexical(query, k=limit)
            else:
                results = backend.search_semantic(query, k=limit)

            current_results = results

            if not results:
                console.print("[yellow]No results found[/yellow]\n")
                continue

            # Display results
            console.print(f"\n[bold]Found {len(results)} results:[/bold]\n")
            for i, result in enumerate(results, 1):
                chunk = result.chunk
                console.print(f"[cyan]{i}.[/cyan] {chunk.symbol}")
                console.print(
                    f"   [dim]{chunk.file_path}:{chunk.start_line}-{chunk.end_line}[/dim]"
                )
                console.print(f"   Score: {result.score:.3f}")

                # Show preview
                if result.snippet:
                    preview = result.snippet[:150]
                    console.print(
                        f"   [dim]{preview}{'...' if len(result.snippet) > 150 else ''}[/dim]"
                    )
                console.print()

            # Ask what to do next
            action = session.prompt(
                "\nAction: [v]iew result, [e]xport to file, [n]ew query, [q]uit > "
            )

            if action.lower() == "q":
                console.print("[green]Goodbye![/green]")
                break
            elif action.lower() == "v":
                try:
                    idx = int(
                        session.prompt("Enter result number to view (1-{}): ".format(len(results)))
                    )
                    if 1 <= idx <= len(results):
                        result = results[idx - 1]
                        chunk = result.chunk
                        console.print(f"\n[bold cyan]{chunk.symbol}[/bold cyan]")
                        console.print(
                            f"[dim]{chunk.file_path}:{chunk.start_line}-{chunk.end_line}[/dim]\n"
                        )
                        console.print(chunk.code)
                        console.print()
                    else:
                        console.print("[yellow]Invalid result number[/yellow]\n")
                except (ValueError, EOFError):
                    console.print("[yellow]Invalid input[/yellow]\n")
            elif action.lower() == "e":
                try:
                    filename = session.prompt("Export filename (e.g., results.json): ")
                    if filename:
                        import json

                        output_data = {
                            "query": current_query,
                            "mode": mode,
                            "results": [r.to_dict() for r in current_results],
                        }
                        Path(filename).write_text(json.dumps(output_data, indent=2))
                        console.print(f"[green]✓[/green] Results exported to {filename}\n")
                except (EOFError, KeyboardInterrupt):
                    console.print("[yellow]Export cancelled[/yellow]\n")
            elif action.lower() == "n":
                continue
            else:
                console.print("[yellow]Unknown action[/yellow]\n")

        except (EOFError, KeyboardInterrupt):
            console.print("\n[green]Goodbye![/green]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]\n")


@main.command()
@click.argument("question")
@click.option("--hops", type=int, default=2, help="Maximum relationship hops")
@click.option("--graph", is_flag=True, help="Show call graph")
@click.option("-k", "--limit", type=int, default=5, help="Results per hop")
@click.option("--no-filter", is_flag=True, help="Disable stale chunk filtering")
def research(question: str, hops: int, graph: bool, limit: int, no_filter: bool):
    """Multi-hop code research for architectural questions.

    Automatically discovers code relationships and builds a complete picture.

    Examples:
        sia-code research "How does authentication work?"
        sia-code research "What calls the indexer?" --graph
        sia-code research "How is configuration loaded?" --hops 3
    """
    from .indexer.chunk_index import ChunkIndex
    from .search.multi_hop import MultiHopSearchStrategy

    sia_dir, config = require_initialized()

    # Load chunk index for filtering (if available and not disabled)
    valid_chunks = None
    if not no_filter:
        chunk_index_path = sia_dir / "chunk_index.json"
        if chunk_index_path.exists():
            try:
                chunk_index = ChunkIndex(chunk_index_path)
                valid_chunks = chunk_index.get_valid_chunks()
            except Exception:
                pass  # Silently fall back to no filtering

    backend = create_backend(sia_dir, config, valid_chunks=valid_chunks)
    backend.open_index()

    strategy = MultiHopSearchStrategy(backend, max_hops=hops)

    console.print(f"[dim]Researching: {question}[/dim]")
    console.print(f"[dim]Max hops: {hops}, Results per hop: {limit}[/dim]\n")

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("Analyzing code relationships...", total=None)
        result = strategy.research(question, max_results_per_hop=limit)
        progress.update(task, completed=True)

    # Display results summary
    console.print("\n[bold green]✓ Research Complete[/bold green]")
    console.print(f"  Found: {len(result.chunks)} related code chunks")
    console.print(f"  Relationships: {len(result.relationships)}")
    console.print(f"  Entities discovered: {result.total_entities_found}")
    console.print(f"  Hops executed: {result.hops_executed}/{hops}\n")

    if not result.chunks:
        console.print("[yellow]No relevant code found. Try rephrasing your question.[/yellow]")
        return

    # Display top chunks
    console.print("[bold]Top Related Code:[/bold]\n")
    for i, chunk in enumerate(result.chunks[:10], 1):
        console.print(f"{i}. [cyan]{chunk.symbol}[/cyan]")
        console.print(f"   {chunk.file_path}:{chunk.start_line}-{chunk.end_line}")
        if i <= 3:  # Show code preview for top 3
            preview = chunk.code[:200].replace("\n", "\n   ")
            console.print(f"   [dim]{preview}...[/dim]")
        console.print()

    # Show call graph if requested
    if graph and result.relationships:
        call_graph = strategy.build_call_graph(result.relationships)
        entry_points = strategy.get_entry_points(result.relationships)

        console.print("\n[bold]Call Graph:[/bold]\n")

        if entry_points:
            console.print("[dim]Entry points:[/dim]")
            for entry in entry_points[:5]:
                console.print(f"  [green]→ {entry}[/green]")
            console.print()

        console.print("[dim]Relationships:[/dim]")
        for entity, targets in list(call_graph.items())[:15]:
            console.print(f"  {entity}")
            for target in targets[:3]:
                rel_type = target["type"].replace("_", " ")
                console.print(f"    [dim]{rel_type}[/dim] → {target['target']}")

        if len(call_graph) > 15:
            console.print(f"\n  [dim]... and {len(call_graph) - 15} more entities[/dim]")


@main.command()
def status():
    """Show index statistics and health."""
    import datetime
    import json
    from .indexer.chunk_index import ChunkIndex

    sia_dir, config = require_initialized()

    # Multi-repo aggregate status
    multi_backends = get_multi_repo_backends()
    if multi_backends:
        total_files = 0
        total_chunks = 0
        repo_rows = []
        for repo_name, backend in multi_backends:
            try:
                stats = backend.get_stats()
                total_files += stats.total_files
                total_chunks += stats.total_chunks
                repo_rows.append((repo_name, stats.total_files, stats.total_chunks))
            except Exception:
                pass

        table = Table(title="Sia Code Index Status (Multi-Repo)")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Workspace Index Path", str(sia_dir))
        table.add_row("Registered Repos", f"{len(multi_backends):,}")
        table.add_row("Total Files", f"{total_files:,}")
        table.add_row("Total Chunks", f"{total_chunks:,}")
        console.print(table)

        repo_table = Table(title="Per-Repo Status")
        repo_table.add_column("Repo", style="cyan")
        repo_table.add_column("Files", justify="right")
        repo_table.add_column("Chunks", justify="right")
        for repo_name, files_n, chunks_n in repo_rows:
            repo_table.add_row(repo_name, f"{files_n:,}", f"{chunks_n:,}")
        console.print(repo_table)
        return

    backend = create_backend(sia_dir, config)
    backend.open_index()
    stats = backend.get_stats()

    table = Table(title="Sia Code Index Status")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Index Path", str(sia_dir))
    table.add_row("Total Files", f"{stats.total_files:,}")
    table.add_row("Total Chunks", f"{stats.total_chunks:,}")

    # Cache statistics
    cache_path = sia_dir / "cache" / "file_hashes.json"
    if cache_path.exists():
        try:
            cache_data = json.loads(cache_path.read_text())
            cache_size = cache_path.stat().st_size
            table.add_row("", "")  # Separator
            table.add_row("Cached Files", str(len(cache_data)))
            table.add_row("Cache Size", f"{cache_size:,} bytes")
        except (json.JSONDecodeError, OSError):
            pass

    # Index age and size
    index_path = sia_dir
    if index_path.exists():
        try:
            stat = index_path.stat()
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
            age = datetime.datetime.now() - mtime
            table.add_row("", "")  # Separator
            table.add_row("Index Size", f"{stat.st_size:,} bytes")
            table.add_row("Index Age", f"{age.days} days, {age.seconds // 3600} hours")
        except OSError:
            pass

    # Chunk index staleness (v2.0)
    chunk_index_path = sia_dir / "chunk_index.json"
    if chunk_index_path.exists():
        try:
            chunk_index = ChunkIndex(chunk_index_path)
            summary = chunk_index.get_staleness_summary()

            table.add_row("", "")  # Separator
            table.add_row("Total Chunks", f"{summary.total_chunks:,}")
            table.add_row("Valid Chunks", f"{summary.valid_chunks:,}")
            table.add_row("Stale Chunks", f"{summary.stale_chunks:,}")
            table.add_row("Staleness Ratio", f"{summary.staleness_ratio:.1%}")
            table.add_row("Health Status", summary.status)
        except Exception:
            pass

    console.print(table)

    # Recommendations
    if chunk_index_path.exists():
        try:
            chunk_index = ChunkIndex(chunk_index_path)
            summary = chunk_index.get_staleness_summary()

            if summary.staleness_ratio >= 0.2:
                console.print(f"\n{summary.status}")
                console.print(f"[dim]Recommendation: {summary.recommendation}[/dim]")
        except Exception:
            pass
    elif index_path.exists():
        # Fallback to age-based warning
        try:
            stat = index_path.stat()
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
            age = datetime.datetime.now() - mtime
            if age.days > 30:
                console.print("\n[yellow]⚠️  Warning: Index is over 30 days old.[/yellow]")
                console.print(
                    "[dim]Consider running 'sia-code index --clean' to rebuild fresh index.[/dim]"
                )
        except OSError:
            pass


@main.command()
@click.argument("path", type=click.Path(exists=True), default=".")
@click.option("--threshold", type=float, default=0.2, help="Minimum staleness ratio to compact")
@click.option("--force", is_flag=True, help="Force compaction regardless of threshold")
def compact(path: str, threshold: float, force: bool):
    """Compact index by removing stale chunks.

    This rebuilds the index with only valid chunks, removing all stale
    chunks that accumulated from file modifications. Improves search
    quality and reduces index size.

    Example:
        sia-code compact              # Compact if >20% stale
        sia-code compact --threshold 0.1  # Compact if >10% stale
        sia-code compact --force      # Force compaction now
    """
    from .indexer.chunk_index import ChunkIndex

    sia_dir, config = require_initialized()

    # Check if chunk index exists
    chunk_index_path = sia_dir / "chunk_index.json"
    if not chunk_index_path.exists():
        console.print("[yellow]Chunk index not found. Compaction requires chunk tracking.[/yellow]")
        console.print(
            "[dim]Run incremental indexing to build chunk index, or use --clean to rebuild.[/dim]"
        )
        sys.exit(1)

    # Load chunk index
    chunk_index = ChunkIndex(chunk_index_path)
    summary = chunk_index.get_staleness_summary()

    console.print("[cyan]Index Health Check[/cyan]")
    console.print(f"  Total chunks: {summary.total_chunks:,}")
    console.print(f"  Valid chunks: {summary.valid_chunks:,}")
    console.print(f"  Stale chunks: {summary.stale_chunks:,}")
    console.print(f"  Staleness: {summary.staleness_ratio:.1%}")
    console.print(f"  Status: {summary.status}\n")

    # Load backend
    backend = create_backend(sia_dir, config, suppress_stdout_notices=True)
    backend.open_index(writable=True)

    coordinator = IndexingCoordinator(config, backend)
    directory = Path(path).resolve()

    # Override threshold if force
    if force:
        threshold = 0.0
        console.print("[yellow]Forcing compaction...[/yellow]\n")

    # Perform compaction
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("Compacting index...", total=None)

        try:
            stats = coordinator.compact_index(directory, chunk_index, threshold)
            progress.update(task, completed=True)

            if not stats["compaction_needed"]:
                console.print(f"\n[green]✓ {stats['message']}[/green]")
            else:
                console.print("\n[green]✓ Compaction complete[/green]")
                console.print(f"  Files reindexed: {stats['files_reindexed']}")
                console.print(f"  Chunks stored: {stats['chunks_stored']}")
                console.print(
                    f"  Removed {stats['stale_chunks']:,} stale chunks "
                    f"({stats['staleness_ratio']:.1%} of total)"
                )

                if stats.get("metrics"):
                    m = stats["metrics"]
                    console.print("\n[dim]Performance:[/dim]")
                    console.print(f"  Duration: {m['duration_seconds']}s")
                    console.print(
                        f"  Throughput: {m['files_per_second']:.1f} files/s, "
                        f"{m['chunks_per_second']:.1f} chunks/s"
                    )

                if stats["errors"]:
                    console.print("\n[yellow]Warnings:[/yellow]")
                    for error in stats["errors"][:5]:
                        console.print(f"  {error}")
                    if len(stats["errors"]) > 5:
                        console.print(f"  ... and {len(stats['errors']) - 5} more")

        except Exception as e:
            console.print(f"[red]Error during compaction: {e}[/red]")
            sys.exit(1)


@main.group()
def config():
    """Manage Sia Code configuration."""
    pass


@config.command(name="show")
def config_show():
    """Display current configuration."""
    sia_dir, cfg = require_initialized()
    config_path = sia_dir / "config.json"

    console.print("[bold cyan]Sia Code Configuration[/bold cyan]\n")
    console.print(cfg.model_dump_json(indent=2))
    console.print(f"\n[dim]Config file: {config_path}[/dim]")


@config.command(name="path")
def config_path():
    """Show configuration file path."""
    sia_dir, _ = require_initialized()
    config_path = sia_dir / "config.json"
    console.print(str(config_path.absolute()))


@config.command(name="edit")
def config_edit():
    """Open configuration in $EDITOR."""
    import os
    import subprocess

    sia_dir, _ = require_initialized()
    config_path = sia_dir / "config.json"

    editor = os.environ.get("EDITOR", "nano")
    try:
        subprocess.run([editor, str(config_path)], check=True)
        console.print("[green]✓[/green] Configuration updated")

        # Validate the edited config
        try:
            Config.load(config_path)
            console.print("[green]✓[/green] Configuration is valid")
        except Exception as e:
            console.print(f"[red]Error: Invalid configuration: {e}[/red]")
            console.print("[yellow]Please fix the configuration file manually[/yellow]")
            sys.exit(1)
    except subprocess.CalledProcessError:
        console.print("[yellow]Editor exited with error[/yellow]")
        sys.exit(1)
    except FileNotFoundError:
        console.print(f"[red]Error: Editor '{editor}' not found[/red]")
        console.print("[dim]Set $EDITOR environment variable or install nano[/dim]")
        sys.exit(1)


@config.command(name="get")
@click.argument("key")
def config_get(key: str):
    """Get a configuration value.

    Example: sia-code config get search.vector_weight
    """
    sia_dir, cfg = require_initialized()

    # Navigate nested keys (e.g., "search.vector_weight")
    parts = key.split(".")
    value = cfg.model_dump()

    try:
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                console.print(f"[red]Unknown key: {key}[/red]")
                sys.exit(1)

        # Pretty print the value
        import json

        console.print(json.dumps(value, indent=2))
    except Exception as e:
        console.print(f"[red]Error getting config value: {e}[/red]")
        sys.exit(1)


@config.command(name="set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a configuration value.

    Example: sia-code config set search.vector_weight 0.0
    """
    sia_dir, cfg = require_initialized()
    config_path = sia_dir / "config.json"

    # Parse value type (int, float, bool, or string)
    parsed_value = _parse_config_value(value)

    # Update nested key
    data = cfg.model_dump()
    parts = key.split(".")

    try:
        _set_nested(data, parts, parsed_value)

        # Validate and save
        new_cfg = Config.model_validate(data)
        new_cfg.save(config_path)
        console.print(f"[green]✓[/green] Set {key} = {parsed_value}")
    except Exception as e:
        console.print(f"[red]Error setting config value: {e}[/red]")
        sys.exit(1)


def _parse_config_value(value: str):
    """Parse config value from string to appropriate type."""
    # Try bool
    if value.lower() in ("true", "yes", "1"):
        return True
    elif value.lower() in ("false", "no", "0"):
        return False

    # Try int
    try:
        return int(value)
    except ValueError:
        pass

    # Try float
    try:
        return float(value)
    except ValueError:
        pass

    # Default to string
    return value


def _set_nested(data: dict, keys: list[str], value):
    """Set nested dictionary value."""
    for key in keys[:-1]:
        if key not in data:
            console.print(f"[red]Unknown key path: {'.'.join(keys)}[/red]")
            sys.exit(1)
        data = data[key]

    if keys[-1] not in data:
        console.print(f"[red]Unknown key: {keys[-1]}[/red]")
        sys.exit(1)

    data[keys[-1]] = value


@main.group()
def memory():
    """Manage project memory (decisions, timeline, changelogs)."""
    pass


@memory.command(name="sync-git")
@click.option("--since", default="HEAD~100", help="Git ref to start from (e.g., v1.0.0, HEAD~50)")
@click.option("--limit", type=int, default=50, help="Maximum events to process")
@click.option("--dry-run", is_flag=True, help="Preview without importing")
@click.option("--tags-only", is_flag=True, help="Only scan tags, skip merge commits")
@click.option("--merges-only", is_flag=True, help="Only scan merge commits, skip tags")
@click.option(
    "--min-importance",
    type=click.Choice(["high", "medium", "low"]),
    default="low",
    help="Minimum importance level to import",
)
def memory_sync_git(since, limit, dry_run, tags_only, merges_only, min_importance):
    """Import timeline events and changelogs from git history.

    Scans git tags for changelogs and merge commits for timeline events.
    Automatically deduplicates to prevent importing the same event twice.
    """
    from .memory.git_sync import GitSyncService

    sia_dir, config = require_initialized()

    # Check if in a git repository
    if not Path(".git").exists():
        console.print("[red]Error: Not a git repository[/red]")
        console.print("[dim]Run this command from a git repository root[/dim]")
        sys.exit(1)

    backend = create_backend(sia_dir, config)
    backend.open_index(writable=True)

    try:
        console.print(f"[cyan]Syncing git history from {since}...[/cyan]\n")

        sync_service = GitSyncService(backend, Path("."))
        stats = sync_service.sync(
            since=since,
            limit=limit,
            dry_run=dry_run,
            tags_only=tags_only,
            merges_only=merges_only,
            min_importance=min_importance,
        )

        # Display results
        console.print("[bold]Summary:[/bold]")
        console.print(
            f"  Changelogs: {stats['changelogs_added']} added, {stats['changelogs_skipped']} skipped"
        )
        console.print(
            f"  Timeline:   {stats['timeline_added']} added, {stats['timeline_skipped']} skipped"
        )

        if stats["errors"]:
            console.print("\n[yellow]Warnings:[/yellow]")
            for error in stats["errors"]:
                console.print(f"  {error}")

        if dry_run:
            console.print("\n[yellow]Dry run complete. No changes made.[/yellow]")
        else:
            console.print(f"\n[green]✓[/green] Imported {stats['total_added']} items")

        backend.close()
    except Exception as e:
        console.print(f"[red]Error during git sync: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)


@memory.command(name="add-decision")
@click.argument("title")
@click.option("--description", "-d", required=True, help="Full description")
@click.option("--reasoning", "-r", help="Why this decision was made")
@click.option("--alternatives", "-a", help="Comma-separated list of alternatives considered")
@click.option(
    "--link-file",
    "link_files",
    multiple=True,
    help="Link decision to file path (repeatable)",
)
@click.option(
    "--link-symbol",
    "link_symbols",
    multiple=True,
    help="Link decision to symbol/entity (repeatable)",
)
@click.option(
    "--link-timeline",
    "link_timeline_refs",
    multiple=True,
    help="Link decision to timeline ref (repeatable)",
)
@click.option(
    "--link-changelog",
    "link_changelog_tags",
    multiple=True,
    help="Link decision to changelog tag (repeatable)",
)
def memory_add_decision(
    title,
    description,
    reasoning,
    alternatives,
    link_files,
    link_symbols,
    link_timeline_refs,
    link_changelog_tags,
):
    """Add a pending technical decision.

    Example: sia-code memory add-decision "Use PostgreSQL" -d "Need ACID compliance" -r "Better than MySQL for our use case"
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index(writable=True)

    try:
        alt_list = _parse_alternatives(alternatives)
        conceptual_links = _build_conceptual_links(
            link_files=link_files,
            link_symbols=link_symbols,
            link_timeline_refs=link_timeline_refs,
            link_changelog_tags=link_changelog_tags,
        )

        # Get session ID (simple timestamp-based)
        import datetime

        session_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

        commit_hash, commit_time = get_git_commit_context(Path("."))

        decision_id = backend.add_decision(
            session_id=session_id,
            title=title,
            description=description,
            reasoning=reasoning,
            alternatives=alt_list,
            conceptual_links=conceptual_links,
            commit_hash=commit_hash,
            commit_time=commit_time,
        )

        console.print(f"[green]✓[/green] Created decision #{decision_id}: {title}")
        console.print("[dim]Use 'sia-code memory approve {decision_id}' to approve[/dim]")

        backend.close()
    except Exception as e:
        console.print(f"[red]Error adding decision: {e}[/red]")
        sys.exit(1)


@memory.command(name="list")
@click.option(
    "--type",
    "item_type",
    type=click.Choice(["decision", "timeline", "changelog", "all"]),
    default="all",
    help="Type of items to list",
)
@click.option(
    "--status",
    type=click.Choice(["pending", "approved", "rejected", "all"]),
    default="all",
    help="Filter decisions by status",
)
@click.option("--limit", type=int, default=20, help="Maximum items to show")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "table"]),
    default="text",
    help="Output format",
)
def memory_list(item_type, status, limit, output_format):
    """List memory items (decisions, timeline, changelogs)."""
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index(writable=True)

    try:
        results = {"decisions": [], "timeline": [], "changelogs": []}

        # Fetch decisions
        if item_type in ("decision", "all"):
            if status == "pending":
                results["decisions"] = backend.list_pending_decisions(limit=limit)
            else:
                # Get all decisions (pending + approved)
                results["decisions"] = backend.list_pending_decisions(limit=limit * 2)
                if status != "all":
                    results["decisions"] = [d for d in results["decisions"] if d.status == status]

        # Fetch timeline events
        if item_type in ("timeline", "all"):
            results["timeline"] = backend.get_timeline_events(limit=limit)

        # Fetch changelogs
        if item_type in ("changelog", "all"):
            results["changelogs"] = backend.get_changelogs(limit=limit)

        # Output
        if output_format == "json":
            import json

            output = {}
            if results["decisions"]:
                output["decisions"] = [d.to_dict() for d in results["decisions"]]
            if results["timeline"]:
                output["timeline"] = [t.to_dict() for t in results["timeline"]]
            if results["changelogs"]:
                output["changelogs"] = [c.to_dict() for c in results["changelogs"]]
            console.print(json.dumps(output, indent=2))

        elif output_format == "table":
            if results["decisions"]:
                table = Table(title="Decisions")
                table.add_column("ID", style="cyan")
                table.add_column("Title")
                table.add_column("Status")
                for d in results["decisions"]:
                    table.add_row(str(d.id), d.title, d.status)
                console.print(table)

            if results["timeline"]:
                table = Table(title="Timeline Events")
                table.add_column("Type", style="cyan")
                table.add_column("From → To")
                table.add_column("Summary")
                for t in results["timeline"]:
                    table.add_row(t.event_type, f"{t.from_ref} → {t.to_ref}", t.summary[:50])
                console.print(table)

            if results["changelogs"]:
                table = Table(title="Changelogs")
                table.add_column("Tag", style="cyan")
                table.add_column("Version")
                table.add_column("Summary")
                for c in results["changelogs"]:
                    table.add_row(c.tag, c.version or "N/A", c.summary[:50])
                console.print(table)

        else:  # text format
            if results["decisions"]:
                console.print("[bold]Decisions:[/bold]")
                for d in results["decisions"]:
                    console.print(f"  #{d.id} [{d.status}] {d.title}")
                console.print()

            if results["timeline"]:
                console.print("[bold]Timeline Events:[/bold]")
                for t in results["timeline"]:
                    console.print(f"  [{t.event_type}] {t.from_ref} → {t.to_ref}: {t.summary[:60]}")
                console.print()

            if results["changelogs"]:
                console.print("[bold]Changelogs:[/bold]")
                for c in results["changelogs"]:
                    console.print(f"  {c.tag} ({c.version or 'N/A'}): {c.summary[:60]}")

        backend.close()
    except Exception as e:
        console.print(f"[red]Error listing memory: {e}[/red]")
        sys.exit(1)


@memory.command(name="approve")
@click.argument("decision_id", type=int)
@click.option(
    "--category", "-c", required=True, help="Category (e.g., architecture, pattern, infrastructure)"
)
def memory_approve(decision_id, category):
    """Approve a pending decision.

    Example: sia-code memory approve 123 --category architecture
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index(writable=True)

    try:
        # Get decision to show what's being approved
        decision = backend.get_decision(decision_id)
        if not decision:
            console.print(f"[red]Decision #{decision_id} not found[/red]")
            sys.exit(1)

        if decision.status != "pending":
            console.print(f"[yellow]Decision #{decision_id} is already {decision.status}[/yellow]")
            sys.exit(1)

        # Approve
        backend.approve_decision(decision_id, category)
        console.print(f"[green]✓[/green] Approved decision #{decision_id}: {decision.title}")
        console.print(f"[dim]Category: {category}[/dim]")

        backend.close()
    except Exception as e:
        console.print(f"[red]Error approving decision: {e}[/red]")
        sys.exit(1)


@memory.command(name="reject")
@click.argument("decision_id", type=int)
def memory_reject(decision_id):
    """Reject a pending decision.

    Example: sia-code memory reject 123
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index(writable=True)

    try:
        decision = backend.get_decision(decision_id)
        if not decision:
            console.print(f"[red]Decision #{decision_id} not found[/red]")
            sys.exit(1)

        backend.reject_decision(decision_id)
        console.print(f"[green]✓[/green] Rejected decision #{decision_id}: {decision.title}")

        backend.close()
    except Exception as e:
        console.print(f"[red]Error rejecting decision: {e}[/red]")
        sys.exit(1)


@memory.command(name="search")
@click.argument("query")
@click.option(
    "--type",
    "search_type",
    type=click.Choice(["decision", "timeline", "changelog", "all"]),
    default="all",
    help="Type of memory to search",
)
@click.option("-k", "--limit", type=int, default=10, help="Number of results")
def memory_search(query, search_type, limit):
    """Search project memory.

    Example: sia-code memory search "why PostgreSQL" --type decision
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index()

    try:
        # Use backend's search_memory method
        results = backend.search_memory(query, k=limit)

        if not results:
            console.print("[yellow]No results found[/yellow]")
        else:
            console.print(f"[bold]Found {len(results)} results:[/bold]\n")
            for i, result in enumerate(results, 1):
                meta = result.chunk.metadata
                console.print(f"{i}. {result.chunk.symbol} [score: {result.score:.2f}]")
                console.print(f"   Type: {meta.get('type', 'unknown')}")
                console.print(f"   {result.chunk.code[:100]}...")
                console.print()

        backend.close()
    except Exception as e:
        console.print(f"[red]Error searching memory: {e}[/red]")
        sys.exit(1)


@memory.command(name="working-set")
@click.argument("query")
@click.option("--agent", type=str, help="Agent name using this shared working-memory payload")
@click.option("--session-id", type=str, help="Session identifier for multi-step agent work")
@click.option("-o", "--output", type=click.Path(), help="Write JSON payload to file")
def memory_working_set(query, agent, session_id, output):
    """Build a shared working-memory JSON payload for agents.

    Example: sia-code memory working-set "auth flow" --agent planner --session-id ses-123
    """
    import json

    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config, suppress_stdout_notices=True)
    backend.open_index()

    try:
        payload = build_working_memory_payload(
            backend=backend,
            query=query,
            agent=agent,
            session_id=session_id,
            base_dir=Path("."),
        )

        if output:
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, indent=2))
            console.print(f"[green]✓[/green] Working memory written to {output_path}")
        else:
            sys.stdout.write(json.dumps(payload, indent=2))
            sys.stdout.write("\n")
    except Exception as e:
        console.print(f"[red]Error building working memory: {e}[/red]")
        sys.exit(1)
    finally:
        backend.close()


@memory.command(name="trace")
@click.argument("query")
@click.option("--hops", type=int, default=1, help="Graph expansion hops from seed symbols")
@click.option("--seed-limit", type=int, default=5, help="Lexical seed count for query and edges")
@click.option("--timeline-limit", type=int, default=100, help="Timeline events to score")
@click.option("-k", "--limit", type=int, default=10, help="Maximum ranked events to return")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "table"]),
    default="text",
    help="Output format",
)
def memory_trace(query, hops, seed_limit, timeline_limit, limit, output_format):
    """Trace timeline events likely related to a query.

    Combines lexical seeds, persisted code relationships, and timeline file overlap
    to answer "when did this behavior likely change?" questions.
    """
    import json
    from .memory.causal_trace import CausalTracer

    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index()

    try:
        tracer = CausalTracer(backend)
        result = tracer.trace(
            query=query,
            hops=max(0, hops),
            seed_limit=max(1, seed_limit),
            timeline_limit=max(1, timeline_limit),
            limit=max(1, limit),
        )

        if output_format == "json":
            payload = {
                "query": result.query,
                "seed_symbols": result.seed_symbols,
                "related_symbols": result.related_symbols,
                "related_files": result.related_files,
                "events": [
                    {
                        "score": event.score,
                        "matched_files": event.matched_files,
                        "event": event.event.to_dict(),
                    }
                    for event in result.events
                ],
            }
            console.print(json.dumps(payload, indent=2))
            return

        if output_format == "table":
            if not result.events:
                console.print("[yellow]No causal timeline events found.[/yellow]")
                return

            table = Table(title=f"Temporal Trace: {query}")
            table.add_column("Score", style="cyan")
            table.add_column("Importance")
            table.add_column("From -> To")
            table.add_column("Matched Files")
            table.add_column("Summary")

            for item in result.events:
                table.add_row(
                    f"{item.score:.1f}",
                    item.event.importance,
                    f"{item.event.from_ref} -> {item.event.to_ref}",
                    ", ".join(item.matched_files[:2]),
                    item.event.summary[:60],
                )

            console.print(table)
            return

        console.print(f"[bold]Temporal Trace:[/bold] {query}\n")
        console.print(
            f"Seed symbols: {', '.join(result.seed_symbols) if result.seed_symbols else 'none'}"
        )
        console.print(f"Related symbols: {len(result.related_symbols)}")
        console.print(f"Related files: {len(result.related_files)}\n")

        if not result.events:
            console.print("[yellow]No causal timeline events found.[/yellow]")
            return

        for idx, item in enumerate(result.events, 1):
            event = item.event
            console.print(
                f"{idx}. [cyan]score={item.score:.1f}[/cyan] "
                f"[{event.importance}] {event.from_ref} -> {event.to_ref}"
            )
            console.print(f"   {event.summary}")
            console.print(f"   matched files: {', '.join(item.matched_files)}")
            if event.created_at:
                console.print(f"   at: {event.created_at.isoformat()}")
            console.print()
    except Exception as e:
        console.print(f"[red]Error tracing memory timeline: {e}[/red]")
        sys.exit(1)
    finally:
        backend.close()


@memory.command(name="timeline")
@click.option("--since", type=str, help="Filter by date (YYYY-MM-DD)")
@click.option(
    "--event-type",
    type=click.Choice(["merge", "tag", "major_change"]),
    help="Filter by event type",
)
@click.option(
    "--importance",
    type=click.Choice(["high", "medium", "low"]),
    help="Filter by importance",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "table", "markdown"]),
    default="text",
    help="Output format",
)
def memory_timeline(since, event_type, importance, output_format):
    """Show project timeline events.

    Example: sia-code memory timeline --format markdown --importance high
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index()

    try:
        events = backend.get_timeline_events(limit=100)

        # Apply filters
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if importance:
            events = [e for e in events if e.importance == importance]
        if since:
            from datetime import datetime

            since_date = datetime.fromisoformat(since)
            events = [e for e in events if e.created_at and e.created_at >= since_date]

        # Output
        if output_format == "json":
            import json

            console.print(json.dumps([e.to_dict() for e in events], indent=2))

        elif output_format == "markdown":
            console.print("# Project Timeline\n")
            for e in events:
                date_str = e.created_at.strftime("%Y-%m-%d") if e.created_at else "Unknown"
                console.print(f"## {date_str} - {e.from_ref} → {e.to_ref}")
                console.print(f"**Type:** {e.event_type} | **Importance:** {e.importance}\n")
                console.print(f"{e.summary}\n")
                if e.files_changed:
                    console.print(f"**Files changed:** {len(e.files_changed)}\n")

        elif output_format == "table":
            table = Table(title="Project Timeline")
            table.add_column("Date", style="cyan")
            table.add_column("Type")
            table.add_column("From → To")
            table.add_column("Summary")
            table.add_column("Importance")

            for e in events:
                date_str = e.created_at.strftime("%Y-%m-%d") if e.created_at else "Unknown"
                table.add_row(
                    date_str,
                    e.event_type,
                    f"{e.from_ref} → {e.to_ref}",
                    e.summary[:40],
                    e.importance,
                )
            console.print(table)

        else:  # text
            for e in events:
                date_str = e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else "Unknown"
                console.print(f"[{e.importance.upper()}] {date_str}")
                console.print(f"  {e.event_type}: {e.from_ref} → {e.to_ref}")
                console.print(f"  {e.summary}")
                console.print()

        backend.close()
    except Exception as e:
        console.print(f"[red]Error displaying timeline: {e}[/red]")
        sys.exit(1)


@memory.command(name="changelog")
@click.argument("range", required=False)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "markdown"]),
    default="markdown",
    help="Output format",
)
@click.option("-o", "--output", type=click.Path(), help="Save to file")
def memory_changelog(range, output_format, output):
    """Generate changelog from memory.

    Example: sia-code memory changelog v1.0.0..v2.0.0 --format markdown -o CHANGELOG.md
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index()

    try:
        changelogs = backend.get_changelogs(limit=100)

        # Filter by range if provided
        if range:
            # Parse range (e.g., "v1.0.0..v2.0.0")
            if ".." in range:
                start, end = range.split("..")
                # Filter changelogs between versions
                changelogs = [c for c in changelogs if start <= (c.tag or "") <= end]

        # Generate output
        if output_format == "json":
            import json

            output_text = json.dumps([c.to_dict() for c in changelogs], indent=2)

        elif output_format == "markdown":
            lines = ["# Changelog\n"]
            for c in changelogs:
                date_str = c.date.strftime("%Y-%m-%d") if c.date else "Unknown"
                lines.append(f"## {c.tag} ({date_str})\n")
                if c.summary:
                    lines.append(f"{c.summary}\n")
                if c.breaking_changes:
                    lines.append("### ⚠️ Breaking Changes\n")
                    for bc in c.breaking_changes:
                        lines.append(f"- {bc}")
                    lines.append("")
                if c.features:
                    lines.append("### ✨ Features\n")
                    for feat in c.features:
                        lines.append(f"- {feat}")
                    lines.append("")
                if c.fixes:
                    lines.append("### 🐛 Fixes\n")
                    for fix in c.fixes:
                        lines.append(f"- {fix}")
                    lines.append("")
                lines.append("")
            output_text = "\n".join(lines)

        else:  # text
            lines = []
            for c in changelogs:
                date_str = c.date.strftime("%Y-%m-%d") if c.date else "Unknown"
                lines.append(f"{c.tag} ({date_str})")
                lines.append(f"  {c.summary}")
                lines.append("")
            output_text = "\n".join(lines)

        # Output to file or console
        if output:
            Path(output).write_text(output_text)
            console.print(f"[green]✓[/green] Changelog written to {output}")
        else:
            console.print(output_text)

        backend.close()
    except Exception as e:
        console.print(f"[red]Error generating changelog: {e}[/red]")
        sys.exit(1)


@memory.command(name="export")
@click.option("-o", "--output", type=click.Path(), default=None, help="Output file")
def memory_export(output):
    """Export memory to JSON file.

    Example: sia-code memory export -o memory-backup.json
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index()

    try:
        if output is None:
            output = str(sia_dir / "memory.json")
        export_path = backend.export_memory(include_pending=True)

        # Copy to specified output location if different
        if output != export_path:
            import shutil

            shutil.copy(export_path, output)

        console.print(f"[green]✓[/green] Memory exported to {output}")
        backend.close()
    except Exception as e:
        console.print(f"[red]Error exporting memory: {e}[/red]")
        sys.exit(1)


@memory.command(name="import")
@click.option(
    "-i",
    "--input",
    "input_file",
    type=click.Path(exists=True),
    default=None,
    help="Input file",
)
def memory_import(input_file):
    """Import memory from JSON file.

    Example: sia-code memory import -i memory-backup.json
    """
    sia_dir, config = require_initialized()
    backend = create_backend(sia_dir, config)
    backend.open_index(writable=True)

    try:
        if input_file is None:
            input_file = str(sia_dir / "memory.json")
        result = backend.import_memory(input_file)

        console.print("[green]✓[/green] Import complete")
        console.print(f"  Added: {result.added}")
        console.print(f"  Updated: {result.updated}")
        console.print(f"  Skipped: {result.skipped}")

        backend.close()
    except Exception as e:
        console.print(f"[red]Error importing memory: {e}[/red]")
        sys.exit(1)


@main.group()
def embed():
    """Embedding server management.

    Start a persistent daemon to share embedding models across repos.
    Saves memory and improves startup time for multi-repo workflows.
    """
    pass


@embed.command(name="start")
@click.option("--foreground", is_flag=True, help="Run in foreground (don't daemonize)")
@click.option("--log", type=click.Path(), help="Log file path (default: stderr)")
@click.option(
    "--idle-timeout",
    type=int,
    default=3600,
    help="Unload model after N seconds of inactivity (default: 3600 = 1 hour)",
)
def embed_start(foreground, log, idle_timeout):
    """Start the embedding server daemon.

    The daemon loads embedding models on-demand and shares them across
    all sia-code sessions, reducing memory usage and startup time.

    Models are automatically unloaded after idle timeout (default: 1 hour)
    to save memory, and reloaded on next request.

    Example: sia-code embed start
    Example: sia-code embed start --idle-timeout 7200  # 2 hours
    """
    from .embed_server.daemon import start_daemon

    console.print("[cyan]Starting embedding server...[/cyan]")
    console.print(f"[dim]Idle timeout: {idle_timeout}s ({idle_timeout / 60:.0f} minutes)[/dim]")

    try:
        start_daemon(foreground=foreground, log_path=log, idle_timeout_seconds=idle_timeout)
        if not foreground:
            console.print("[green]✓[/green] Embedding server started")
            console.print("[dim]Use 'sia-code embed status' to check health[/dim]")
    except Exception as e:
        console.print(f"[red]Error starting daemon: {e}[/red]")
        sys.exit(1)


@embed.command(name="stop")
def embed_stop():
    """Stop the embedding server daemon.

    Example: sia-code embed stop
    """
    from .embed_server.daemon import stop_daemon

    console.print("[cyan]Stopping embedding server...[/cyan]")

    if stop_daemon():
        console.print("[green]✓[/green] Embedding server stopped")
    else:
        console.print("[yellow]Embedding server was not running[/yellow]")


@embed.command(name="status")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed model status")
def embed_status(verbose):
    """Show embedding server status.

    Displays:
    - Running status
    - Loaded models
    - Memory usage
    - Device (CPU/GPU)
    - Idle timeout
    - Model idle times (with --verbose)

    Example: sia-code embed status
    Example: sia-code embed status -v
    """
    from .embed_server.daemon import daemon_status

    status = daemon_status()

    if status["running"]:
        health = status.get("health", {})

        console.print("[green]● Embedding server is running[/green]")
        console.print(f"  PID: {status['pid']}")
        console.print(f"  Device: {health.get('device', 'unknown')}")
        console.print(f"  Memory: {health.get('memory_mb', 0):.1f} MB")
        console.print(f"  Idle timeout: {health.get('idle_timeout_minutes', 60):.0f} minutes")

        models = health.get("models_loaded", [])
        if models:
            console.print(f"  Models loaded: {', '.join(models)}")
        else:
            console.print("  Models loaded: none (will load on first request)")

        # Verbose: Show model status details
        if verbose:
            model_status = health.get("model_status", {})
            if model_status:
                console.print("\n  [bold]Model Status:[/bold]")
                for model_name, info in model_status.items():
                    loaded = "✓ loaded" if info.get("loaded") else "✗ unloaded"
                    idle_min = info.get("idle_minutes", 0)
                    console.print(f"    {model_name}: {loaded}, idle {idle_min:.1f}m")
    else:
        console.print("[red]● Embedding server is not running[/red]")
        if "reason" in status:
            console.print(f"  Reason: {status['reason']}")
        console.print("\n[dim]Start with: sia-code embed start[/dim]")


# ---------------------------------------------------------------------------
# Dynamic Git Memory CLI Commands (consolidated)
# ---------------------------------------------------------------------------


@memory.command(name="git-context")
@click.argument("file_paths", nargs=-1, required=True)
@click.option("--no-blast-radius", is_flag=True, help="Skip blast radius analysis")
@click.option("--no-narrative", is_flag=True, help="Skip evolution narrative")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
)
def memory_git_context(file_paths, no_blast_radius, no_narrative, output_format):
    """Show git context for files: history, blast radius, and evolution narrative.

    Combines file history (revert-aware, cross-branch, recency-scored),
    co-change blast radius, and model-generated evolution narrative.
    Auto-uses local flan-t5 model for narrative when available.

    Examples:
        sia-code memory git-context src/api/datasample.py
        sia-code memory git-context src/api.py src/crud.py --format json
        sia-code memory git-context src/api.py --no-narrative
    """
    from .config import Config
    from .memory.blast_radius import BlastRadiusAnalyzer
    from .memory.diff_analyzer import DiffSemanticAnalyzer
    from .memory.git_dynamic import GitDynamicMemory
    from .memory.intent_classifier import IntentClassifier
    from .memory.recency import RecencyConfig

    project_dir = Path.cwd()
    config = Config.load(project_dir / ".sia-code" / "config.json")
    gc = config.git_dynamic

    recency_cfg = RecencyConfig(
        halflife_days=gc.recency_halflife_days,
        working_window_days=gc.working_window_days,
    )
    mem = GitDynamicMemory(project_dir, recency_config=recency_cfg)
    classifier = IntentClassifier()

    all_results = {}
    for fp in file_paths:
        hist = mem.file_history(fp, cross_branch=gc.cross_branch_enabled, limit=15)
        if not hist.effective_commits:
            console.print(f"[yellow]No history found for {fp}[/yellow]")
            continue

        # Classify intents
        for c in hist.effective_commits:
            intent = classifier.classify(c.message, len(c.files_changed), c.insertions + c.deletions)
            c.intent = intent.intent

        entry = {"hist": hist, "radius": None, "narrative": None}

        # Blast radius
        if not no_blast_radius:
            analyzer = BlastRadiusAnalyzer(
                project_dir,
                lookback=gc.lookback_commits,
                min_coupling=gc.coupling_threshold,
                max_files_per_commit=gc.max_files_per_commit,
                recency_config=recency_cfg,
            )
            entry["radius"] = analyzer.co_changed_files(fp)

        # Narrative
        if not no_narrative:
            try:
                diff_analyzer = DiffSemanticAnalyzer(project_dir, model_name=gc.narrative_model)
                entry["narrative"] = diff_analyzer.summarize_evolution(hist)
            except Exception:
                pass

        all_results[fp] = entry

    if not all_results:
        console.print("[red]No results found.[/red]")
        return

    if output_format == "json":
        import json

        data = {}
        for fp, entry in all_results.items():
            hist = entry["hist"]
            d = {
                "branch_context": {
                    "current": hist.branch_context.current_branch if hist.branch_context else None,
                    "base": hist.branch_context.base_branch if hist.branch_context else None,
                },
                "owners": hist.owners[:3],
                "reverts": [
                    {"reverted": r.reverted_hash[:7], "by": r.reverting_hash[:7]}
                    for r in hist.reverts
                ],
                "commits": [
                    {
                        "hash": c.hash[:7],
                        "message": c.message,
                        "intent": c.intent,
                        "recency": round(c.recency_score, 3),
                        "author": c.author,
                    }
                    for c in hist.effective_commits[:10]
                ],
            }
            if entry["radius"]:
                d["blast_radius"] = [
                    {"path": cf.path, "coupling": round(cf.coupling_score, 3)}
                    for cf in entry["radius"].coupled_files[:10]
                ]
            if entry["narrative"]:
                d["narrative"] = entry["narrative"].narrative
                d["phases"] = entry["narrative"].key_phases
                d["model_used"] = entry["narrative"].model_used
            data[fp] = d
        console.print(json.dumps(data, indent=2))
    else:
        # Table format
        for fp, entry in all_results.items():
            hist = entry["hist"]
            console.print(f"\n[bold]{'='*60}[/bold]")
            console.print(f"[bold]File:[/bold] {fp}")
            if hist.branch_context:
                ctx = hist.branch_context
                console.print(
                    f"  Branch: {ctx.current_branch} | Base: {ctx.base_branch}"
                )
            if hist.owners:
                owners_str = ", ".join(f"{a} ({n})" for a, n in hist.owners[:3])
                console.print(f"  Owners: {owners_str}")
            if hist.reverts:
                console.print(f"  [yellow]Reverts: {len(hist.reverts)}[/yellow]")
                for r in hist.reverts:
                    console.print(f"    {r.reverting_hash[:7]} reverts {r.reverted_hash[:7]}")

            # Narrative
            if entry["narrative"]:
                n = entry["narrative"]
                model_tag = f" [dim](via {n.model_used})[/dim]" if n.model_used else " [dim](heuristic)[/dim]"
                console.print(f"\n  [bold]Evolution:[/bold]{model_tag}")
                console.print(f"  {n.narrative}")
                if n.key_phases:
                    console.print(f"  Phases: {', '.join(n.key_phases)}")

            # Commits
            console.print(f"\n  [bold]History[/bold] ({len(hist.effective_commits)} effective):")
            for c in hist.effective_commits[:8]:
                intent_tag = f"[{c.intent}]" if c.intent else ""
                console.print(
                    f"    [{c.recency_score:.2f}] {c.hash[:7]} {c.message[:55]} "
                    f"[dim]{c.author} {intent_tag}[/dim]"
                )

            # Blast radius
            if entry["radius"] and entry["radius"].coupled_files:
                radius = entry["radius"]
                console.print(
                    f"\n  [bold]Blast Radius[/bold] "
                    f"({radius.total_commits_analyzed} commits, "
                    f"{radius.commits_excluded_squash} squash-excluded):"
                )
                for cf in radius.coupled_files[:8]:
                    bar = "█" * int(cf.coupling_score * 20)
                    console.print(
                        f"    [{cf.coupling_score:.2f}] {bar:20s} {cf.path}"
                    )
                if radius.change_clusters:
                    for cl in radius.change_clusters:
                        console.print(
                            f"    [dim]Cluster (cohesion {cl.cohesion_score:.2f}): "
                            f"{', '.join(cl.files)}[/dim]"
                        )


if __name__ == "__main__":
    main()
