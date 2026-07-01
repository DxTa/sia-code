"""MCP entrypoint for Sia Code."""

from __future__ import annotations

import json
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in non-extra installs
    FastMCP = None
    _MCP_IMPORT_ERROR = exc
else:
    _MCP_IMPORT_ERROR = None

from .config import Config
from .embed_server.client import EmbedClient
from .indexer.chunk_index import ChunkIndex
from .indexer.coordinator import IndexingCoordinator
from .indexer.hash_cache import HashCache
from .indexer.project_analyzer import ProjectAnalyzer
from .memory.causal_trace import CausalTracer
from .memory.git_sync import GitSyncService
from .mcp_lock import index_lock
from .runtime_context import (
    WorkspaceContext,
    build_working_memory_payload,
    create_backend,
    get_git_commit_context,
    resolve_workspace_context,
)
from .search.multi_hop import MultiHopSearchStrategy


ENGINEERING_RESEARCH_HINTS = (
    "architecture",
    "trace",
    "flow",
    "dependency",
    "dependencies",
    "cross-file",
    "cross file",
    "how does",
    "root cause",
    "call graph",
)


def _task_keywords(task: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z_]+", task)}


def _classify_engineering_task(task: str) -> str:
    lowered = task.lower()
    keywords = _task_keywords(task)
    if any(hint in lowered for hint in ENGINEERING_RESEARCH_HINTS):
        return "architecture_trace"
    if {"plan", "planning", "design", "spec"} & keywords:
        return "planning"
    if {"debug", "bug", "fix", "failure", "error"} & keywords:
        return "debugging"
    if {"review", "audit", "regression"} & keywords:
        return "review"
    return "exact_lookup"


def _default_probe_query(task: str) -> str:
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task)
    if identifiers:
        return identifiers[0]
    return task.strip() or "main"


def _search_matches(backend, query: str, limit: int) -> list[dict[str, Any]]:
    matches = backend.search_lexical(query, k=limit)
    return [_serialize_result(item, detail="minimal") for item in matches]


def _health_payload(
    context: WorkspaceContext,
    config: Config,
    *,
    probe_query: str | None,
    probe_limit: int = 3,
) -> dict[str, Any]:
    issues: list[str] = []
    probe: dict[str, Any] | None = None
    stats_payload: dict[str, Any] | None = None
    backend_name = config.storage.backend
    embedding_runtime = _embedding_runtime(config)

    with _backend_session(context, config, suppress_stdout_notices=True) as backend:
        stats_payload = _serialize_stats(backend.get_stats())
        try:
            backend.get_code_relationships(limit=1)
            research_ready = True
        except Exception as exc:
            research_ready = False
            issues.append(f"research schema unavailable: {exc}")

        if probe_query:
            try:
                matches = _search_matches(backend, probe_query, probe_limit)
                probe = {"query": probe_query, "matches": matches, "match_count": len(matches)}
            except Exception as exc:
                issues.append(f"probe search failed: {exc}")
                probe = {"query": probe_query, "matches": [], "match_count": 0}

    initialized = (context.index_dir / "config.json").exists()
    degraded = bool(issues)
    fallback_guidance = (
        "Use lexical search and memory tools only; avoid multi-hop research until the index schema is repaired."
        if not research_ready
        else "Use search and memory tools normally; multi-hop research is available when needed."
    )

    return {
        "initialized": initialized,
        "backend": backend_name,
        "embedding_runtime": embedding_runtime,
        "stats": stats_payload,
        "research_ready": research_ready,
        "degraded": degraded,
        "issues": issues,
        "probe": probe,
        "fallback_guidance": fallback_guidance,
    }


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: compacted
            for key, raw in value.items()
            if (compacted := _compact(raw)) not in (None, {}, [], "")
        }
    if isinstance(value, list):
        return [
            compacted for item in value if (compacted := _compact(item)) not in (None, {}, [], "")
        ]
    return value


def _ok(*, result: Any, context: WorkspaceContext | None = None, scope: str = "workspace", **extra):
    payload = {"ok": True, "scope": scope, "result": result}
    if context is not None:
        payload["resolved_workspace_root"] = str(context.workspace_root)
        payload["resolved_index_dir"] = str(context.index_dir)
    payload.update(extra)
    return _compact(payload)


def _uninitialized_payload(context: WorkspaceContext) -> dict[str, Any]:
    return {
        "initialized": False,
        "usable": False,
        "degraded": True,
        "issues": [f"Sia Code is not initialized at {context.index_dir}"],
        "recommended_action": "Ask the user before indexing, then run init and index for this workspace.",
        "recommended_commands": [
            f"sia-code init --workspace-root {context.workspace_root}",
            f"sia-code index --workspace-root {context.workspace_root} .",
        ],
    }


def _resolve_uninitialized_context(
    workspace_root: str | Path | None = None,
    index_dir: str | Path | None = None,
) -> WorkspaceContext:
    return resolve_workspace_context(workspace_root=workspace_root, index_dir=index_dir)


def _load_config(index_dir: Path) -> Config:
    config_path = index_dir / "config.json"
    if not config_path.exists():
        raise ValueError(f"Sia Code is not initialized at {index_dir}")
    return Config.load(config_path)


def _latest_source_mtime(workspace_root: Path) -> datetime | None:
    latest: float | None = None
    for path in workspace_root.rglob("*"):
        if ".sia-code" in path.parts or ".git" in path.parts:
            continue
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        latest = mtime if latest is None else max(latest, mtime)
    return datetime.fromtimestamp(latest) if latest is not None else None


def _freshness_payload(context: WorkspaceContext, stats_payload: dict[str, Any]) -> dict[str, Any]:
    last_indexed_raw = stats_payload.get("last_indexed")
    if not last_indexed_raw:
        return {
            "status": "unknown",
            "stale": False,
            "recommendation": "No last_indexed timestamp available; run index --update if results look wrong.",
        }
    try:
        last_indexed = datetime.fromisoformat(last_indexed_raw)
    except ValueError:
        return {
            "status": "unknown",
            "stale": False,
            "recommendation": "Could not parse last_indexed; run index --update if results look wrong.",
        }

    latest_source = _latest_source_mtime(context.workspace_root)
    if latest_source is None or latest_source <= last_indexed:
        return {"status": "fresh", "stale": False}

    return {
        "status": "stale",
        "stale": True,
        "latest_source_mtime": latest_source.isoformat(),
        "recommendation": "Run sia-code index --update before trusting missing or low-confidence results.",
    }


def _require_initialized_context(
    workspace_root: str | Path | None = None,
    index_dir: str | Path | None = None,
) -> tuple[WorkspaceContext, Config]:
    context = resolve_workspace_context(workspace_root=workspace_root, index_dir=index_dir)
    return context, _load_config(context.index_dir)


@contextmanager
def _backend_session(
    context: WorkspaceContext,
    config: Config,
    *,
    writable: bool = False,
    valid_chunks=None,
    suppress_stdout_notices: bool = True,
):
    backend = create_backend(
        context.index_dir,
        config,
        valid_chunks=valid_chunks,
        suppress_stdout_notices=suppress_stdout_notices,
    )
    backend.open_index(writable=writable)
    try:
        yield backend
    finally:
        backend.close()


def _augment_query(
    query: str,
    objective: str | None = None,
    current_focus: list[str] | None = None,
    constraints: list[str] | None = None,
) -> str:
    parts = [query]
    if objective:
        parts.append(f"objective: {objective}")
    if current_focus:
        parts.append(f"focus: {', '.join(current_focus)}")
    if constraints:
        parts.append(f"constraints: {', '.join(constraints)}")
    return "\n".join(parts)


def _chunk_index_valid_chunks(context: WorkspaceContext, no_filter: bool) -> set[str] | None:
    if no_filter:
        return None
    chunk_index_path = context.index_dir / "chunk_index.json"
    if not chunk_index_path.exists():
        return None
    try:
        chunk_index = ChunkIndex(chunk_index_path)
        return chunk_index.get_valid_chunks()
    except Exception:
        return None


def _serialize_chunk(chunk, detail: str = "minimal") -> dict[str, Any]:
    payload = {
        "id": chunk.id,
        "symbol": chunk.symbol,
        "file_path": str(chunk.file_path),
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "language": getattr(chunk.language, "value", chunk.language),
        "chunk_type": getattr(chunk.chunk_type, "value", chunk.chunk_type),
    }
    if detail in {"normal", "debug"}:
        payload["metadata"] = chunk.metadata
        payload["preview"] = chunk.code[:200]
    if detail == "debug":
        payload["code"] = chunk.code
    return payload


def _serialize_result(result, detail: str = "minimal") -> dict[str, Any]:
    payload = {
        "score": result.score,
        "chunk": _serialize_chunk(result.chunk, detail=detail),
    }
    snippet = result.snippet or result.chunk.code[:120]
    payload["snippet"] = snippet[:120]
    return payload


def _serialize_stats(stats) -> dict[str, Any]:
    return {
        "total_files": stats.total_files,
        "total_chunks": stats.total_chunks,
        "total_size_bytes": stats.total_size_bytes,
        "languages": {getattr(k, "value", str(k)): v for k, v in stats.languages.items()},
        "last_indexed": stats.last_indexed.isoformat() if stats.last_indexed else None,
    }


def _embedding_runtime(config: Config) -> str:
    if not config.embedding.enabled:
        return "disabled"
    return "daemon" if EmbedClient.is_available() else "in_process"


def _set_nested(data: dict, keys: list[str], value):
    for key in keys[:-1]:
        if key not in data:
            raise ValueError(f"Unknown key path: {'.'.join(keys)}")
        data = data[key]
    if keys[-1] not in data:
        raise ValueError(f"Unknown key: {keys[-1]}")
    data[keys[-1]] = value


def _parse_config_value(value: str):
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def build_server() -> FastMCP:
    if FastMCP is None:
        raise RuntimeError("MCP support requires installing 'sia-code[mcp]'") from _MCP_IMPORT_ERROR
    mcp = FastMCP("sia-code")

    @mcp.tool()
    def init(
        workspace_root: str,
        index_dir: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        context = resolve_workspace_context(workspace_root=workspace_root, index_dir=index_dir)
        if context.index_dir.exists() and not dry_run:
            config = _load_config(context.index_dir)
            return _ok(
                context=context,
                result={"initialized": True, "already_initialized": True},
                embedding_runtime=_embedding_runtime(config),
            )

        analyzer = ProjectAnalyzer(context.workspace_root)
        profile = analyzer.analyze()
        analysis = {
            "languages": profile.primary_languages,
            "multi_language": profile.is_multi_language,
            "has_dependencies": profile.has_dependencies,
            "has_documentation": profile.has_documentation,
            "recommended_strategy": profile.recommended_strategy,
        }
        if dry_run:
            return _ok(context=context, result={"initialized": False, "analysis": analysis})

        with index_lock(context.index_dir):
            context.index_dir.mkdir(parents=True, exist_ok=True)
            (context.index_dir / "cache").mkdir(exist_ok=True)
            config = Config()
            config.search.tier_boost = profile.tier_boost
            config.search.include_dependencies = profile.has_dependencies
            config.save(context.index_dir / "config.json")
            backend = create_backend(context.index_dir, config)
            backend.create_index()
            backend.close()

        return _ok(
            context=context,
            result={"initialized": True, "analysis": analysis},
            embedding_runtime=_embedding_runtime(config),
        )

    @mcp.tool()
    def index(
        workspace_root: str,
        index_dir: str | None = None,
        path: str | None = None,
        update: bool = False,
        clean: bool = False,
        parallel: bool = False,
        workers: int | None = None,
        no_git_sync: bool = False,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        directory = (context.workspace_root / path).resolve() if path else context.workspace_root

        with index_lock(context.index_dir):
            if clean:
                for target in (
                    context.index_dir / "index.db",
                    context.index_dir / "cache" / "file_hashes.json",
                    context.index_dir / "vectors.usearch",
                ):
                    if target.exists():
                        target.unlink()

            backend = create_backend(context.index_dir, config, suppress_stdout_notices=True)
            if clean:
                backend.create_index()
                update = False
            else:
                backend.open_index()

            coordinator = IndexingCoordinator(config, backend)
            if update:
                cache = HashCache(context.index_dir / "cache" / "file_hashes.json")
                chunk_index = ChunkIndex(context.index_dir / "chunk_index.json")
                stats = coordinator.index_directory_incremental_v2(directory, cache, chunk_index)
            else:
                if parallel:
                    stats = coordinator.index_directory_parallel(directory, max_workers=workers)
                else:
                    stats = coordinator.index_directory(directory)
            backend.close()

            sync_stats = None
            if not no_git_sync:
                backend = create_backend(context.index_dir, config, suppress_stdout_notices=True)
                backend.open_index(writable=True)
                try:
                    sync_service = GitSyncService(backend, context.workspace_root, config=config)
                    sync_stats = sync_service.sync(since="HEAD~100", limit=50)
                finally:
                    backend.close()

        return _ok(
            context=context,
            result={"stats": stats, "git_sync": sync_stats},
            embedding_runtime=_embedding_runtime(config),
        )

    @mcp.tool()
    def status(workspace_root: str, index_dir: str | None = None) -> dict[str, Any]:
        """Report index stats for a workspace.

        Use this to inspect whether a repo is initialized and how large the index is.
        Prefer `health_check` before relying on multi-hop research during engineering work.
        """
        context = _resolve_uninitialized_context(workspace_root, index_dir)
        try:
            config = _load_config(context.index_dir)
        except ValueError:
            return _ok(context=context, result=_uninitialized_payload(context))
        with _backend_session(context, config) as backend:
            stats = backend.get_stats()

        stats_payload = _serialize_stats(stats)

        chunk_summary = None
        chunk_index_path = context.index_dir / "chunk_index.json"
        if chunk_index_path.exists():
            try:
                chunk_index = ChunkIndex(chunk_index_path)
                summary = chunk_index.get_staleness_summary()
                chunk_summary = {
                    "total_chunks": summary.total_chunks,
                    "valid_chunks": summary.valid_chunks,
                    "stale_chunks": summary.stale_chunks,
                    "staleness_ratio": summary.staleness_ratio,
                    "status": summary.status,
                    "recommendation": summary.recommendation,
                }
            except Exception:
                chunk_summary = None

        return _ok(
            context=context,
            result={
                "initialized": True,
                "usable": True,
                "stats": stats_payload,
                "chunk_health": chunk_summary,
                "freshness": _freshness_payload(context, stats_payload),
            },
            backend=config.storage.backend,
            embedding_runtime=_embedding_runtime(config),
        )

    @mcp.tool()
    def health_check(
        workspace_root: str,
        index_dir: str | None = None,
        probe_query: str | None = None,
        probe_limit: int = 3,
    ) -> dict[str, Any]:
        """Check whether the index is ready for engineering retrieval.

        Use this before planning, debugging, review, or architecture tracing. It verifies
        lightweight search and whether persisted relationship data is available for
        `research`.
        """
        context = _resolve_uninitialized_context(workspace_root, index_dir)
        try:
            config = _load_config(context.index_dir)
        except ValueError:
            return _ok(context=context, result=_uninitialized_payload(context))
        payload = _health_payload(
            context,
            config,
            probe_query=probe_query,
            probe_limit=max(1, probe_limit),
        )
        if payload.get("stats"):
            payload["freshness"] = _freshness_payload(context, payload["stats"])
            if payload["freshness"].get("stale"):
                payload["degraded"] = True
                payload["issues"].append("index appears stale compared with workspace files")
        return _ok(context=context, result=payload)

    @mcp.tool()
    def search(
        workspace_root: str,
        query: str,
        index_dir: str | None = None,
        mode: str = "hybrid",
        limit: int = 5,
        no_filter: bool = False,
        no_deps: bool = False,
        deps_only: bool = False,
        detail: str = "minimal",
    ) -> dict[str, Any]:
        """Search indexed code for engineering tasks.

        Use this as the first retrieval step for symbols, files, and lightweight task
        exploration. Prefer lexical/regex mode for exact identifiers.
        """
        if no_deps and deps_only:
            raise ValueError("no_deps and deps_only cannot both be true")
        context = _resolve_uninitialized_context(workspace_root, index_dir)
        try:
            config = _load_config(context.index_dir)
        except ValueError:
            return _ok(
                context=context,
            result={
                "query": query,
                "mode": mode,
                "match_count": 0,
                "matches": [],
                "readiness": _uninitialized_payload(context),
                "warning": "Search skipped because the workspace is not indexed.",
                },
            )
        valid_chunks = _chunk_index_valid_chunks(context, no_filter)
        include_deps = not no_deps
        tier_boost = config.search.tier_boost if hasattr(config.search, "tier_boost") else None

        with _backend_session(context, config, valid_chunks=valid_chunks) as backend:
            if mode in {"regex", "lexical"}:
                results = backend.search_lexical(
                    query, k=limit, include_deps=include_deps, tier_boost=tier_boost
                )
                resolved_mode = "lexical"
            elif mode in {"semantic", "semantic_only"}:
                results = backend.search_semantic(
                    query, k=limit, include_deps=include_deps, tier_boost=tier_boost
                )
                resolved_mode = "semantic"
            else:
                results = backend.search_hybrid(
                    query,
                    k=limit,
                    vector_weight=config.search.vector_weight,
                    include_deps=include_deps,
                    tier_boost=tier_boost,
                )
                resolved_mode = "hybrid"

        if deps_only:
            results = [r for r in results if r.chunk.metadata.get("tier") == "dependency"]

        stats_payload = None
        with _backend_session(context, config, suppress_stdout_notices=True) as backend:
            stats_payload = _serialize_stats(backend.get_stats())
        freshness = _freshness_payload(context, stats_payload)
        warning = None
        if not results and freshness.get("stale"):
            warning = "No matches found, but the index appears stale. Run index --update before trusting this result."

        return _ok(
            context=context,
            result={
                "query": query,
                "mode": resolved_mode,
                "match_count": len(results),
                "matches": [_serialize_result(result, detail=detail) for result in results],
                "freshness": freshness,
                "warning": warning,
            },
            backend=config.storage.backend,
            embedding_runtime=_embedding_runtime(config),
        )

    @mcp.tool()
    def research(
        workspace_root: str,
        question: str,
        index_dir: str | None = None,
        hops: int = 2,
        graph: bool = False,
        limit: int = 5,
        no_filter: bool = False,
        detail: str = "minimal",
        objective: str | None = None,
        current_focus: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run multi-hop code research for architecture-heavy engineering questions.

        Use this after `health_check` confirms research readiness, or when
        `engineering_bootstrap` recommends deeper tracing.
        """
        context, config = _require_initialized_context(workspace_root, index_dir)
        valid_chunks = _chunk_index_valid_chunks(context, no_filter)
        augmented_question = _augment_query(question, objective, current_focus, constraints)

        with _backend_session(context, config, valid_chunks=valid_chunks) as backend:
            strategy = MultiHopSearchStrategy(backend, max_hops=hops)
            result = strategy.research(augmented_question, max_results_per_hop=limit)
            call_graph = strategy.build_call_graph(result.relationships) if graph else None

        return _ok(
            context=context,
            result={
                "question": question,
                "chunks": [_serialize_chunk(chunk, detail=detail) for chunk in result.chunks],
                "relationships": [rel.__dict__ for rel in result.relationships],
                "call_graph": call_graph,
                "hops_executed": result.hops_executed,
                "total_entities_found": result.total_entities_found,
            },
            backend=config.storage.backend,
            embedding_runtime=_embedding_runtime(config),
        )

    @mcp.tool()
    def engineering_bootstrap(
        workspace_root: str,
        task: str,
        index_dir: str | None = None,
        objective: str | None = None,
        current_focus: list[str] | None = None,
        constraints: list[str] | None = None,
        research_mode: str = "auto",
        include_memory: bool = True,
        include_search: bool = True,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Build compact engineering context for planning, debugging, and review.

        This is the preferred first call for engineering tasks in MCP-capable clients.
        It performs health checks, lightweight retrieval, optional memory lookup, and
        only escalates to multi-hop research when the task shape and index readiness
        justify it.
        """
        if research_mode not in {"auto", "never", "always"}:
            raise ValueError("research_mode must be one of: auto, never, always")

        context = _resolve_uninitialized_context(workspace_root, index_dir)
        try:
            config = _load_config(context.index_dir)
        except ValueError:
            readiness = _uninitialized_payload(context)
            return _ok(
                context=context,
                result={
                    "task": task,
                    "task_classification": _classify_engineering_task(task),
                    "health": readiness,
                    "degraded": True,
                    "research_ready": False,
                    "search_hits": None,
                    "memory_hits": None,
                    "working_memory": None,
                    "research": {"executed": False, "mode": research_mode, "reason": "workspace is not indexed"},
                    "recommended_next_step": readiness["recommended_action"],
                    "fallback_guidance": "Use grep/glob or ask to initialize Sia Code before relying on MCP retrieval.",
                },
            )
        probe_query = _default_probe_query(task)
        health = _health_payload(
            context,
            config,
            probe_query=probe_query,
            probe_limit=min(max(1, limit), 3),
        )
        if health.get("stats"):
            health["freshness"] = _freshness_payload(context, health["stats"])
            if health["freshness"].get("stale"):
                health["degraded"] = True
                health["issues"].append("index appears stale compared with workspace files")
        task_classification = _classify_engineering_task(task)
        augmented_task = _augment_query(task, objective, current_focus, constraints)

        search_hits: dict[str, Any] | None = None
        working_memory: dict[str, Any] | None = None
        memory_hits: dict[str, Any] | None = None
        research_payload: dict[str, Any] = {
            "executed": False,
            "mode": research_mode,
            "reason": "disabled by caller" if research_mode == "never" else "not required yet",
        }

        with _backend_session(context, config, suppress_stdout_notices=True) as backend:
            if include_search:
                try:
                    matches = _search_matches(backend, probe_query, max(1, limit))
                    search_hits = {
                        "query": probe_query,
                        "match_count": len(matches),
                        "matches": matches,
                    }
                except Exception:
                    search_hits = {"query": probe_query, "match_count": 0, "matches": []}

            if include_memory:
                try:
                    memory_results = backend.search_memory(augmented_task, k=max(1, min(limit, 3)))
                    matches = [_serialize_result(item, detail="minimal") for item in memory_results]
                    memory_hits = {
                        "query": augmented_task,
                        "match_count": len(matches),
                        "matches": matches,
                    }
                except Exception:
                    memory_hits = {"query": augmented_task, "match_count": 0, "matches": []}

                try:
                    working_memory = build_working_memory_payload(
                        backend=backend,
                        query=augmented_task,
                        agent="engineering-bootstrap",
                        session_id=None,
                        base_dir=context.workspace_root,
                    )["working_memory"]
                except Exception:
                    working_memory = None

        should_research = False
        if research_mode == "always":
            should_research = True
        elif research_mode == "auto":
            should_research = task_classification in {"architecture_trace", "debugging", "review"}

        if should_research and health["research_ready"]:
            try:
                with _backend_session(context, config, suppress_stdout_notices=True) as backend:
                    strategy = MultiHopSearchStrategy(backend, max_hops=2)
                    research_result = strategy.research(
                        augmented_task, max_results_per_hop=max(1, limit)
                    )
                research_payload = {
                    "executed": True,
                    "mode": research_mode,
                    "question": task,
                    "chunks": [
                        _serialize_chunk(chunk, detail="minimal")
                        for chunk in research_result.chunks
                    ],
                    "relationships": [rel.__dict__ for rel in research_result.relationships],
                    "hops_executed": research_result.hops_executed,
                    "total_entities_found": research_result.total_entities_found,
                }
            except Exception as exc:
                health["degraded"] = True
                health["issues"].append(f"research execution failed: {exc}")
                health["fallback_guidance"] = (
                    "Use search and working memory only for now; multi-hop research failed during bootstrap."
                )
                research_payload = {
                    "executed": False,
                    "mode": research_mode,
                    "reason": f"research execution failed: {exc}",
                }
        elif should_research and not health["research_ready"]:
            research_payload = {
                "executed": False,
                "mode": research_mode,
                "reason": "research unavailable until health issues are resolved",
            }

        if health["degraded"]:
            recommended_next_step = "Use search results and working memory, then fall back to lexical-only investigation if needed."
        elif research_payload.get("executed"):
            recommended_next_step = (
                "Start from the returned research graph, then inspect the highest-scoring chunks."
            )
        elif task_classification == "planning":
            recommended_next_step = (
                "Use working memory plus top search hits to draft the engineering plan."
            )
        else:
            recommended_next_step = "Start with the top search hits, then escalate to research only if cross-file tracing is needed."

        # Auto-enrich with git context for files found in search
        git_context_payload = {}
        try:
            if search_hits and search_hits.get("matches"):
                hit_files = list({m["file_path"] for m in search_hits["matches"] if "file_path" in m})[:3]
                if hit_files:
                    git_context_payload = _compute_git_context(
                        context.workspace_root, hit_files, limit=3
                    )
        except Exception:
            pass  # Graceful — git context is supplementary

        return _ok(
            context=context,
            result={
                "task": task,
                "task_classification": task_classification,
                "health": health,
                "degraded": health["degraded"],
                "research_ready": health["research_ready"],
                "search_hits": search_hits,
                "memory_hits": memory_hits,
                "working_memory": working_memory,
                "research": research_payload,
                "git_context": git_context_payload,
                "recommended_next_step": recommended_next_step,
                "fallback_guidance": health["fallback_guidance"],
            },
        )

    @mcp.tool()
    def compact(
        workspace_root: str,
        index_dir: str | None = None,
        path: str | None = None,
        threshold: float = 0.2,
        force: bool = False,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        chunk_index_path = context.index_dir / "chunk_index.json"
        if not chunk_index_path.exists():
            raise ValueError("Chunk index not found. Run incremental indexing before compaction.")
        chunk_index = ChunkIndex(chunk_index_path)
        directory = (context.workspace_root / path).resolve() if path else context.workspace_root

        with index_lock(context.index_dir):
            with _backend_session(
                context, config, writable=True, suppress_stdout_notices=True
            ) as backend:
                coordinator = IndexingCoordinator(config, backend)
                stats = coordinator.compact_index(
                    directory, chunk_index, 0.0 if force else threshold
                )

        return _ok(
            context=context,
            result=stats,
            backend=config.storage.backend,
            embedding_runtime=_embedding_runtime(config),
        )

    @mcp.tool()
    def memory_sync_git(
        workspace_root: str,
        index_dir: str | None = None,
        since: str = "HEAD~100",
        limit: int = 50,
        dry_run: bool = False,
        tags_only: bool = False,
        merges_only: bool = False,
        min_importance: str = "low",
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with index_lock(context.index_dir):
            with _backend_session(context, config, writable=True) as backend:
                sync_service = GitSyncService(backend, context.workspace_root, config=config)
                stats = sync_service.sync(
                    since=since,
                    limit=limit,
                    dry_run=dry_run,
                    tags_only=tags_only,
                    merges_only=merges_only,
                    min_importance=min_importance,
                )
        return _ok(context=context, result=stats, embedding_runtime=_embedding_runtime(config))

    @mcp.tool()
    def memory_add_decision(
        workspace_root: str,
        title: str,
        description: str,
        index_dir: str | None = None,
        reasoning: str | None = None,
        alternatives: list[str] | None = None,
        link_files: list[str] | None = None,
        link_symbols: list[str] | None = None,
        link_timeline_refs: list[str] | None = None,
        link_changelog_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        alternative_records = [{"option": item} for item in (alternatives or [])]
        conceptual_links = (
            [{"type": "file", "ref": value} for value in (link_files or [])]
            + [{"type": "symbol", "ref": value} for value in (link_symbols or [])]
            + [{"type": "timeline", "ref": value} for value in (link_timeline_refs or [])]
            + [{"type": "changelog", "ref": value} for value in (link_changelog_tags or [])]
        )
        commit_hash, commit_time = get_git_commit_context(context.workspace_root)
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        with index_lock(context.index_dir):
            with _backend_session(context, config, writable=True) as backend:
                decision_id = backend.add_decision(
                    session_id=session_id,
                    title=title,
                    description=description,
                    reasoning=reasoning,
                    alternatives=alternative_records,
                    conceptual_links=conceptual_links,
                    commit_hash=commit_hash,
                    commit_time=commit_time,
                )
        return _ok(context=context, result={"decision_id": decision_id, "title": title})

    @mcp.tool()
    def memory_list(
        workspace_root: str,
        index_dir: str | None = None,
        item_type: str = "all",
        status: str = "all",
        limit: int = 10,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with _backend_session(context, config, writable=True) as backend:
            results = {"decisions": [], "timeline": [], "changelogs": []}
            if item_type in ("decision", "all"):
                decisions = backend.list_pending_decisions(
                    limit=limit if status == "pending" else limit * 2
                )
                if status not in {"all", "pending"}:
                    decisions = [item for item in decisions if item.status == status]
                results["decisions"] = [decision.to_dict() for decision in decisions]
            if item_type in ("timeline", "all"):
                results["timeline"] = [
                    event.to_dict() for event in backend.get_timeline_events(limit=limit)
                ]
            if item_type in ("changelog", "all"):
                results["changelogs"] = [
                    entry.to_dict() for entry in backend.get_changelogs(limit=limit)
                ]
        return _ok(context=context, result=results)

    @mcp.tool()
    def memory_approve(
        workspace_root: str,
        decision_id: int,
        category: str,
        index_dir: str | None = None,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with index_lock(context.index_dir):
            with _backend_session(context, config, writable=True) as backend:
                decision = backend.get_decision(decision_id)
                if decision is None:
                    raise ValueError(f"Decision {decision_id} not found")
                backend.approve_decision(decision_id, category)
        return _ok(context=context, result={"decision_id": decision_id, "category": category})

    @mcp.tool()
    def memory_reject(
        workspace_root: str,
        decision_id: int,
        index_dir: str | None = None,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with index_lock(context.index_dir):
            with _backend_session(context, config, writable=True) as backend:
                decision = backend.get_decision(decision_id)
                if decision is None:
                    raise ValueError(f"Decision {decision_id} not found")
                backend.reject_decision(decision_id)
        return _ok(context=context, result={"decision_id": decision_id})

    @mcp.tool()
    def memory_search(
        workspace_root: str,
        query: str,
        index_dir: str | None = None,
        search_type: str = "all",
        limit: int = 5,
        detail: str = "minimal",
    ) -> dict[str, Any]:
        """Search stored project memory for engineering context.

        Use this to recover prior decisions, changelogs, and timeline artifacts before
        planning or review work.
        """
        context, config = _require_initialized_context(workspace_root, index_dir)
        with _backend_session(context, config) as backend:
            results = backend.search_memory(query, k=limit)
        if search_type != "all":
            results = [item for item in results if item.chunk.metadata.get("type") == search_type]
        return _ok(
            context=context,
            result={
                "query": query,
                "matches": [_serialize_result(item, detail=detail) for item in results],
            },
            embedding_runtime=_embedding_runtime(config),
        )

    @mcp.tool()
    def memory_working_set(
        workspace_root: str,
        query: str,
        index_dir: str | None = None,
        agent: str | None = None,
        session_id: str | None = None,
        objective: str | None = None,
        current_focus: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build a compact working-memory payload for engineering planning and handoff.

        Use this when an engineering task needs query-scoped repo context rather than a
        raw memory search list.
        """
        context, config = _require_initialized_context(workspace_root, index_dir)
        augmented_query = _augment_query(query, objective, current_focus, constraints)
        with _backend_session(context, config, suppress_stdout_notices=True) as backend:
            payload = build_working_memory_payload(
                backend=backend,
                query=augmented_query,
                agent=agent,
                session_id=session_id,
                base_dir=context.workspace_root,
            )
        return _ok(context=context, result=payload, embedding_runtime=_embedding_runtime(config))

    @mcp.tool()
    def memory_trace(
        workspace_root: str,
        query: str,
        index_dir: str | None = None,
        hops: int = 1,
        seed_limit: int = 5,
        timeline_limit: int = 100,
        limit: int = 10,
        objective: str | None = None,
        current_focus: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        augmented_query = _augment_query(query, objective, current_focus, constraints)
        with _backend_session(context, config) as backend:
            tracer = CausalTracer(backend)
            result = tracer.trace(
                query=augmented_query,
                hops=max(0, hops),
                seed_limit=max(1, seed_limit),
                timeline_limit=max(1, timeline_limit),
                limit=max(1, limit),
            )
        payload = {
            "query": query,
            "seed_symbols": result.seed_symbols,
            "related_symbols": result.related_symbols,
            "related_files": result.related_files,
            "events": [
                {
                    "score": item.score,
                    "matched_files": item.matched_files,
                    "event": item.event.to_dict(),
                }
                for item in result.events
            ],
        }

        # Supplement with dynamic git history for related files
        dynamic_git = {}
        try:
            if result.related_files:
                dynamic_git = _compute_git_context(
                    context.workspace_root, result.related_files[:3],
                    limit=3, include_blast_radius=False,
                )
        except Exception:
            pass
        payload["dynamic_git"] = dynamic_git

        return _ok(context=context, result=payload, embedding_runtime=_embedding_runtime(config))

    @mcp.tool()
    def memory_timeline(
        workspace_root: str,
        index_dir: str | None = None,
        since: str | None = None,
        event_type: str | None = None,
        importance: str | None = None,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with _backend_session(context, config) as backend:
            events = backend.get_timeline_events(limit=100)
        if event_type:
            events = [item for item in events if item.event_type == event_type]
        if importance:
            events = [item for item in events if item.importance == importance]
        if since:
            since_date = datetime.fromisoformat(since)
            events = [item for item in events if item.created_at and item.created_at >= since_date]
        return _ok(context=context, result={"events": [event.to_dict() for event in events]})

    @mcp.tool()
    def memory_changelog(
        workspace_root: str,
        index_dir: str | None = None,
        range_spec: str | None = None,
        output_format: str = "markdown",
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with _backend_session(context, config) as backend:
            changelogs = backend.get_changelogs(limit=100)
        if range_spec and ".." in range_spec:
            start, end = range_spec.split("..", 1)
            changelogs = [item for item in changelogs if start <= (item.tag or "") <= end]
        if output_format == "json":
            content: Any = [item.to_dict() for item in changelogs]
        elif output_format == "text":
            content = "\n\n".join(
                f"{item.tag} ({item.date.strftime('%Y-%m-%d') if item.date else 'Unknown'})\n{item.summary}"
                for item in changelogs
            )
        else:
            lines = ["# Changelog\n"]
            for item in changelogs:
                date_str = item.date.strftime("%Y-%m-%d") if item.date else "Unknown"
                lines.append(f"## {item.tag} ({date_str})\n")
                if item.summary:
                    lines.append(f"{item.summary}\n")
            content = "\n".join(lines)
        return _ok(context=context, result={"format": output_format, "content": content})

    @mcp.tool()
    def memory_export(
        workspace_root: str,
        index_dir: str | None = None,
        include_pending: bool = True,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with _backend_session(context, config) as backend:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
                export_path = backend.export_memory(handle.name, include_pending=include_pending)
            payload = json.loads(Path(export_path).read_text())
        return _ok(context=context, result={"payload": payload})

    @mcp.tool()
    def memory_import(
        workspace_root: str,
        index_dir: str | None = None,
        payload: dict[str, Any] | None = None,
        input_path: str | None = None,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        with index_lock(context.index_dir):
            with _backend_session(context, config, writable=True) as backend:
                if payload is not None:
                    with tempfile.NamedTemporaryFile(
                        suffix=".json", delete=False, mode="w"
                    ) as handle:
                        json.dump(payload, handle)
                        temp_path = handle.name
                    result = backend.import_memory(temp_path)
                else:
                    result = backend.import_memory(
                        input_path or str(context.index_dir / "memory.json")
                    )
        return _ok(
            context=context,
            result={"added": result.added, "updated": result.updated, "skipped": result.skipped},
        )

    @mcp.tool()
    def config_show(workspace_root: str, index_dir: str | None = None) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        return _ok(context=context, result={"config": config.model_dump()})

    @mcp.tool()
    def config_path(workspace_root: str, index_dir: str | None = None) -> dict[str, Any]:
        context, _ = _require_initialized_context(workspace_root, index_dir)
        return _ok(
            context=context,
            result={"config_path": str((context.index_dir / "config.json").resolve())},
        )

    @mcp.tool()
    def config_get(workspace_root: str, key: str, index_dir: str | None = None) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        value: Any = config.model_dump()
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                raise ValueError(f"Unknown key: {key}")
        return _ok(context=context, result={"key": key, "value": value})

    @mcp.tool()
    def config_set(
        workspace_root: str,
        key: str,
        value: str,
        index_dir: str | None = None,
    ) -> dict[str, Any]:
        context, config = _require_initialized_context(workspace_root, index_dir)
        data = config.model_dump()
        _set_nested(data, key.split("."), _parse_config_value(value))
        new_config = Config.model_validate(data)
        with index_lock(context.index_dir):
            new_config.save(context.index_dir / "config.json")
        current_value: Any = new_config.model_dump()
        for part in key.split("."):
            current_value = current_value[part]
        return _ok(context=context, result={"key": key, "value": current_value})

    @mcp.tool()
    def embed_start(idle_timeout: int = 3600, foreground: bool = False, log: str | None = None):
        from .embed_server.daemon import start_daemon

        start_daemon(foreground=foreground, log_path=log, idle_timeout_seconds=idle_timeout)
        return _ok(scope="machine", result={"started": True, "idle_timeout": idle_timeout})

    @mcp.tool()
    def embed_status(verbose: bool = False):
        from .embed_server.daemon import daemon_status

        status = daemon_status()
        if not verbose and status.get("running"):
            health = status.get("health", {})
            status = {
                "running": True,
                "pid": status.get("pid"),
                "device": health.get("device"),
                "memory_mb": health.get("memory_mb"),
                "models_loaded": health.get("models_loaded", []),
            }
        return _ok(scope="machine", result=status)

    @mcp.tool()
    def embed_stop():
        from .embed_server.daemon import stop_daemon

        return _ok(scope="machine", result={"stopped": stop_daemon()})

    # ------------------------------------------------------------------
    # Dynamic Git Memory (consolidated)
    # ------------------------------------------------------------------

    def _compute_git_context(
        workspace_root: Path,
        file_paths: list[str],
        limit: int = 5,
        include_blast_radius: bool = True,
        include_narrative: bool = True,
    ) -> dict:
        """Shared helper — computes git context for files.

        Used by git_context tool, research, engineering_bootstrap, memory_trace.
        """
        from .config import Config
        from .memory.blast_radius import BlastRadiusAnalyzer
        from .memory.diff_analyzer import DiffSemanticAnalyzer
        from .memory.git_dynamic import GitDynamicMemory
        from .memory.intent_classifier import IntentClassifier
        from .memory.recency import RecencyConfig

        config = Config.load(workspace_root / ".sia-code" / "config.json")
        gc = config.git_dynamic

        if not gc.enabled:
            return {}

        recency_cfg = RecencyConfig(
            halflife_days=gc.recency_halflife_days,
            working_window_days=gc.working_window_days,
        )
        mem = GitDynamicMemory(workspace_root, recency_config=recency_cfg)
        classifier = IntentClassifier()

        results = {}
        for fp in file_paths[:limit]:
            # File history + revert detection + branch context
            hist = mem.file_history(fp, cross_branch=gc.cross_branch_enabled, limit=10)
            if not hist.effective_commits:
                continue

            # Classify intents
            for c in hist.effective_commits:
                intent = classifier.classify(
                    c.message, len(c.files_changed), c.insertions + c.deletions
                )
                c.intent = intent.intent

            entry: dict = {
                "effective_commits": [
                    {
                        "hash": c.hash[:7],
                        "message": c.message,
                        "author": c.author,
                        "date": c.date.isoformat(),
                        "recency_score": round(c.recency_score, 3),
                        "branch": c.branch,
                        "intent": c.intent,
                    }
                    for c in hist.effective_commits[:8]
                ],
                "owners": hist.owners[:3],
                "reverts": [
                    {
                        "reverted": r.reverted_hash[:7],
                        "by": r.reverting_hash[:7],
                        "method": r.matched_by,
                    }
                    for r in hist.reverts
                ],
                "branch_context": {
                    "current": hist.branch_context.current_branch if hist.branch_context else None,
                    "base": hist.branch_context.base_branch if hist.branch_context else None,
                    "merge_base": hist.branch_context.merge_base if hist.branch_context else None,
                },
            }

            # Blast radius
            if include_blast_radius:
                analyzer = BlastRadiusAnalyzer(
                    workspace_root,
                    lookback=gc.lookback_commits,
                    min_coupling=gc.coupling_threshold,
                    max_files_per_commit=gc.max_files_per_commit,
                    recency_config=recency_cfg,
                )
                radius = analyzer.co_changed_files(fp)
                entry["blast_radius"] = [
                    {
                        "path": cf.path,
                        "coupling": round(cf.coupling_score, 3),
                        "co_changes": cf.co_change_count,
                    }
                    for cf in radius.coupled_files[:10]
                ]
                if radius.change_clusters:
                    entry["clusters"] = [
                        {"files": cl.files, "cohesion": round(cl.cohesion_score, 3)}
                        for cl in radius.change_clusters
                    ]

            # Evolution narrative (auto-uses local model if available)
            if include_narrative:
                try:
                    diff_analyzer = DiffSemanticAnalyzer(
                        workspace_root, model_name=gc.narrative_model
                    )
                    narrative = diff_analyzer.summarize_evolution(hist)
                    entry["narrative"] = narrative.narrative
                    entry["phases"] = narrative.key_phases
                    entry["model_used"] = narrative.model_used
                except Exception:
                    pass  # Graceful degradation

            results[fp] = entry

        return results

    @mcp.tool()
    def git_context(
        workspace_root: str,
        file_paths: list[str],
        include_blast_radius: bool = True,
        include_narrative: bool = True,
    ) -> dict:
        """Git-aware context for files: history, blast radius, evolution narrative.

        Combines file history (revert-aware, cross-branch, recency-scored),
        co-change blast radius, and model-generated evolution narrative.
        Auto-uses local flan-t5 model for narrative when available.
        """
        result = _compute_git_context(
            Path(workspace_root),
            file_paths,
            include_blast_radius=include_blast_radius,
            include_narrative=include_narrative,
        )
        return _ok(scope="project", result=result)

    return mcp


def main() -> None:
    if FastMCP is None:
        raise SystemExit("MCP support requires installing 'sia-code[mcp]'")
    build_server().run("stdio")


if __name__ == "__main__":
    main()
