"""Cross-process locking helpers for MCP mutations."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    from filelock import FileLock
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in non-extra installs
    FileLock = None
    _FILELOCK_IMPORT_ERROR = exc
else:
    _FILELOCK_IMPORT_ERROR = None


def lock_path_for_index(index_dir: Path) -> Path:
    """Return the lock file path for an index directory."""
    return index_dir.parent / f".{index_dir.name}.mcp.lock"


@contextmanager
def index_lock(index_dir: Path, timeout: float = 60.0) -> Iterator[Path]:
    """Serialize MCP mutations for a single index across processes."""
    if FileLock is None:
        raise RuntimeError(
            "MCP support requires installing 'sia-code[mcp]'"
        ) from _FILELOCK_IMPORT_ERROR
    lock_path = lock_path_for_index(index_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=timeout):
        yield lock_path
