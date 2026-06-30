"""Dynamic git memory — on-demand file history with branch/worktree awareness.

Computes git context dynamically per query rather than relying on pre-indexed batch data.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .recency import RecencyConfig, RecencyScorer
from .revert_detector import CommitInfo, RevertDetector, RevertPair


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class HistoricalCommit:
    """A commit in a file's history."""

    hash: str
    message: str
    author: str
    date: datetime
    files_changed: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    is_reverted: bool = False
    reverted_by: str | None = None
    branch: str | None = None
    branch_relevance: float = 1.0
    recency_score: float = 1.0
    intent: str | None = None  # set by IntentClassifier


@dataclass
class BranchInfo:
    """Information about a git branch."""

    name: str
    last_commit_date: datetime | None = None
    is_merged: bool = False
    is_current: bool = False
    relevance: float = 0.0


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""

    path: Path
    branch: str
    head_commit: str
    is_current: bool = False


@dataclass
class BranchContext:
    """Context about the current branch state."""

    current_branch: str
    base_branch: str
    merge_base: str | None = None
    divergence_commits: int = 0  # commits since divergence on current
    worktrees: list[WorktreeInfo] = field(default_factory=list)


@dataclass
class FileHistory:
    """Complete history for a file with metadata."""

    file_path: str
    all_commits: list[HistoricalCommit] = field(default_factory=list)
    effective_commits: list[HistoricalCommit] = field(default_factory=list)
    reverts: list[RevertPair] = field(default_factory=list)
    branch_context: BranchContext | None = None
    owners: list[tuple[str, int]] = field(default_factory=list)  # (author, count)


# ---------------------------------------------------------------------------
# BranchResolver
# ---------------------------------------------------------------------------


@dataclass
class BranchResolverConfig:
    """Configuration for branch resolution."""

    base_branch: str = "main"
    base_branch_fallbacks: list[str] = field(
        default_factory=lambda: ["master", "develop"]
    )
    branch_relevance: dict[str, float] = field(
        default_factory=lambda: {
            "current": 1.0,
            "base": 0.8,
            "recent": 0.5,
            "merged": 0.3,
            "stale": 0.1,
        }
    )
    stale_days: int = 90


class BranchResolver:
    """Resolves branch context, worktrees, and relevance scoring."""

    def __init__(self, repo_path: Path, config: BranchResolverConfig | None = None):
        self.repo_path = repo_path
        self.config = config or BranchResolverConfig()

    def _git(self, *args: str, check: bool = True) -> str | None:
        """Run a git command, return stdout or None on failure."""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=check,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            return None

    def current_branch(self) -> str:
        """Get current branch name (handles detached HEAD)."""
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        if branch and branch != "HEAD":
            return branch
        # Detached HEAD — use short hash
        short = self._git("rev-parse", "--short", "HEAD")
        return short or "unknown"

    def resolve_base_branch(self) -> str:
        """Find the actual base branch (main/master/develop)."""
        # Check configured base first
        candidates = [self.config.base_branch] + self.config.base_branch_fallbacks
        for branch in candidates:
            check = self._git("rev-parse", "--verify", f"refs/heads/{branch}", check=False)
            if check:
                return branch
        # Fallback: first branch that isn't current
        current = self.current_branch()
        branches = self.all_branch_names()
        for b in branches:
            if b != current:
                return b
        return current

    def merge_base(self, branch_a: str, branch_b: str) -> str | None:
        """Find merge base (common ancestor). Returns None on shallow clone."""
        return self._git("merge-base", branch_a, branch_b, check=False)

    def all_branch_names(self) -> list[str]:
        """List all local branch names."""
        output = self._git("branch", "--format=%(refname:short)")
        if not output:
            return []
        return [b.strip() for b in output.splitlines() if b.strip()]

    def all_branches(self) -> list[BranchInfo]:
        """All local branches with metadata and relevance scoring."""
        current = self.current_branch()
        base = self.resolve_base_branch()
        now = datetime.now(timezone.utc)

        output = self._git(
            "branch",
            "--format=%(refname:short)|%(committerdate:iso-strict)|%(upstream:track)",
        )
        if not output:
            return []

        branches: list[BranchInfo] = []
        for line in output.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 2:
                continue
            name = parts[0].strip()
            date_str = parts[1].strip() if len(parts) > 1 else ""

            last_date = None
            if date_str:
                try:
                    last_date = datetime.fromisoformat(date_str)
                except ValueError:
                    pass

            # Determine if merged into base
            is_merged = False
            if name != base:
                merge_check = self._git(
                    "branch", "--merged", base, "--format=%(refname:short)", check=False
                )
                if merge_check:
                    is_merged = name in merge_check.splitlines()

            # Compute relevance
            relevance = self._compute_relevance(
                name, current, base, last_date, is_merged, now
            )

            branches.append(
                BranchInfo(
                    name=name,
                    last_commit_date=last_date,
                    is_merged=is_merged,
                    is_current=(name == current),
                    relevance=relevance,
                )
            )

        return sorted(branches, key=lambda b: b.relevance, reverse=True)

    def _compute_relevance(
        self,
        name: str,
        current: str,
        base: str,
        last_date: datetime | None,
        is_merged: bool,
        now: datetime,
    ) -> float:
        """Compute branch relevance score."""
        rel = self.config.branch_relevance
        if name == current:
            return rel.get("current", 1.0)
        if name == base:
            return rel.get("base", 0.8)
        if is_merged:
            return rel.get("merged", 0.3)
        if last_date:
            days_ago = (now - last_date).total_seconds() / 86400
            if days_ago > self.config.stale_days:
                return rel.get("stale", 0.1)
            return rel.get("recent", 0.5)
        return rel.get("stale", 0.1)

    def branches_touching_file(self, file_path: str) -> list[str]:
        """Which branches have commits touching this file."""
        output = self._git(
            "log", "--all", "--format=%D", "--", file_path, check=False
        )
        if not output:
            return []
        branches: set[str] = set()
        for line in output.splitlines():
            for ref in line.split(","):
                ref = ref.strip()
                if ref and "HEAD" not in ref and "->" not in ref:
                    branches.add(ref.split("/")[-1])  # strip origin/ prefix
        return sorted(branches)

    def worktree_list(self) -> list[WorktreeInfo]:
        """List all git worktrees."""
        output = self._git("worktree", "list", "--porcelain")
        if not output:
            return []

        worktrees: list[WorktreeInfo] = []
        current_wt: dict[str, str] = {}

        for line in output.splitlines():
            if not line.strip():
                if current_wt.get("worktree"):
                    worktrees.append(
                        WorktreeInfo(
                            path=Path(current_wt["worktree"]),
                            branch=current_wt.get("branch", "").replace(
                                "refs/heads/", ""
                            ),
                            head_commit=current_wt.get("HEAD", ""),
                            is_current=(
                                Path(current_wt["worktree"]).resolve()
                                == self.repo_path.resolve()
                            ),
                        )
                    )
                current_wt = {}
            elif line.startswith("worktree "):
                current_wt["worktree"] = line[9:]
            elif line.startswith("HEAD "):
                current_wt["HEAD"] = line[5:]
            elif line.startswith("branch "):
                current_wt["branch"] = line[7:]

        # Last entry
        if current_wt.get("worktree"):
            worktrees.append(
                WorktreeInfo(
                    path=Path(current_wt["worktree"]),
                    branch=current_wt.get("branch", "").replace("refs/heads/", ""),
                    head_commit=current_wt.get("HEAD", ""),
                    is_current=(
                        Path(current_wt["worktree"]).resolve()
                        == self.repo_path.resolve()
                    ),
                )
            )
        return worktrees

    def get_branch_context(self) -> BranchContext:
        """Get full branch context for current state."""
        current = self.current_branch()
        base = self.resolve_base_branch()
        mb = self.merge_base(current, base) if current != base else None

        divergence = 0
        if mb:
            count = self._git("rev-list", "--count", f"{mb}..HEAD")
            if count:
                try:
                    divergence = int(count)
                except ValueError:
                    pass

        return BranchContext(
            current_branch=current,
            base_branch=base,
            merge_base=mb,
            divergence_commits=divergence,
            worktrees=self.worktree_list(),
        )


# ---------------------------------------------------------------------------
# GitDynamicMemory
# ---------------------------------------------------------------------------


class GitDynamicMemory:
    """On-demand git memory — computes file history dynamically.

    Features:
    - Per-file history with rename tracking (--follow)
    - Cross-branch context (current + base + other branches)
    - Revert detection and filtering
    - Recency-weighted scoring
    - Worktree awareness
    """

    def __init__(
        self,
        repo_path: Path,
        recency_config: RecencyConfig | None = None,
        branch_config: BranchResolverConfig | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.recency = RecencyScorer(recency_config)
        self.branch_resolver = BranchResolver(self.repo_path, branch_config)
        self.revert_detector = RevertDetector()

    def _git(self, *args: str) -> str | None:
        """Run git command, return stdout or None."""
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except (subprocess.CalledProcessError, OSError):
            return None

    def file_history(
        self,
        file_path: str,
        branch: str | None = None,
        cross_branch: bool = True,
        limit: int = 20,
    ) -> FileHistory:
        """Get comprehensive history for a file.

        Args:
            file_path: Path relative to repo root.
            branch: Specific branch (default: current HEAD).
            cross_branch: Include commits from base branch since divergence.
            limit: Max commits to return.

        Returns:
            FileHistory with scored, revert-filtered commits.
        """
        branch_ctx = self.branch_resolver.get_branch_context()

        # Get commits on target branch (with rename following)
        target_branch = branch or branch_ctx.current_branch
        commits = self._get_file_commits(file_path, target_branch, limit * 2)

        # Cross-branch: also get base branch commits since divergence
        cross_commits: list[HistoricalCommit] = []
        if cross_branch and branch_ctx.merge_base and target_branch != branch_ctx.base_branch:
            cross_commits = self._get_file_commits_since(
                file_path, branch_ctx.base_branch, branch_ctx.merge_base, limit
            )
            for c in cross_commits:
                c.branch = branch_ctx.base_branch
                c.branch_relevance = 0.8

        # Tag current branch commits
        for c in commits:
            c.branch = target_branch
            c.branch_relevance = 1.0

        # Merge and deduplicate
        all_commits = self._merge_commits(commits + cross_commits)

        # Detect reverts
        commit_infos = [
            CommitInfo(hash=c.hash, message=c.message) for c in all_commits
        ]
        reverts = self.revert_detector.detect_reverts(commit_infos)
        reverted_hashes = {p.reverted_hash for p in reverts}
        reverting_hashes = {p.reverting_hash for p in reverts}

        for c in all_commits:
            if c.hash in reverted_hashes:
                c.is_reverted = True
                # Find which commit reverted it
                for p in reverts:
                    if p.reverted_hash == c.hash:
                        c.reverted_by = p.reverting_hash
                        break

        # Score by recency
        for c in all_commits:
            c.recency_score = self.recency.weighted_score(
                1.0, c.date, c.branch_relevance
            )

        # Sort by recency score (highest first)
        all_commits.sort(key=lambda c: c.recency_score, reverse=True)

        # Effective history: remove reverted + reverting commits
        excluded = reverted_hashes | reverting_hashes
        effective = [c for c in all_commits if c.hash not in excluded]

        # Compute owners
        owners = self._compute_owners(all_commits)

        return FileHistory(
            file_path=file_path,
            all_commits=all_commits[:limit],
            effective_commits=effective[:limit],
            reverts=reverts,
            branch_context=branch_ctx,
            owners=owners,
        )

    def _get_file_commits(
        self, file_path: str, branch: str, limit: int
    ) -> list[HistoricalCommit]:
        """Get commits touching a file on a specific branch using --follow."""
        output = self._git(
            "log",
            branch,
            f"--max-count={limit}",
            "--follow",
            "--format=%H|%s|%an|%aI",
            "--numstat",
            "--",
            file_path,
        )
        if not output:
            return []
        return self._parse_log_output(output)

    def _get_file_commits_since(
        self, file_path: str, branch: str, since_commit: str, limit: int
    ) -> list[HistoricalCommit]:
        """Get commits touching a file on branch since a specific commit."""
        output = self._git(
            "log",
            f"{since_commit}..{branch}",
            f"--max-count={limit}",
            "--follow",
            "--format=%H|%s|%an|%aI",
            "--numstat",
            "--",
            file_path,
        )
        if not output:
            return []
        return self._parse_log_output(output)

    def _parse_log_output(self, output: str) -> list[HistoricalCommit]:
        """Parse git log output with --format=%H|%s|%an|%aI and --numstat."""
        commits: list[HistoricalCommit] = []
        current: HistoricalCommit | None = None

        for line in output.splitlines():
            if "|" in line and len(line.split("|")) >= 4:
                # Looks like a commit line
                parts = line.split("|", 3)
                if len(parts[0]) >= 7 and all(c in "0123456789abcdef" for c in parts[0][:7]):
                    if current:
                        commits.append(current)
                    try:
                        date = datetime.fromisoformat(parts[3].strip())
                    except (ValueError, IndexError):
                        date = datetime.now(timezone.utc)
                    current = HistoricalCommit(
                        hash=parts[0].strip(),
                        message=parts[1].strip(),
                        author=parts[2].strip(),
                        date=date,
                    )
                    continue

            # Numstat line: additions\tdeletions\tfilename
            if current and "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 3:
                    try:
                        ins = int(parts[0]) if parts[0] != "-" else 0
                        dels = int(parts[1]) if parts[1] != "-" else 0
                        current.insertions += ins
                        current.deletions += dels
                        current.files_changed.append(parts[2])
                    except ValueError:
                        pass

        if current:
            commits.append(current)
        return commits

    def _merge_commits(
        self, commits: list[HistoricalCommit]
    ) -> list[HistoricalCommit]:
        """Deduplicate commits by hash, keeping first occurrence."""
        seen: set[str] = set()
        result: list[HistoricalCommit] = []
        for c in commits:
            if c.hash not in seen:
                seen.add(c.hash)
                result.append(c)
        return result

    def _compute_owners(
        self, commits: list[HistoricalCommit]
    ) -> list[tuple[str, int]]:
        """Compute top authors by commit count."""
        author_counts: dict[str, int] = {}
        for c in commits:
            author_counts[c.author] = author_counts.get(c.author, 0) + 1
        return sorted(author_counts.items(), key=lambda x: x[1], reverse=True)
