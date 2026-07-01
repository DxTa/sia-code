"""SQLite-vec + SQLite FTS5 storage backend for code and memory."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..core.models import (
    ChangelogEntry,
    Chunk,
    CodeRelationshipRecord,
    Decision,
    ImportResult,
    IndexStats,
    SearchResult,
    TimelineEvent,
)
from ..core.types import ChunkType, Language
from .base import StorageBackend
from .sqlite_runtime import connect_sqlite


class _MemoryAdapter:
    """Compatibility adapter for legacy mem interface."""

    def __init__(self, backend: "SqliteVecBackend") -> None:
        self._backend = backend

    def put(
        self,
        title: str,
        label: str,
        metadata: dict[str, Any],
        text: str,
        uri: str,
    ) -> None:
        chunk_type = (
            ChunkType(label) if label in ChunkType._value2member_map_ else ChunkType.UNKNOWN
        )
        language_value = metadata.get("language", Language.UNKNOWN.value)
        language = (
            Language(language_value)
            if language_value in Language._value2member_map_
            else Language.UNKNOWN
        )
        parsed_path, parsed_start, parsed_end = self._backend._parse_uri(uri)
        start_line = int(metadata.get("start_line", parsed_start))
        end_line = int(metadata.get("end_line", parsed_end))
        file_path = Path(metadata.get("file_path", parsed_path))

        chunk = Chunk(
            symbol=title,
            start_line=start_line,
            end_line=end_line,
            code=text,
            chunk_type=chunk_type,
            language=language,
            file_path=file_path,
            metadata={**metadata, "uri": uri},
        )
        self._backend.store_chunks_batch([chunk])


class SqliteVecBackend(StorageBackend):
    """Storage backend using sqlite-vec + SQLite (FTS5).

    File structure:
    - .sia-code/index.db: SQLite database with FTS5 + vectors

    Features:
    - sqlite-vec vector index stored inside SQLite
    - Optional fallback to pure SQLite when sqlite-vec isn't available
    - Unified index (code + memory)
    - Full-text search with FTS5
    - Hybrid search with RRF
    - Decision workflow with FIFO
    - Git timeline integration
    - Import/export for collaboration
    """

    def __init__(
        self,
        path: Path,
        embedding_enabled: bool = True,
        embedding_model: str = "BAAI/bge-base-en-v1.5",
        ndim: int = 768,
        **kwargs,
    ):
        """Initialize sqlite-vec + SQLite backend.

        Args:
            path: Path to .sia-code directory
            embedding_enabled: Whether to enable embeddings
            embedding_model: Embedding model name (e.g., 'bge-small')
            ndim: Embedding dimensionality
            **kwargs: Additional configuration
        """
        super().__init__(path, **kwargs)
        self.embedding_enabled = embedding_enabled
        self.embedding_model = embedding_model
        self.ndim = ndim
        self.embedding_granularity = kwargs.pop("embedding_granularity", "chunk")
        self.max_vectors_per_file = int(kwargs.pop("max_vectors_per_file", 0) or 0)
        self.semantic_chunk_types = set(kwargs.pop("semantic_chunk_types", []) or [])
        self.persistent_embedding_cache = bool(kwargs.pop("persistent_embedding_cache", True))
        self.flan_query_rewrite = bool(kwargs.pop("flan_query_rewrite", False))

        # Paths
        self.db_path = self.path / "index.db"

        # Will be initialized in create_index() or open_index()
        self.conn: sqlite3.Connection | None = None
        self._embedder = None  # Lazy-loaded embedding model
        self._vector_table_initialized = False
        self._using_vec_extension = False
        self._vec_extension_error: Exception | None = None

        # Thread-local storage for parallel search
        import threading

        self._local = threading.local()

        # Search result cache
        self._search_cache: dict[str, list] | None = None
        self._search_cache_enabled = False

        # Persistent embedding cache (global across repos/runs)
        self._embedding_cache_conn: sqlite3.Connection | None = None

        self.mem = _MemoryAdapter(self)

        # Vector key prefixes for unified index
        self.KEY_PREFIX_CHUNK = "chunk:"
        self.KEY_PREFIX_TIMELINE = "timeline:"
        self.KEY_PREFIX_CHANGELOG = "changelog:"
        self.KEY_PREFIX_DECISION = "decision:"
        self.KEY_PREFIX_MEMORY = "memory:"

        # Vector ID offsets to avoid collisions with chunk IDs
        self.DECISION_OFFSET = 1_000_000
        self.TIMELINE_OFFSET = 2_000_000
        self.CHANGELOG_OFFSET = 3_000_000

    def _parse_uri(self, uri: str) -> tuple[str, int, int]:
        """Parse pci:// URIs into path and line numbers."""
        if not uri.startswith("pci://"):
            return ("unknown", 1, 1)

        remainder = uri[len("pci://") :]
        if not remainder:
            return ("unknown", 1, 1)

        if "#" in remainder:
            path_part, line_part = remainder.split("#", 1)
        else:
            path_part, line_part = remainder, ""

        if not path_part:
            path_part = "unknown"

        start = end = 1
        if line_part:
            if "-" in line_part:
                start_str, end_str = line_part.split("-", 1)
            else:
                start_str, end_str = line_part, line_part
            try:
                start = int(start_str)
                end = int(end_str)
            except ValueError:
                start = end = 1

        return (path_part, start, end)

    @staticmethod
    def _load_json_list(raw_value: str | None) -> list[dict[str, Any]]:
        """Decode a JSON list column, defaulting to an empty list."""
        return json.loads(raw_value) if raw_value else []

    @staticmethod
    def _render_conceptual_links(conceptual_links: list[dict[str, Any]]) -> str:
        """Render conceptual links into approved-memory markdown text."""
        if not conceptual_links:
            return ""

        rendered = "\n\nConceptual links:\n"
        for link in conceptual_links:
            link_type = link.get("type", "artifact")
            ref = link.get("ref", "")
            rationale = link.get("rationale")
            if rationale:
                rendered += f"- [{link_type}] {ref} ({rationale})\n"
            else:
                rendered += f"- [{link_type}] {ref}\n"
        return rendered

    @staticmethod
    def _row_to_code_relationship(row: sqlite3.Row) -> CodeRelationshipRecord:
        """Convert a database row into a code relationship record."""
        return CodeRelationshipRecord(
            id=row["id"],
            from_entity=row["from_entity"],
            to_entity=row["to_entity"],
            relationship_type=row["relationship_type"],
            from_chunk_id=row["from_chunk_id"] or None,
            to_chunk_id=row["to_chunk_id"] or None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    def _load_vec_extension(self, conn: sqlite3.Connection) -> bool:
        """Try to load sqlite-vec extension."""
        if not self.embedding_enabled:
            return False
        if self._using_vec_extension or self._vec_extension_error is not None:
            return self._using_vec_extension

        try:
            import sqlite_vec  # type: ignore

            conn.enable_load_extension(True)
            if hasattr(sqlite_vec, "load"):
                sqlite_vec.load(conn)
            else:
                conn.load_extension(sqlite_vec.__file__)
            self._using_vec_extension = True
            return True
        except Exception as exc:
            self._vec_extension_error = exc
            self._using_vec_extension = False
            return False

    def _ensure_vector_table(self) -> None:
        """Ensure vector table exists (sqlite-vec or fallback)."""
        if self.conn is None:
            raise RuntimeError("Database connection not initialized")
        if not self.embedding_enabled or self._vector_table_initialized:
            return

        cursor = self.conn.cursor()
        use_vec = self._load_vec_extension(self.conn)

        if use_vec:
            cursor.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0(
                    embedding float[{self.ndim}]
                )
            """
            )
        else:
            import logging

            logger = logging.getLogger(__name__)
            if not getattr(SqliteVecBackend, "_sqlite_vec_warned", False):
                logger.warning(
                    "sqlite-vec extension not available; falling back to brute-force vector search."
                )
                SqliteVecBackend._sqlite_vec_warned = True
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS vectors (
                    id INTEGER PRIMARY KEY,
                    embedding BLOB NOT NULL
                )
            """
            )

        self.conn.commit()
        self._vector_table_initialized = True

    def _serialize_vector(self, vector: np.ndarray) -> bytes:
        """Serialize a vector for sqlite-vec or fallback storage."""
        array = np.asarray(vector, dtype=np.float32)
        if self._using_vec_extension:
            try:
                import sqlite_vec  # type: ignore

                if hasattr(sqlite_vec, "serialize"):
                    return sqlite_vec.serialize(array)
            except Exception:
                pass
        return array.tobytes()

    def _vector_insert(self, vector_id: int, vector: np.ndarray) -> None:
        """Insert or replace a vector embedding."""
        self._vector_insert_many([(vector_id, vector)])

    def _vector_insert_many(self, items: list[tuple[int, np.ndarray]]) -> None:
        """Bulk insert or replace vector embeddings."""
        if self.conn is None:
            raise RuntimeError("Database connection not initialized")
        if not self.embedding_enabled or not items:
            return
        self._ensure_vector_table()
        cursor = self.conn.cursor()
        rows = [(vector_id, self._serialize_vector(vector)) for vector_id, vector in items]
        if self._using_vec_extension:
            # sqlite-vec virtual table path is safer with row-wise inserts.
            # executemany + OR REPLACE can hit primary-key issues on some builds.
            for row in rows:
                cursor.execute(
                    "INSERT OR REPLACE INTO vectors(rowid, embedding) VALUES (?, ?)",
                    row,
                )
        else:
            cursor.executemany(
                "INSERT OR REPLACE INTO vectors(id, embedding) VALUES (?, ?)",
                rows,
            )

    def _vector_search(self, query_vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        """Search vectors, returning list of (id, score)."""
        if self.conn is None:
            raise RuntimeError("Database connection not initialized")
        if not self.embedding_enabled:
            return []
        self._ensure_vector_table()

        cursor = self.conn.cursor()
        if self._using_vec_extension:
            payload = self._serialize_vector(query_vector)
            cursor.execute(
                """
                SELECT rowid, distance
                FROM vectors
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
            """,
                (payload, k),
            )
            rows = cursor.fetchall()
            return [(str(row[0]), 1.0 - float(row[1])) for row in rows]

        cursor.execute("SELECT id, embedding FROM vectors")
        rows = cursor.fetchall()
        if not rows:
            return []

        query = np.asarray(query_vector, dtype=np.float32).flatten()
        query_norm = np.linalg.norm(query) or 1.0
        scored = []
        for row in rows:
            vec = np.frombuffer(row[1], dtype=np.float32)
            if vec.size != query.size:
                continue
            denom = (np.linalg.norm(vec) or 1.0) * query_norm
            score = float(np.dot(vec, query) / denom)
            scored.append((str(row[0]), score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]

    def _get_embedder(self):
        """Lazy-load the embedding model with GPU if available.

        Tries to use embedding daemon first for better performance and memory sharing.
        Falls back to local model if daemon is not available.
        """
        if self._embedder is None:
            import logging

            logger = logging.getLogger(__name__)

            # Try embedding daemon first (fast path with model sharing)
            try:
                from ..embed_server.client import EmbedClient

                if EmbedClient.is_available():
                    self._embedder = EmbedClient(model_name=self.embedding_model)
                    logger.info(f"Using embedding daemon for {self.embedding_model}")
                    return self._embedder
            except Exception as e:
                logger.debug(f"Embedding daemon not available: {e}")

            # Auto-start daemon if not running (keeps model warm across repos)
            if self._try_auto_start_daemon():
                try:
                    from ..embed_server.client import EmbedClient

                    self._embedder = EmbedClient(model_name=self.embedding_model)
                    logger.info(
                        f"Auto-started embedding daemon for {self.embedding_model}"
                    )
                    return self._embedder
                except Exception as e:
                    logger.debug(f"Auto-started daemon not usable: {e}")

            # Fallback to local model (current behavior)
            from sentence_transformers import SentenceTransformer
            import torch

            # Auto-detect device (GPU if available, CPU fallback)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"

            self._embedder = SentenceTransformer(self.embedding_model, device=device)

            # Log device for debugging
            logger.info(f"Loaded local {self.embedding_model} on {device.upper()}")

        return self._embedder

    def _try_auto_start_daemon(self) -> bool:
        """Auto-start embedding daemon in background if not running.

        Returns True if daemon started successfully and is reachable.
        """
        import logging

        logger = logging.getLogger(__name__)
        try:
            from ..embed_server.client import EmbedClient
            import subprocess
            import sys
            import time

            logger.info("Auto-starting embedding daemon...")
            # Start daemon as separate process (NOT in-process fork which calls sys.exit)
            python_exe = sys.executable
            subprocess.Popen(
                [python_exe, "-c",
                 "from sia_code.embed_server.daemon import start_daemon; "
                 "start_daemon(foreground=True)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            # Wait for socket to become available (up to 10s for model load)
            for _ in range(100):
                if EmbedClient.is_available():
                    return True
                time.sleep(0.1)

            logger.debug("Daemon started but socket not reachable within timeout")
            return False
        except Exception as e:
            logger.debug(f"Failed to auto-start daemon: {e}")
            return False

    def _get_thread_conn(self) -> sqlite3.Connection:
        """Get thread-local SQLite connection for parallel operations.

        Returns:
            Thread-safe SQLite connection
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            # Create new connection for this thread
            conn = connect_sqlite(self.db_path, check_same_thread=False)
            self._local.conn = conn
        return self._local.conn

    def _get_embedding_cache_conn(self) -> sqlite3.Connection | None:
        """Open persistent embedding cache DB shared across repos/runs."""
        if not self.persistent_embedding_cache:
            return None
        if self._embedding_cache_conn is not None:
            return self._embedding_cache_conn
        try:
            cache_dir = Path.home() / ".cache" / "sia-code"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_db = cache_dir / "embedding-cache.sqlite3"
            conn = sqlite3.connect(cache_db)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings_cache (
                    cache_key TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_cache_model ON embeddings_cache(model)"
            )
            conn.commit()
            self._embedding_cache_conn = conn
            return conn
        except Exception:
            return None

    def _embedding_cache_key(self, text: str) -> str:
        """Stable key for persistent embedding cache."""
        import hashlib

        return hashlib.sha256(f"{self.embedding_model}\0{text}".encode("utf-8")).hexdigest()

    def _get_cached_embeddings(self, texts: list[str]) -> tuple[dict[int, np.ndarray], list[int], list[str]]:
        """Return cached embeddings plus list of misses preserving order."""
        conn = self._get_embedding_cache_conn()
        if conn is None or not texts:
            return {}, list(range(len(texts))), list(texts)

        keys = [self._embedding_cache_key(t) for t in texts]
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT cache_key, embedding FROM embeddings_cache WHERE cache_key IN ({placeholders})",
            keys,
        ).fetchall()
        cached = {
            row[0]: np.frombuffer(row[1], dtype=np.float32).copy() for row in rows
        }
        hit_map: dict[int, np.ndarray] = {}
        miss_idx: list[int] = []
        miss_texts: list[str] = []
        for i, key in enumerate(keys):
            vec = cached.get(key)
            if vec is None:
                miss_idx.append(i)
                miss_texts.append(texts[i])
            else:
                hit_map[i] = vec
        return hit_map, miss_idx, miss_texts

    def _put_cached_embeddings(self, texts: list[str], vectors: np.ndarray) -> None:
        """Persist newly computed embeddings."""
        conn = self._get_embedding_cache_conn()
        if conn is None or not texts:
            return
        rows = [
            (
                self._embedding_cache_key(text),
                self.embedding_model,
                np.asarray(vec, dtype=np.float32).tobytes(),
            )
            for text, vec in zip(texts, vectors, strict=False)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings_cache(cache_key, model, embedding) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()

    def _embed(self, text: str) -> np.ndarray | None:
        """Embed text to vector with caching.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as numpy array, or None if embeddings disabled
        """
        if not self.embedding_enabled:
            return None

        # Use cached version to avoid re-embedding same text
        cached_result = self._embed_cached(text)
        if cached_result is not None:
            return np.array(cached_result)
        return None

    def _embed_cached(self, text: str) -> tuple | None:
        """Cached embedding with LRU cache.

        Returns tuple instead of ndarray for hashability (cache requirement).
        """
        from functools import lru_cache

        # Create cache on first call
        if not hasattr(self, "_embedding_cache"):

            @lru_cache(maxsize=1000)
            def cached_encode(text: str) -> tuple:
                embedder = self._get_embedder()
                vector = embedder.encode(text, convert_to_numpy=True)
                return tuple(vector.tolist())

            self._embedding_cache = cached_encode

        return self._embedding_cache(text)

    def _get_embed_batch_size(self) -> int:
        """Compute embedding batch size based on host capacity."""
        if getattr(self, "_embed_batch_size", None):
            return self._embed_batch_size

        import os

        try:
            import psutil

            mem_bytes = psutil.virtual_memory().total
            mem_gb = mem_bytes / (1024**3)
        except Exception:
            mem_gb = 8.0

        if mem_gb < 6:
            mem_based = 8
        elif mem_gb < 12:
            mem_based = 16
        elif mem_gb < 24:
            mem_based = 32
        elif mem_gb < 48:
            mem_based = 96
        else:
            mem_based = 128

        cpu_count = os.cpu_count() or 2
        max_by_cpu = max(8, cpu_count * 8)
        size = min(mem_based, max_by_cpu)
        size = max(8, min(128, size))

        self._embed_batch_size = int(size)
        return self._embed_batch_size

    def _embed_batch(self, texts: list[str]) -> np.ndarray | None:
        """Embed a batch of texts to vectors.

        Args:
            texts: List of texts to embed

        Returns:
            Array of embedding vectors, or None if embeddings disabled
        """
        if not self.embedding_enabled:
            return None
        if not texts:
            return np.empty((0, self.ndim), dtype=np.float32)

        # Persistent cache across runs/rebuilds
        hit_map, miss_idx, miss_texts = self._get_cached_embeddings(texts)
        out = np.empty((len(texts), self.ndim), dtype=np.float32)
        for i, vec in hit_map.items():
            out[i] = vec

        if miss_texts:
            batch_size = self._get_embed_batch_size()
            encoded = []
            for idx in range(0, len(miss_texts), batch_size):
                batch = miss_texts[idx : idx + batch_size]
                last_error = None
                for attempt in range(2):
                    embedder = self._get_embedder()
                    try:
                        vectors = embedder.encode(
                            batch,
                            batch_size=batch_size,
                            show_progress_bar=False,
                            convert_to_numpy=True,
                        )
                        encoded.append(np.asarray(vectors, dtype=np.float32))
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        # Daemon may have died after availability check; force re-resolve once.
                        self._embedder = None
                if last_error is not None:
                    raise last_error
            new_vectors = encoded[0] if len(encoded) == 1 else np.vstack(encoded)
            self._put_cached_embeddings(miss_texts, new_vectors)
            for pos, vec in zip(miss_idx, new_vectors, strict=False):
                out[pos] = vec

        return out

    def _make_chunk_key(self, chunk_id: int) -> str:
        """Create vector index key for chunk."""
        return f"{self.KEY_PREFIX_CHUNK}{chunk_id}"

    def _make_decision_key(self, decision_id: int) -> str:
        """Create vector index key for decision."""
        return f"{self.KEY_PREFIX_DECISION}{decision_id}"

    def _sanitize_fts5_query(self, query: str) -> str:
        """Extract FTS5-safe tokens from code query.

        Handles special characters in code (., #, (), "", etc.) by extracting
        only alphanumeric identifiers and joining with OR for broader matching.

        Args:
            query: Raw query text (may contain code)

        Returns:
            FTS5-safe query string
        """
        import re

        # Extract alphanumeric identifiers (function names, variables, classes)
        tokens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", query)

        if not tokens:
            # Fallback to empty phrase if no valid tokens
            return '""'

        # Remove duplicates while preserving order, case-insensitive
        seen = set()
        unique = []
        for t in tokens:
            t_lower = t.lower()
            if t_lower not in seen:
                seen.add(t_lower)
                unique.append(t)

        # Add trailing wildcards for prefix matching (e.g., "Serv" matches "Service")
        # Note: FTS5 doesn't support leading wildcards, so "*Service" won't work
        # Limit to 20 tokens for performance, join with OR for broader matching
        wildcarded = [f"{t}*" if len(t) >= 3 else t for t in unique[:20]]
        return " OR ".join(wildcarded)

    def _make_timeline_key(self, timeline_id: int) -> str:
        """Create vector index key for timeline."""
        return f"{self.KEY_PREFIX_TIMELINE}{timeline_id}"

    def _make_changelog_key(self, changelog_id: int) -> str:
        """Create vector index key for changelog."""
        return f"{self.KEY_PREFIX_CHANGELOG}{changelog_id}"

    def _parse_vector_key(self, key: str) -> tuple[str, int]:
        """Parse vector key into type and ID.

        Returns:
            Tuple of (type, id) where type is 'chunk', 'decision', etc.
        """
        for prefix in [
            self.KEY_PREFIX_CHUNK,
            self.KEY_PREFIX_TIMELINE,
            self.KEY_PREFIX_CHANGELOG,
            self.KEY_PREFIX_DECISION,
            self.KEY_PREFIX_MEMORY,
        ]:
            if key.startswith(prefix):
                type_name = prefix.rstrip(":")
                id_str = key[len(prefix) :]
                return (type_name, int(id_str))
        raise ValueError(f"Invalid vector key: {key}")

    # ===================================================================
    # Index Lifecycle
    # ===================================================================

    def create_index(self) -> None:
        """Create a new index (vectors + SQLite)."""
        self.path.mkdir(parents=True, exist_ok=True)

        # Create SQLite database (check_same_thread=False for parallel search)
        self.conn = connect_sqlite(self.db_path, check_same_thread=False)
        self._vector_table_initialized = False
        self._create_tables()

        # Ensure vector table exists when embeddings are enabled
        self._ensure_vector_table()

    def open_index(self, writable: bool = False) -> None:
        """Open an existing index.

        Args:
            writable: Open in read-write mode when True.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        # Open SQLite database (check_same_thread=False for parallel search)
        self.conn = connect_sqlite(self.db_path, check_same_thread=False)
        self._vector_table_initialized = False

        # Ensure schema is up to date for older indexes
        self._create_tables()
        if writable:
            self._ensure_vector_table()

    def close(self) -> None:
        """Close the index and save changes."""
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None
        if self._embedding_cache_conn is not None:
            self._embedding_cache_conn.commit()
            self._embedding_cache_conn.close()
            self._embedding_cache_conn = None

    def seal(self) -> None:
        """Seal the index to finalize WAL and reduce storage.

        For SQLite, this commits pending transactions and optimizes the database.
        """
        if self.conn is not None:
            try:
                self.conn.commit()
                # Optional: VACUUM to reclaim space (can be slow on large DBs)
                # self.conn.execute("VACUUM")
            except Exception as e:
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to seal index: {e}")

    def _create_tables(self) -> None:
        """Create all SQLite tables."""
        if self.conn is None:
            raise RuntimeError("Database connection not initialized")

        cursor = self.conn.cursor()

        def ensure_column(table: str, column: str, column_type: str) -> None:
            cursor.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in cursor.fetchall()}
            if column not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

        # Code chunks table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uri TEXT UNIQUE,
                symbol TEXT,
                chunk_type TEXT,
                file_path TEXT,
                start_line INTEGER,
                end_line INTEGER,
                language TEXT,
                code TEXT,
                metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # FTS5 for code search
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                symbol, code, content=chunks, content_rowid=id
            )
        """
        )

        # Triggers to keep FTS5 in sync
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, symbol, code) 
                VALUES (new.id, new.symbol, new.code);
            END
        """
        )

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                DELETE FROM chunks_fts WHERE rowid = old.id;
            END
        """
        )

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                DELETE FROM chunks_fts WHERE rowid = old.id;
                INSERT INTO chunks_fts(rowid, symbol, code) 
                VALUES (new.id, new.symbol, new.code);
            END
        """
        )

        # Index for file path queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_file_path ON chunks(file_path)")

        # Persisted code relationships for graph-aware research
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS code_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_entity TEXT NOT NULL,
                to_entity TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                from_chunk_id TEXT NOT NULL DEFAULT '',
                to_chunk_id TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(from_entity, to_entity, relationship_type, from_chunk_id, to_chunk_id)
            )
        """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_code_relationships_from ON code_relationships(from_entity)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_code_relationships_to ON code_relationships(to_entity)"
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_code_relationships_type
            ON code_relationships(relationship_type)
        """
        )

        # Timeline events table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS timeline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                from_ref TEXT,
                to_ref TEXT,
                summary TEXT,
                files_changed JSON,
                diff_stats JSON,
                importance TEXT DEFAULT 'medium',
                commit_hash TEXT,
                commit_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Changelogs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS changelogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT UNIQUE,
                version TEXT,
                date TIMESTAMP,
                summary TEXT,
                breaking_changes JSON,
                features JSON,
                fixes JSON,
                commit_hash TEXT,
                commit_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Decisions table (pending, max 100 with FIFO)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                title TEXT,
                description TEXT,
                reasoning TEXT,
                alternatives JSON,
                conceptual_links JSON,
                status TEXT DEFAULT 'pending',
                category TEXT,
                commit_hash TEXT,
                commit_time TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP
            )
        """
        )

        # Backward-compatible schema upgrades
        ensure_column("timeline", "commit_hash", "TEXT")
        ensure_column("timeline", "commit_time", "TIMESTAMP")
        ensure_column("changelogs", "commit_hash", "TEXT")
        ensure_column("changelogs", "commit_time", "TIMESTAMP")
        ensure_column("decisions", "commit_hash", "TEXT")
        ensure_column("decisions", "commit_time", "TIMESTAMP")
        ensure_column("decisions", "conceptual_links", "JSON")

        # FIFO trigger for decisions (delete oldest when >100 pending)
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS decisions_fifo 
            AFTER INSERT ON decisions
            WHEN (SELECT COUNT(*) FROM decisions WHERE status = 'pending') > 100
            BEGIN
                DELETE FROM decisions 
                WHERE id = (
                    SELECT id FROM decisions 
                    WHERE status = 'pending' 
                    ORDER BY created_at ASC 
                    LIMIT 1
                );
            END
        """
        )

        # Approved memory table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS approved_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER REFERENCES decisions(id),
                category TEXT,
                title TEXT,
                content TEXT,
                approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # FTS5 for memory search (decisions + approved memory)
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                title, description, content
            )
        """
        )

        self.conn.commit()

    # The rest of the methods will be added in subsequent parts...

    # ===================================================================
    # Code Operations
    # ===================================================================

    def _select_semantic_embedding_indices(self, chunks: list[Chunk]) -> list[int]:
        """Select which chunks should receive semantic vectors.

        All chunks are still stored lexically/FTS. This only reduces vector count
        for heavy repo profiles. Selection is type-prioritized within each file.
        """
        if self.embedding_granularity != "budget" or self.max_vectors_per_file <= 0:
            return list(range(len(chunks)))

        allowed_types = self.semantic_chunk_types or {"class", "function", "method", "definition"}
        type_priority = {
            "class": 100,
            "method": 95,
            "function": 90,
            "definition": 88,
            "structure": 60,
            "import": 45,
            "block": 35,
            "call": 25,
            "docstring": 18,
            "comment": 10,
            "unknown": 5,
        }

        def _chunk_score(ch: Chunk) -> tuple[int, int, int, int]:
            t = getattr(ch.chunk_type, "value", str(ch.chunk_type))
            base = type_priority.get(t, 0)
            if t in allowed_types:
                base += 1000
            symbol = (ch.symbol or "").lower()
            if any(k in symbol for k in ("view", "viewset", "api", "handler", "endpoint", "trainer", "process", "service", "serializer", "model")):
                base += 20
            span = max(0, int(ch.end_line) - int(ch.start_line))
            return (base, min(span, 400), 1 if ch.parent_header else 0, -int(ch.start_line))

        by_file: dict[str, list[tuple[int, Chunk]]] = {}
        for i, chunk in enumerate(chunks):
            by_file.setdefault(str(chunk.file_path), []).append((i, chunk))

        selected: list[int] = []
        for items in by_file.values():
            ranked = sorted(items, key=lambda it: _chunk_score(it[1]), reverse=True)
            chosen = [idx for idx, _ in ranked[: self.max_vectors_per_file]]
            selected.extend(chosen)
        return sorted(selected)

    def store_chunks_batch(self, chunks: list[Chunk]) -> list[str]:
        """Store multiple code chunks.

        Args:
            chunks: List of code chunks to store

        Returns:
            List of chunk IDs (as strings)
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        chunk_ids: list[int] = []

        # Phase 1: preserve stable IDs on conflict without REPLACE row churn
        for chunk in chunks:
            uri = f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
            cursor.execute("SELECT id FROM chunks WHERE uri = ?", (uri,))
            row = cursor.fetchone()

            if row is None:
                cursor.execute(
                    """
                    INSERT INTO chunks (
                        uri, symbol, chunk_type, file_path, start_line, end_line, language, code, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        uri,
                        chunk.symbol,
                        chunk.chunk_type.value,
                        str(chunk.file_path),
                        chunk.start_line,
                        chunk.end_line,
                        chunk.language.value,
                        chunk.code,
                        json.dumps(chunk.metadata),
                    ),
                )
                chunk_id = int(cursor.lastrowid)
            else:
                chunk_id = int(row[0])
                cursor.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
                cursor.execute(
                    """
                    INSERT INTO chunks (
                        id, uri, symbol, chunk_type, file_path, start_line, end_line, language, code, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        chunk_id,
                        uri,
                        chunk.symbol,
                        chunk.chunk_type.value,
                        str(chunk.file_path),
                        chunk.start_line,
                        chunk.end_line,
                        chunk.language.value,
                        chunk.code,
                        json.dumps(chunk.metadata),
                    ),
                )

            chunk_ids.append(chunk_id)

        # Phase 2: Batch-embed selected chunks only (all chunks remain lexically searchable)
        if self.embedding_enabled and chunk_ids:
            try:
                selected_indices = self._select_semantic_embedding_indices(chunks)
                if selected_indices:
                    embed_texts = [
                        f"{chunks[idx].symbol}\n\n{chunks[idx].code}" for idx in selected_indices
                    ]
                    vectors = self._embed_batch(embed_texts)
                    if vectors is not None:
                        # Dedupe by chunk_id in case multiple chunks in the batch
                        # resolve to the same stable URI/id.
                        vector_map = {
                            int(chunk_ids[idx]): vectors[pos]
                            for pos, idx in enumerate(selected_indices)
                        }
                        self._vector_insert_many(list(vector_map.items()))
            except Exception:
                # Rollback SQLite inserts to avoid chunks without embeddings
                self.conn.rollback()
                raise

        self.conn.commit()
        return [str(chunk_id) for chunk_id in chunk_ids]

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        """Retrieve a chunk by ID.

        Args:
            chunk_id: The chunk identifier

        Returns:
            Chunk if found, None otherwise
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, symbol, chunk_type, file_path, start_line, end_line, 
                   language, code, metadata, created_at
            FROM chunks
            WHERE id = ?
        """,
            (int(chunk_id),),
        )

        row = cursor.fetchone()
        if row is None:
            return None

        return Chunk(
            id=str(row["id"]),
            symbol=row["symbol"],
            chunk_type=ChunkType(row["chunk_type"]),
            file_path=Path(row["file_path"]),
            start_line=row["start_line"],
            end_line=row["end_line"],
            language=Language(row["language"]),
            code=row["code"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    def _preprocess_code_query(self, code: str) -> str:
        """Extract searchable terms from code snippet.

        Code queries (e.g., from RepoEval) contain operators, punctuation, and noise.
        This extracts meaningful identifiers and API patterns for better retrieval.

        Args:
            code: Raw code snippet

        Returns:
            Space-separated search terms
        """
        import re

        terms = []

        # Extract identifiers (CamelCase, snake_case, alphanumeric)
        # Match: MyClass, my_function, getUserData, API_KEY, model123
        identifiers = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", code)

        for ident in identifiers:
            # Skip common keywords
            if ident.lower() in {
                "def",
                "class",
                "import",
                "from",
                "return",
                "if",
                "else",
                "elif",
                "for",
                "while",
                "try",
                "except",
                "with",
                "as",
                "self",
                "true",
                "false",
                "none",
                "null",
                "var",
                "let",
                "const",
                "function",
                "this",
                "super",
                "new",
            }:
                continue

            # Split CamelCase: getUserData -> get User Data
            camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)", ident)
            if len(camel_parts) > 1:
                terms.extend(camel_parts)

            # Split snake_case: my_function -> my function
            snake_parts = ident.split("_")
            if len(snake_parts) > 1:
                terms.extend([p for p in snake_parts if len(p) > 1])

            # Add full identifier
            terms.append(ident)

        # Extract API-like patterns (e.g., model.from_pretrained, np.array)
        api_calls = re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)", code)
        for call in api_calls:
            terms.append(
                call.replace(".", " ")
            )  # "model.from_pretrained" -> "model from_pretrained"
            terms.append(call.split(".")[-1])  # Also add just "from_pretrained"

        # Deduplicate while preserving order
        seen = set()
        unique_terms = []
        for term in terms:
            term_lower = term.lower()
            if term_lower not in seen and len(term) > 1:
                seen.add(term_lower)
                unique_terms.append(term)

        # Limit to top 30 terms to avoid overwhelming the query
        return " ".join(unique_terms[:30])

    def _apply_tier_filtering(
        self,
        results: list[SearchResult],
        k: int,
        include_deps: bool = True,
        tier_boost: dict | None = None,
    ) -> list[SearchResult]:
        """Apply tier filtering and boosting to search results.

        Args:
            results: Raw search results
            k: Number of results to return
            include_deps: Whether to include dependency tier
            tier_boost: Score multipliers per tier

        Returns:
            Filtered and boosted results
        """
        if not results:
            return results

        # Default tier boost values
        if tier_boost is None:
            tier_boost = {"project": 1.0, "dependency": 0.7, "stdlib": 0.5}

        # Apply tier boosting
        for result in results:
            tier = result.chunk.metadata.get("tier", "project")
            result.score *= tier_boost.get(tier, 1.0)

        # Filter by tier if needed
        if not include_deps:
            results = [r for r in results if r.chunk.metadata.get("tier", "project") == "project"]

        # Re-sort by boosted score
        results.sort(key=lambda r: r.score, reverse=True)

        return results[:k]

    def search_semantic(
        self,
        query: str,
        k: int = 10,
        filter_fn: Any = None,
        include_deps: bool = True,
        tier_boost: dict | None = None,
    ) -> list[SearchResult]:
        """Semantic vector search using usearch HNSW.

        Args:
            query: Query text (will be embedded)
            k: Number of results to return
            filter_fn: Optional filter function (not implemented yet)
            include_deps: Whether to include dependency tier chunks (default: True)
            tier_boost: Score multipliers per tier (default: project=1.0, dep=0.7, stdlib=0.5)

        Returns:
            List of search results sorted by relevance
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        if not self.embedding_enabled:
            # If embeddings disabled, return empty results (fallback to lexical)
            return []

        # Embed query
        query_vector = self._embed(query)

        if query_vector is None:
            return []

        ids_with_scores = self._vector_search(query_vector, k)

        if not ids_with_scores:
            return []

        chunk_ids = [chunk_id for chunk_id, _ in ids_with_scores]
        cursor = self.conn.cursor()
        placeholders = ",".join("?" * len(chunk_ids))
        cursor.execute(
            f"""
            SELECT id, symbol, chunk_type, file_path, start_line, end_line,
                   language, code, metadata, created_at
            FROM chunks WHERE id IN ({placeholders})
            """,
            chunk_ids,
        )

        chunk_lookup = {}
        for row in cursor.fetchall():
            chunk_lookup[str(row["id"])] = Chunk(
                id=str(row["id"]),
                symbol=row["symbol"],
                chunk_type=ChunkType(row["chunk_type"]),
                file_path=Path(row["file_path"]),
                start_line=row["start_line"],
                end_line=row["end_line"],
                language=Language(row["language"]),
                code=row["code"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            )

        results = []
        for chunk_id, score in ids_with_scores:
            chunk = chunk_lookup.get(chunk_id)
            if chunk:
                results.append(SearchResult(chunk=chunk, score=score))

        # Apply tier filtering and boosting
        return self._apply_tier_filtering(results, k, include_deps, tier_boost)

    def search_lexical(
        self, query: str, k: int = 10, include_deps: bool = True, tier_boost: dict | None = None
    ) -> list[SearchResult]:
        """Lexical full-text search using SQLite FTS5.

        Args:
            query: Query text
            k: Number of results to return
            include_deps: Whether to include dependency tier chunks (default: True)
            tier_boost: Score multipliers per tier (default: project=1.0, dep=0.7, stdlib=0.5)

        Returns:
            List of search results sorted by relevance
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()

        # Sanitize query for FTS5 using token extraction
        sanitized_query = self._sanitize_fts5_query(query)

        # FTS5 search
        cursor.execute(
            """
            SELECT chunks.id, bm25(chunks_fts) as rank
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """,
            (sanitized_query, k),
        )

        rows = cursor.fetchall()
        if not rows:
            return []

        ids_with_scores = []
        for row in rows:
            score = abs(float(row["rank"])) / 100.0  # Rough normalization
            ids_with_scores.append((str(row["id"]), score))

        chunk_ids = [chunk_id for chunk_id, _ in ids_with_scores]
        placeholders = ",".join("?" * len(chunk_ids))
        cursor.execute(
            f"""
            SELECT id, symbol, chunk_type, file_path, start_line, end_line,
                   language, code, metadata, created_at
            FROM chunks WHERE id IN ({placeholders})
            """,
            chunk_ids,
        )

        chunk_lookup = {}
        for row in cursor.fetchall():
            chunk_lookup[str(row["id"])] = Chunk(
                id=str(row["id"]),
                symbol=row["symbol"],
                chunk_type=ChunkType(row["chunk_type"]),
                file_path=Path(row["file_path"]),
                start_line=row["start_line"],
                end_line=row["end_line"],
                language=Language(row["language"]),
                code=row["code"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            )

        results = []
        for chunk_id, score in ids_with_scores:
            chunk = chunk_lookup.get(chunk_id)
            if chunk:
                results.append(SearchResult(chunk=chunk, score=score))

        # Apply tier filtering and boosting
        return self._apply_tier_filtering(results, k, include_deps, tier_boost)

    def search_hybrid(
        self,
        query: str,
        k: int = 10,
        vector_weight: float = 0.7,
        include_deps: bool = True,
        tier_boost: dict | None = None,
        preprocess_code: bool = False,
        parallel: bool = True,
        use_cache: bool = False,
    ) -> list[SearchResult]:
        """Hybrid search using Reciprocal Rank Fusion (RRF).

        Args:
            query: Query text (or code snippet)
            k: Number of results to return
            vector_weight: Weight for vector search (0-1)
            include_deps: Include dependency chunks (for compatibility, not yet implemented)
            tier_boost: Tier boosting configuration (for compatibility, not yet implemented)
            preprocess_code: If True, extract searchable terms from code query (for RepoEval-style queries)
            parallel: If True, run semantic and lexical searches in parallel (~2x speedup)
            use_cache: If True, cache search results (good for repeated queries)

        Returns:
            List of search results sorted by combined relevance
        """
        # Generate cache key (used later if caching enabled)
        cache_key = f"{query}:{k}:{vector_weight}:{preprocess_code}"

        # Check cache if enabled
        if use_cache:
            if self._search_cache is None:
                # Initialize cache dict
                self._search_cache = {}

            if cache_key in self._search_cache:
                return self._search_cache[cache_key]

        # Preprocess code query if requested
        processed_query = query
        if preprocess_code:
            processed_query = self._preprocess_code_query(query)
            # Use original query for semantic (better for embeddings), processed for lexical

        # If embeddings disabled, fall back to lexical only
        if not self.embedding_enabled:
            results = self.search_lexical(processed_query if preprocess_code else query, k)
            if use_cache and self._search_cache is not None:
                self._search_cache[cache_key] = results
            return results

        # Fetch more candidates for fusion
        fetch_k = k * 3

        # Get semantic and lexical results (parallel or sequential)
        # Semantic: use original query (embeddings work better with raw code context)
        # Lexical: use processed query (FTS5 works better with extracted terms)
        if parallel:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as executor:
                semantic_future = executor.submit(self.search_semantic, query, fetch_k)
                lexical_future = executor.submit(
                    self.search_lexical, processed_query if preprocess_code else query, fetch_k
                )
                semantic_results = semantic_future.result()
                lexical_results = lexical_future.result()
        else:
            # Sequential execution (original behavior)
            semantic_results = self.search_semantic(query, fetch_k)
            lexical_results = self.search_lexical(
                processed_query if preprocess_code else query, fetch_k
            )

        # Reciprocal Rank Fusion
        scores: dict[str, float] = {}
        k_rrf = 60  # RRF constant

        # Add semantic scores
        for rank, result in enumerate(semantic_results):
            chunk_id = result.chunk.id
            if chunk_id:
                scores[chunk_id] = scores.get(chunk_id, 0) + vector_weight / (k_rrf + rank)

        # Add lexical scores
        lexical_weight = 1.0 - vector_weight
        for rank, result in enumerate(lexical_results):
            chunk_id = result.chunk.id
            if chunk_id:
                scores[chunk_id] = scores.get(chunk_id, 0) + lexical_weight / (k_rrf + rank)

        # Sort by combined score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]

        # Batch fetch chunks (much faster than individual get_chunk calls)
        chunk_ids = [chunk_id for chunk_id, _ in ranked]
        if not chunk_ids:
            return []

        # Fetch all chunks in one query
        cursor = self.conn.cursor()
        placeholders = ",".join("?" * len(chunk_ids))
        cursor.execute(
            f"""
            SELECT id, symbol, chunk_type, file_path, start_line, end_line,
                   language, code, metadata, created_at
            FROM chunks WHERE id IN ({placeholders})
            """,
            chunk_ids,
        )

        # Build chunk lookup
        chunk_lookup = {}
        for row in cursor.fetchall():
            chunk_lookup[str(row["id"])] = Chunk(
                id=str(row["id"]),
                symbol=row["symbol"],
                chunk_type=ChunkType(row["chunk_type"]),
                file_path=Path(row["file_path"]),
                start_line=row["start_line"],
                end_line=row["end_line"],
                language=Language(row["language"]),
                code=row["code"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            )

        # Convert to SearchResults in original ranked order
        results = []
        for chunk_id, score in ranked:
            chunk = chunk_lookup.get(chunk_id)
            if chunk:
                results.append(SearchResult(chunk=chunk, score=score))

        # Cache results if enabled
        if use_cache and self._search_cache is not None:
            cache_key = f"{query}:{k}:{vector_weight}:{preprocess_code}"
            self._search_cache[cache_key] = results
            # Limit cache size to prevent memory issues
            if len(self._search_cache) > 500:
                # Remove oldest entries (simple FIFO)
                oldest_keys = list(self._search_cache.keys())[:100]
                for key in oldest_keys:
                    del self._search_cache[key]

        return results

    def search_files(
        self,
        query: str,
        k: int = 10,
        vector_weight: float = 0.7,
        preprocess_code: bool = False,
        aggregation: str = "sum",
    ) -> list[tuple[str, float]]:
        """Search and aggregate results at file level (for RepoEval-style benchmarks).

        Returns files ranked by aggregated chunk scores, useful for file-level recall metrics.

        Args:
            query: Query text
            k: Number of files to return
            vector_weight: Weight for vector search (0-1)
            preprocess_code: If True, extract searchable terms from code query
            aggregation: Score aggregation method ("sum" or "max")

        Returns:
            List of (file_path, score) tuples sorted by relevance
        """
        # Get more chunk results to ensure good file coverage
        fetch_k = k * 5

        chunk_results = self.search_hybrid(
            query, k=fetch_k, vector_weight=vector_weight, preprocess_code=preprocess_code
        )

        # Aggregate by file
        file_scores: dict[str, list[float]] = {}
        for result in chunk_results:
            file_path = str(result.chunk.file_path)
            if file_path not in file_scores:
                file_scores[file_path] = []
            file_scores[file_path].append(result.score)

        # Compute aggregated scores
        ranked_files = []
        for file_path, scores in file_scores.items():
            if aggregation == "max":
                agg_score = max(scores)
            else:  # sum
                agg_score = sum(scores)
            ranked_files.append((file_path, agg_score))

        # Sort by score and return top k
        ranked_files.sort(key=lambda x: x[1], reverse=True)
        return ranked_files[:k]

    def get_stats(self) -> IndexStats:
        """Get index statistics.

        Returns:
            Index statistics including file count, chunk count, etc.
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()

        # Count chunks
        cursor.execute("SELECT COUNT(*) as count FROM chunks")
        total_chunks = cursor.fetchone()["count"]

        # Count unique files
        cursor.execute("SELECT COUNT(DISTINCT file_path) as count FROM chunks")
        total_files = cursor.fetchone()["count"]

        # Count by language
        cursor.execute(
            """
            SELECT language, COUNT(*) as count 
            FROM chunks 
            GROUP BY language
        """
        )
        languages = {Language(row["language"]): row["count"] for row in cursor.fetchall()}

        # Get last indexed time
        cursor.execute("SELECT MAX(created_at) as last FROM chunks")
        last_indexed_str = cursor.fetchone()["last"]
        last_indexed = datetime.fromisoformat(last_indexed_str) if last_indexed_str else None

        return IndexStats(
            total_files=total_files,
            total_chunks=total_chunks,
            total_size_bytes=0,  # Not tracked in this backend
            languages=languages,
            last_indexed=last_indexed,
        )

    # ===================================================================
    # Code Relationship Graph
    # ===================================================================

    def add_code_relationships(self, relationships: list[CodeRelationshipRecord]) -> int:
        """Persist code relationships discovered during research."""
        if self.conn is None:
            raise RuntimeError("Index not initialized")
        if not relationships:
            return 0

        cursor = self.conn.cursor()
        before = self.conn.total_changes

        cursor.executemany(
            """
            INSERT OR IGNORE INTO code_relationships (
                from_entity,
                to_entity,
                relationship_type,
                from_chunk_id,
                to_chunk_id
            ) VALUES (?, ?, ?, ?, ?)
        """,
            [
                (
                    rel.from_entity,
                    rel.to_entity,
                    rel.relationship_type,
                    rel.from_chunk_id or "",
                    rel.to_chunk_id or "",
                )
                for rel in relationships
            ],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def get_code_relationships(
        self,
        from_entity: str | None = None,
        to_entity: str | None = None,
        relationship_type: str | None = None,
        limit: int = 100,
    ) -> list[CodeRelationshipRecord]:
        """Query persisted code relationships."""
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()

        conditions = []
        params: list[Any] = []

        if from_entity:
            conditions.append("from_entity = ?")
            params.append(from_entity)
        if to_entity:
            conditions.append("to_entity = ?")
            params.append(to_entity)
        if relationship_type:
            conditions.append("relationship_type = ?")
            params.append(relationship_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        cursor.execute(
            f"""
            SELECT id, from_entity, to_entity, relationship_type,
                   from_chunk_id, to_chunk_id, created_at
            FROM code_relationships
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """,
            params,
        )

        return [self._row_to_code_relationship(row) for row in cursor.fetchall()]

    # ===================================================================
    # Decision Management
    # ===================================================================

    def add_decision(
        self,
        session_id: str,
        title: str,
        description: str,
        reasoning: str | None = None,
        alternatives: list[dict[str, Any]] | None = None,
        conceptual_links: list[dict[str, Any]] | None = None,
        commit_hash: str | None = None,
        commit_time: datetime | None = None,
    ) -> int:
        """Add a pending decision (FIFO auto-cleanup when >100).

        Args:
            session_id: LLM session that created this
            title: Short title
            description: Full context
            reasoning: Why this decision was made
            alternatives: Other options considered

        Returns:
            Decision ID
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO decisions (
                session_id,
                title,
                description,
                reasoning,
                alternatives,
                conceptual_links,
                commit_hash,
                commit_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                session_id,
                title,
                description,
                reasoning,
                json.dumps(alternatives or []),
                json.dumps(conceptual_links or []),
                commit_hash,
                commit_time.isoformat() if commit_time else None,
            ),
        )
        decision_id = cursor.lastrowid

        # Embed decision for semantic search
        decision_text = f"{title}\n\n{description}"
        if reasoning:
            decision_text += f"\n\nReasoning: {reasoning}"

        vector = self._embed(decision_text)
        # Store with decision key prefix
        if vector is not None:
            self._vector_insert(self.DECISION_OFFSET + decision_id, vector)

        self.conn.commit()
        return decision_id

    def approve_decision(self, decision_id: int, category: str) -> int:
        """Promote decision to permanent memory.

        Args:
            decision_id: ID of pending decision
            category: Category like 'architecture', 'pattern'

        Returns:
            Approved memory ID
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()

        # Get the decision
        cursor.execute(
            """
            SELECT title, description, reasoning, conceptual_links
            FROM decisions 
            WHERE id = ? AND status = 'pending'
        """,
            (decision_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Decision {decision_id} not found or already processed")

        # Update decision status
        cursor.execute(
            """
            UPDATE decisions 
            SET status = 'approved', category = ?, approved_at = ?
            WHERE id = ?
        """,
            (category, datetime.now().isoformat(), decision_id),
        )

        # Add to approved memory
        content = f"{row['description']}\n\nReasoning: {row['reasoning'] or 'N/A'}"
        conceptual_links = self._load_json_list(row["conceptual_links"])
        content += self._render_conceptual_links(conceptual_links)
        cursor.execute(
            """
            INSERT INTO approved_memory (decision_id, category, title, content)
            VALUES (?, ?, ?, ?)
        """,
            (decision_id, category, row["title"], content),
        )
        memory_id = cursor.lastrowid

        self.conn.commit()
        return memory_id

    def reject_decision(self, decision_id: int) -> None:
        """Mark decision as rejected.

        Args:
            decision_id: ID of pending decision
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE decisions 
            SET status = 'rejected'
            WHERE id = ? AND status = 'pending'
        """,
            (decision_id,),
        )
        self.conn.commit()

    def list_pending_decisions(self, limit: int = 20) -> list[Decision]:
        """List oldest pending decisions for review.

        Args:
            limit: Maximum number to return

        Returns:
            List of pending decisions, oldest first
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, session_id, title, description, reasoning, alternatives, conceptual_links,
                   status, category, commit_hash, commit_time, created_at, approved_at
            FROM decisions
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
        """,
            (limit,),
        )

        decisions = []
        for row in cursor.fetchall():
            decisions.append(
                Decision(
                    id=row["id"],
                    session_id=row["session_id"],
                    title=row["title"],
                    description=row["description"],
                    reasoning=row["reasoning"],
                    alternatives=self._load_json_list(row["alternatives"]),
                    conceptual_links=self._load_json_list(row["conceptual_links"]),
                    status=row["status"],
                    category=row["category"],
                    commit_hash=row["commit_hash"],
                    commit_time=datetime.fromisoformat(row["commit_time"])
                    if row["commit_time"]
                    else None,
                    created_at=datetime.fromisoformat(row["created_at"])
                    if row["created_at"]
                    else None,
                    approved_at=datetime.fromisoformat(row["approved_at"])
                    if row["approved_at"]
                    else None,
                )
            )

        return decisions

    def get_decision(self, decision_id: int) -> Decision | None:
        """Get a specific decision by ID.

        Args:
            decision_id: Decision ID

        Returns:
            Decision if found, None otherwise
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, session_id, title, description, reasoning, alternatives, conceptual_links,
                   status, category, commit_hash, commit_time, created_at, approved_at
            FROM decisions
            WHERE id = ?
        """,
            (decision_id,),
        )

        row = cursor.fetchone()
        if row is None:
            return None

        return Decision(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            description=row["description"],
            reasoning=row["reasoning"],
            alternatives=self._load_json_list(row["alternatives"]),
            conceptual_links=self._load_json_list(row["conceptual_links"]),
            status=row["status"],
            category=row["category"],
            commit_hash=row["commit_hash"],
            commit_time=datetime.fromisoformat(row["commit_time"]) if row["commit_time"] else None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            approved_at=datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None,
        )

    # ===================================================================
    # Timeline & Changelog Management
    # ===================================================================

    def add_timeline_event(
        self,
        event_type: str,
        from_ref: str,
        to_ref: str,
        summary: str,
        files_changed: list[str] | None = None,
        diff_stats: dict[str, Any] | None = None,
        importance: str = "medium",
        commit_hash: str | None = None,
        commit_time: datetime | None = None,
    ) -> int:
        """Add a timeline event.

        Args:
            event_type: 'tag', 'merge', 'major_change'
            from_ref: Starting git ref
            to_ref: Ending git ref
            summary: Description
            files_changed: List of affected files
            diff_stats: Diff statistics
            importance: 'high', 'medium', 'low'

        Returns:
            Timeline event ID
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO timeline (
                event_type, from_ref, to_ref, summary, files_changed, diff_stats, importance, commit_hash, commit_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                event_type,
                from_ref,
                to_ref,
                summary,
                json.dumps(files_changed or []),
                json.dumps(diff_stats or {}),
                importance,
                commit_hash,
                commit_time.isoformat() if commit_time else None,
            ),
        )
        timeline_id = cursor.lastrowid

        # Embed timeline event for semantic search
        event_text = f"{event_type}: {from_ref} → {to_ref}\n\n{summary}"
        vector = self._embed(event_text)
        if vector is not None:
            self._vector_insert(self.TIMELINE_OFFSET + timeline_id, vector)

        self.conn.commit()
        return timeline_id

    def add_changelog(
        self,
        tag: str,
        version: str | None = None,
        summary: str = "",
        breaking_changes: list[str] | None = None,
        features: list[str] | None = None,
        fixes: list[str] | None = None,
        commit_hash: str | None = None,
        commit_time: datetime | None = None,
    ) -> int:
        """Add a changelog entry.

        Args:
            tag: Git tag
            version: Semantic version
            summary: Changelog summary
            breaking_changes: Breaking changes list
            features: Features list
            fixes: Fixes list

        Returns:
            Changelog entry ID
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO changelogs (
                tag, version, summary, breaking_changes, features, fixes, date, commit_hash, commit_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                tag,
                version,
                summary,
                json.dumps(breaking_changes or []),
                json.dumps(features or []),
                json.dumps(fixes or []),
                datetime.now().isoformat(),
                commit_hash,
                commit_time.isoformat() if commit_time else None,
            ),
        )
        changelog_id = cursor.lastrowid

        # Embed changelog for semantic search
        changelog_text = f"{tag} ({version})\n\n{summary}"
        vector = self._embed(changelog_text)
        if vector is not None:
            self._vector_insert(self.CHANGELOG_OFFSET + changelog_id, vector)

        self.conn.commit()
        return changelog_id

    def get_timeline_events(
        self, from_ref: str | None = None, to_ref: str | None = None, limit: int = 20
    ) -> list[TimelineEvent]:
        """Get timeline events.

        Args:
            from_ref: Filter by starting ref
            to_ref: Filter by ending ref
            limit: Maximum number to return

        Returns:
            List of timeline events
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()

        # Build query
        conditions = []
        params = []

        if from_ref:
            conditions.append("from_ref = ?")
            params.append(from_ref)
        if to_ref:
            conditions.append("to_ref = ?")
            params.append(to_ref)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        cursor.execute(
            f"""
            SELECT id, event_type, from_ref, to_ref, summary, files_changed, diff_stats, importance,
                   commit_hash, commit_time, created_at
            FROM timeline
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """,
            params,
        )

        events = []
        for row in cursor.fetchall():
            events.append(
                TimelineEvent(
                    id=row["id"],
                    event_type=row["event_type"],
                    from_ref=row["from_ref"],
                    to_ref=row["to_ref"],
                    summary=row["summary"],
                    files_changed=json.loads(row["files_changed"]) if row["files_changed"] else [],
                    diff_stats=json.loads(row["diff_stats"]) if row["diff_stats"] else {},
                    importance=row["importance"],
                    commit_hash=row["commit_hash"],
                    commit_time=datetime.fromisoformat(row["commit_time"])
                    if row["commit_time"]
                    else None,
                    created_at=datetime.fromisoformat(row["created_at"])
                    if row["created_at"]
                    else None,
                )
            )

        return events

    def get_changelogs(self, limit: int = 20) -> list[ChangelogEntry]:
        """Get changelog entries.

        Args:
            limit: Maximum number to return

        Returns:
            List of changelog entries, newest first
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, tag, version, date, summary, breaking_changes, features, fixes,
                   commit_hash, commit_time, created_at
            FROM changelogs
            ORDER BY date DESC
            LIMIT ?
        """,
            (limit,),
        )

        changelogs = []
        for row in cursor.fetchall():
            changelogs.append(
                ChangelogEntry(
                    id=row["id"],
                    tag=row["tag"],
                    version=row["version"],
                    date=datetime.fromisoformat(row["date"]) if row["date"] else None,
                    summary=row["summary"],
                    breaking_changes=json.loads(row["breaking_changes"])
                    if row["breaking_changes"]
                    else [],
                    features=json.loads(row["features"]) if row["features"] else [],
                    fixes=json.loads(row["fixes"]) if row["fixes"] else [],
                    commit_hash=row["commit_hash"],
                    commit_time=datetime.fromisoformat(row["commit_time"])
                    if row["commit_time"]
                    else None,
                    created_at=datetime.fromisoformat(row["created_at"])
                    if row["created_at"]
                    else None,
                )
            )

        return changelogs

    # ===================================================================
    # Unified Search (Code + Memory)
    # ===================================================================

    def search_all(self, query: str, k: int = 10, vector_weight: float = 0.7) -> list[SearchResult]:
        """Search across both code and memory.

        Note: Currently just returns code search results.
        Full unified search across memory types needs ID offset handling.

        Args:
            query: Query text
            k: Number of results
            vector_weight: Weight for vector search

        Returns:
            List of results from code chunks
        """
        # For now, delegate to hybrid code search
        # TODO: Implement true unified search with proper ID handling
        return self.search_hybrid(query, k, vector_weight)

    def search_memory(
        self, query: str, k: int = 10, vector_weight: float = 0.7
    ) -> list[SearchResult]:
        """Search only memory (decisions + approved).

        Note: Simplified implementation for now.

        Args:
            query: Query text
            k: Number of results
            vector_weight: Weight for vector search

        Returns:
            List of results from decisions and approved memory
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        # Simple text search in decisions for now
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, title, description, category, status, conceptual_links
            FROM decisions
            WHERE title LIKE ? OR description LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (f"%{query}%", f"%{query}%", k),
        )

        # Convert to SearchResults (wrapping in fake chunks for compatibility)
        results = []
        for row in cursor.fetchall():
            conceptual_links = self._load_json_list(row["conceptual_links"])
            # Create a pseudo-chunk for the decision
            fake_chunk = Chunk(
                symbol=row["title"],
                start_line=1,
                end_line=1,
                code=row["description"],
                chunk_type=ChunkType.FUNCTION,  # Fake type
                language=Language.PYTHON,  # Fake language
                file_path=Path(f"decisions/{row['id']}.md"),
                metadata={
                    "type": "decision",
                    "status": row["status"],
                    "category": row["category"],
                    "conceptual_links": conceptual_links,
                    "conceptual_link_count": len(conceptual_links),
                },
            )
            results.append(SearchResult(chunk=fake_chunk, score=1.0))

        return results

    # ===================================================================
    # LLM Context Generation
    # ===================================================================

    def generate_context(
        self,
        query: str | None = None,
        include_code: bool = True,
        include_decisions: bool = True,
        include_timeline: bool = True,
        include_changelogs: bool = True,
    ) -> dict[str, Any]:
        """Generate JSON context for LLM consumption.

        Args:
            query: Optional query to include relevant code
            include_code: Include code chunks
            include_decisions: Include decisions
            include_timeline: Include timeline
            include_changelogs: Include changelogs

        Returns:
            Dictionary with project memory context
        """
        context: dict[str, Any] = {
            "project_memory": {
                "generated_at": datetime.now().isoformat(),
            }
        }

        # Codebase summary
        stats = self.get_stats()
        context["project_memory"]["codebase_summary"] = {
            "total_files": stats.total_files,
            "total_chunks": stats.total_chunks,
            "languages": [lang.value for lang in stats.languages.keys()],
            "last_indexed": stats.last_indexed.isoformat() if stats.last_indexed else None,
        }

        # Recent decisions
        if include_decisions:
            decisions = self.list_pending_decisions(limit=10)
            context["project_memory"]["recent_decisions"] = [
                {
                    "id": d.id,
                    "title": d.title,
                    "description": d.description,
                    "status": d.status,
                    "category": d.category,
                }
                for d in decisions
            ]

            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT id, title, description, category, approved_at
                FROM decisions
                WHERE status = 'approved'
                ORDER BY approved_at DESC
                LIMIT 10
                """
            )
            context["project_memory"]["approved_decisions"] = [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "description": row["description"],
                    "status": "approved",
                    "category": row["category"],
                    "approved_at": row["approved_at"],
                }
                for row in cursor.fetchall()
            ]

        # Recent timeline events
        if include_timeline:
            timeline = self.get_timeline_events(limit=10)
            context["project_memory"]["recent_changes"] = [
                {
                    "from": t.from_ref,
                    "to": t.to_ref,
                    "summary": t.summary,
                    "importance": t.importance,
                }
                for t in timeline
            ]

        # Changelogs
        if include_changelogs:
            changelogs = self.get_changelogs(limit=5)
            context["project_memory"]["changelogs"] = [
                {
                    "tag": c.tag,
                    "version": c.version,
                    "summary": c.summary,
                    "breaking_changes": c.breaking_changes,
                }
                for c in changelogs
            ]

        # Relevant code (if query provided)
        if include_code and query:
            code_results = self.search_hybrid(query, k=5)
            context["project_memory"]["relevant_code"] = [
                {
                    "file": str(r.chunk.file_path),
                    "symbol": r.chunk.symbol,
                    "code": r.chunk.code[:200],  # Truncate for context size
                    "score": r.score,
                }
                for r in code_results
            ]

        return context

    # ===================================================================
    # Import/Export for Collaboration
    # ===================================================================

    def export_memory(
        self,
        output_path: str | Path = ".sia-code/memory.json",
        include_timeline: bool = True,
        include_changelogs: bool = True,
        include_decisions: bool = True,
        include_pending: bool = False,
    ) -> str:
        """Export memory to JSON file for git commit.

        Args:
            output_path: Path to output file
            include_timeline: Include timeline events
            include_changelogs: Include changelog entries
            include_decisions: Include approved decisions
            include_pending: Include pending decisions

        Returns:
            Path to exported file
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        output_path = Path(output_path)
        if not output_path.is_absolute():
            output_path = self.path / output_path

        memory: dict[str, Any] = {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "project": self.path.name,
        }

        # Timeline events
        if include_timeline:
            timeline = self.get_timeline_events(limit=100)
            memory["timeline"] = [t.to_dict() for t in timeline]

        # Changelogs
        if include_changelogs:
            changelogs = self.get_changelogs(limit=100)
            memory["changelogs"] = [c.to_dict() for c in changelogs]

        # Approved decisions
        if include_decisions:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT id, session_id, title, description, reasoning, category, conceptual_links, commit_hash, commit_time, approved_at
                FROM decisions
                WHERE status = 'approved'
                ORDER BY approved_at DESC
            """
            )
            approved = []
            for row in cursor.fetchall():
                approved.append(
                    {
                        "id": f"decision:{row['id']}",
                        "title": row["title"],
                        "description": row["description"],
                        "reasoning": row["reasoning"],
                        "category": row["category"],
                        "conceptual_links": self._load_json_list(row["conceptual_links"]),
                        "commit_hash": row["commit_hash"],
                        "commit_time": row["commit_time"],
                        "approved_at": row["approved_at"],
                    }
                )
            memory["decisions"] = approved

        # Pending decisions (optional)
        if include_pending:
            pending = self.list_pending_decisions(limit=100)
            memory["pending_decisions"] = [
                {
                    "id": f"decision:{d.id}",
                    "title": d.title,
                    "description": d.description,
                    "reasoning": d.reasoning,
                    "conceptual_links": d.conceptual_links,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in pending
            ]

        # Write to file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(memory, f, indent=2)

        return str(output_path)

    def import_memory(
        self,
        input_path: str | Path = ".sia-code/memory.json",
        conflict_strategy: str = "newest_wins",
    ) -> ImportResult:
        """Import memory from JSON file (e.g., after git pull).

        Args:
            input_path: Path to input file
            conflict_strategy: 'newest_wins' - keep entry with latest timestamp

        Returns:
            ImportResult with counts
        """
        if self.conn is None:
            raise RuntimeError("Index not initialized")

        input_path = Path(input_path)
        if not input_path.is_absolute():
            input_path = self.path / input_path

        if not input_path.exists():
            raise FileNotFoundError(f"Import file not found: {input_path}")

        with open(input_path) as f:
            memory = json.load(f)

        result = ImportResult()

        # Import timeline events
        for event_data in memory.get("timeline", []):
            existing = self.get_timeline_events(
                from_ref=event_data["from_ref"], to_ref=event_data["to_ref"], limit=1
            )

            if existing:
                # Check if imported is newer
                imported_time = datetime.fromisoformat(event_data["created_at"])
                if imported_time > existing[0].created_at:
                    # Update existing (simplified: just skip for now)
                    result.skipped += 1
                else:
                    result.skipped += 1
            else:
                # Add new
                self.add_timeline_event(
                    event_type=event_data["event_type"],
                    from_ref=event_data["from_ref"],
                    to_ref=event_data["to_ref"],
                    summary=event_data["summary"],
                    files_changed=event_data.get("files_changed", []),
                    diff_stats=event_data.get("diff_stats", {}),
                    importance=event_data.get("importance", "medium"),
                    commit_hash=event_data.get("commit_hash"),
                    commit_time=datetime.fromisoformat(event_data["commit_time"])
                    if event_data.get("commit_time")
                    else None,
                )
                result.added += 1

        # Import changelogs
        for changelog_data in memory.get("changelogs", []):
            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM changelogs WHERE tag = ?", (changelog_data["tag"],))
            existing = cursor.fetchone()

            if existing:
                result.skipped += 1
            else:
                self.add_changelog(
                    tag=changelog_data["tag"],
                    version=changelog_data.get("version"),
                    summary=changelog_data.get("summary", ""),
                    breaking_changes=changelog_data.get("breaking_changes", []),
                    features=changelog_data.get("features", []),
                    fixes=changelog_data.get("fixes", []),
                    commit_hash=changelog_data.get("commit_hash"),
                    commit_time=datetime.fromisoformat(changelog_data["commit_time"])
                    if changelog_data.get("commit_time")
                    else None,
                )
                result.added += 1

        # Import decisions (approved only)
        for decision_data in memory.get("decisions", []):
            # Check if already exists by title (simplified)
            cursor = self.conn.cursor()
            cursor.execute("SELECT id FROM decisions WHERE title = ?", (decision_data["title"],))
            existing = cursor.fetchone()

            if existing:
                result.skipped += 1
            else:
                # Add as approved decision
                decision_id = self.add_decision(
                    session_id="imported",
                    title=decision_data["title"],
                    description=decision_data["description"],
                    reasoning=decision_data.get("reasoning"),
                    conceptual_links=decision_data.get("conceptual_links"),
                    commit_hash=decision_data.get("commit_hash"),
                    commit_time=datetime.fromisoformat(decision_data["commit_time"])
                    if decision_data.get("commit_time")
                    else None,
                )
                # Immediately approve it
                self.approve_decision(decision_id, decision_data.get("category", "imported"))
                result.added += 1

        return result
