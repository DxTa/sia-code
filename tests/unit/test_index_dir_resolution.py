import pytest

from sia_code.cli import resolve_index_dir


class _RunResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def _fake_run_factory(mapping: dict[tuple[str, ...], str]):
    def fake_run(args, **kwargs):
        key = tuple(args)
        if key not in mapping:
            raise AssertionError(f"Unexpected subprocess args: {args}")
        return _RunResult(mapping[key])

    return fake_run


def test_resolve_index_dir_prefers_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-index"
    monkeypatch.setenv("SIA_CODE_INDEX_DIR", str(override))
    assert resolve_index_dir(tmp_path) == override


def test_default_scope_uses_worktree_local_index_when_not_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("SIA_CODE_INDEX_DIR", raising=False)
    monkeypatch.delenv("SIA_CODE_INDEX_SCOPE", raising=False)

    monkeypatch.setattr(
        "sia_code.cli.subprocess.run",
        _fake_run_factory(
            {
                ("git", "rev-parse", "--git-dir"): ".git\n",
                ("git", "rev-parse", "--git-common-dir"): ".git\n",
            }
        ),
    )

    assert resolve_index_dir(tmp_path) == tmp_path / ".sia-code"


def test_default_scope_uses_shared_index_in_worktree(tmp_path, monkeypatch):
    monkeypatch.delenv("SIA_CODE_INDEX_DIR", raising=False)
    monkeypatch.delenv("SIA_CODE_INDEX_SCOPE", raising=False)

    common_dir = tmp_path / ".." / "repo" / ".git"
    common_dir = common_dir.resolve()

    monkeypatch.setattr(
        "sia_code.cli.subprocess.run",
        _fake_run_factory(
            {
                ("git", "rev-parse", "--git-dir"): ".git/worktrees/branch\n",
                ("git", "rev-parse", "--git-common-dir"): str(common_dir) + "\n",
            }
        ),
    )

    assert resolve_index_dir(tmp_path) == common_dir / "sia-code"


@pytest.mark.parametrize("scope", ["shared", "auto"])
def test_explicit_scope_controls_resolution(tmp_path, monkeypatch, scope):
    monkeypatch.delenv("SIA_CODE_INDEX_DIR", raising=False)
    monkeypatch.setenv("SIA_CODE_INDEX_SCOPE", scope)

    common_dir = tmp_path / "common" / ".git"
    common_dir.mkdir(parents=True)

    if scope == "shared":
        mapping = {
            ("git", "rev-parse", "--git-dir"): ".git\n",
            ("git", "rev-parse", "--git-common-dir"): str(common_dir) + "\n",
        }
        expected = common_dir / "sia-code"
    else:
        # auto decides based on whether we're in a linked worktree
        mapping = {
            ("git", "rev-parse", "--git-dir"): ".git\n",
            ("git", "rev-parse", "--git-common-dir"): ".git\n",
        }
        expected = tmp_path / ".sia-code"

    monkeypatch.setattr(
        "sia_code.cli.subprocess.run",
        _fake_run_factory(mapping),
    )

    assert resolve_index_dir(tmp_path) == expected
