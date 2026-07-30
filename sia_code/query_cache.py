"""Disk-backed query->result cache for MCP search/research tools.

The backend already has an in-memory `_search_cache`, but `_backend_session`
creates a fresh backend per tool call, so that cache never hits across calls.
This module provides a process-wide, disk-backed cache keyed by
(workspace, query, mode, k, detail, index_signature) with a TTL, invalidated
automatically when the index changes (last_indexed timestamp + chunk count).

Results are stored as the already-serialized JSON string of the `_ok` payload,
so a cache hit skips both the backend search and the serialization work.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _cache_path(index_dir: Path) -> Path:
    return index_dir / "cache" / "query_cache.sqlite"


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=2.0)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS query_cache (
            key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL,
            index_sig TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _signature(stats_payload: dict[str, Any] | None) -> str:
    if not stats_payload:
        return "unknown"
    return f"{stats_payload.get('last_indexed', '')}|{stats_payload.get('total_chunks', stats_payload.get('chunks', 0))}"


def _cache_key(
    *,
    workspace_root: str,
    index_dir: str,
    tool: str,
    query: str,
    mode: str,
    k: int,
    detail: str,
    extra: str = "",
) -> str:
    raw = json.dumps(
        [workspace_root, index_dir, tool, query, mode, k, detail, extra],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(
    index_dir: Path,
    *,
    workspace_root: str,
    tool: str,
    query: str,
    mode: str,
    k: int,
    detail: str,
    stats_payload: dict[str, Any] | None,
    ttl_seconds: int,
    extra: str = "",
) -> dict[str, Any] | None:
    """Return a cached serialized payload, or None on miss/stale."""
    try:
        conn = _open(_cache_path(index_dir))
    except sqlite3.Error:
        return None
    try:
        key = _cache_key(
            workspace_root=workspace_root,
            index_dir=str(index_dir),
            tool=tool,
            query=query,
            mode=mode,
            k=k,
            detail=detail,
            extra=extra,
        )
        sig = _signature(stats_payload)
        row = conn.execute(
            "SELECT payload, created_at, index_sig FROM query_cache WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        payload, created_at, stored_sig = row
        if stored_sig != sig:
            return None
        if (time.time() - created_at) > ttl_seconds:
            return None
        return json.loads(payload)
    except (sqlite3.Error, json.JSONDecodeError):
        return None
    finally:
        conn.close()


def put(
    index_dir: Path,
    *,
    workspace_root: str,
    tool: str,
    query: str,
    mode: str,
    k: int,
    detail: str,
    stats_payload: dict[str, Any] | None,
    payload: dict[str, Any],
    extra: str = "",
) -> None:
    """Store a serialized payload. Best-effort: errors are swallowed."""
    try:
        conn = _open(_cache_path(index_dir))
        try:
            key = _cache_key(
                workspace_root=workspace_root,
                index_dir=str(index_dir),
                tool=tool,
                query=query,
                mode=mode,
                k=k,
                detail=detail,
                extra=extra,
            )
            conn.execute(
                "INSERT OR REPLACE INTO query_cache (key, payload, created_at, index_sig) VALUES (?, ?, ?, ?)",
                (
                    key,
                    json.dumps(payload, separators=(",", ":")),
                    time.time(),
                    _signature(stats_payload),
                ),
            )
            conn.commit()
            # ponytail: unbounded growth guard — evict oldest 200 rows when table exceeds 2000.
            n = conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]
            if n > 2000:
                conn.execute(
                    "DELETE FROM query_cache WHERE key IN ("
                    "SELECT key FROM query_cache ORDER BY created_at ASC LIMIT 200)"
                )
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return