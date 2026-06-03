from pathlib import Path

from sia_code.mcp_lock import index_lock, lock_path_for_index


def test_lock_path_for_index_uses_parent_directory(tmp_path):
    index_dir = tmp_path / ".sia-code"

    assert lock_path_for_index(index_dir) == tmp_path / "..sia-code.mcp.lock"


def test_index_lock_creates_lock_file_parent(tmp_path):
    index_dir = tmp_path / ".sia-code"

    with index_lock(index_dir) as lock_path:
        assert lock_path.parent == tmp_path
        assert lock_path.name == "..sia-code.mcp.lock"
