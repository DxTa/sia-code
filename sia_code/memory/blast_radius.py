"""Blast radius analysis — co-change coupling from git history.

Identifies files that frequently change together with a target file,
with squash-merge dilution guard and recency weighting.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .recency import RecencyConfig, RecencyScorer


@dataclass
class CoupledFile:
    """A file that co-changes with the target."""

    path: str
    coupling_score: float  # 0.0-1.0 (recency-weighted)
    raw_coupling: float  # 0.0-1.0 (unweighted)
    co_change_count: int
    total_changes_target: int
    total_changes_self: int
    recent_co_change: datetime | None = None


@dataclass
class ChangeCluster:
    """A group of files that form a logical change unit."""

    files: list[str]
    cohesion_score: float  # How tightly coupled the group is
    common_commits: int  # Commits where all files appear


@dataclass
class BlastRadius:
    """Complete blast radius analysis for a file."""

    target_file: str
    coupled_files: list[CoupledFile] = field(default_factory=list)
    change_clusters: list[ChangeCluster] = field(default_factory=list)
    total_commits_analyzed: int = 0
    commits_excluded_squash: int = 0


class BlastRadiusAnalyzer:
    """Analyze co-change coupling between files in git history.

    Features:
    - Frequency-based coupling score
    - Squash-merge dilution guard (skip commits with too many files)
    - Recency weighting (recent co-changes score higher)
    - Change cluster detection
    """

    def __init__(
        self,
        repo_path: Path,
        lookback: int = 200,
        min_coupling: float = 0.3,
        max_files_per_commit: int = 20,
        recency_config: RecencyConfig | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.lookback = lookback
        self.min_coupling = min_coupling
        self.max_files_per_commit = max_files_per_commit
        self.recency = RecencyScorer(recency_config)

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

    def co_changed_files(
        self, file_path: str, branch: str | None = None
    ) -> BlastRadius:
        """Find files that co-change with target file.

        Algorithm:
        1. Get all commits touching target (up to lookback)
        2. Apply squash dilution guard (skip commits with > max_files)
        3. Count co-occurrence of other files
        4. Weight by recency (recent co-changes count more)
        5. Compute coupling = weighted_co_occurrences / max(commits_a, commits_b)

        Args:
            file_path: Target file path (relative to repo root).
            branch: Branch to analyze (default: current HEAD).

        Returns:
            BlastRadius with coupled files sorted by score.
        """
        # Get commits touching target file with files changed per commit
        commits = self._get_commits_with_files(file_path, branch)

        total_analyzed = len(commits)
        excluded_squash = 0

        # Co-occurrence tracking
        co_occurrences: dict[str, float] = {}  # file → weighted count
        co_dates: dict[str, datetime] = {}  # file → most recent co-change
        raw_co_counts: dict[str, int] = {}  # file → raw count
        target_commit_count = 0

        for commit_hash, files, date in commits:
            # Squash dilution guard
            if len(files) > self.max_files_per_commit:
                excluded_squash += 1
                continue

            target_commit_count += 1
            recency_weight = self.recency.score(date)

            for f in files:
                if f == file_path:
                    continue
                co_occurrences[f] = co_occurrences.get(f, 0.0) + recency_weight
                raw_co_counts[f] = raw_co_counts.get(f, 0) + 1
                if f not in co_dates or date > co_dates[f]:
                    co_dates[f] = date

        if target_commit_count == 0:
            return BlastRadius(
                target_file=file_path,
                total_commits_analyzed=total_analyzed,
                commits_excluded_squash=excluded_squash,
            )

        # Compute coupling scores
        coupled: list[CoupledFile] = []
        for f, weighted_count in co_occurrences.items():
            # Get total commits for this file (approximated by co-change context)
            other_total = self._count_file_commits(f, branch)
            if other_total == 0:
                other_total = raw_co_counts[f]

            # Coupling = weighted_co_occ / max(target_commits, other_commits)
            denominator = max(target_commit_count, other_total)
            coupling = weighted_count / denominator if denominator > 0 else 0.0
            raw_coupling = raw_co_counts[f] / denominator if denominator > 0 else 0.0

            if raw_coupling >= self.min_coupling:
                coupled.append(
                    CoupledFile(
                        path=f,
                        coupling_score=min(coupling, 1.0),
                        raw_coupling=min(raw_coupling, 1.0),
                        co_change_count=raw_co_counts[f],
                        total_changes_target=target_commit_count,
                        total_changes_self=other_total,
                        recent_co_change=co_dates.get(f),
                    )
                )

        coupled.sort(key=lambda c: c.coupling_score, reverse=True)

        # Detect change clusters
        clusters = self._detect_clusters(coupled, commits, file_path)

        return BlastRadius(
            target_file=file_path,
            coupled_files=coupled,
            change_clusters=clusters,
            total_commits_analyzed=total_analyzed,
            commits_excluded_squash=excluded_squash,
        )

    def change_cluster(self, file_paths: list[str], branch: str | None = None) -> BlastRadius:
        """Find blast radius for multiple files (union of their coupled files).

        Useful when a query spans multiple related files.
        """
        all_coupled: dict[str, CoupledFile] = {}
        total_analyzed = 0
        total_excluded = 0

        for fp in file_paths:
            radius = self.co_changed_files(fp, branch)
            total_analyzed += radius.total_commits_analyzed
            total_excluded += radius.commits_excluded_squash
            for cf in radius.coupled_files:
                if cf.path in file_paths:
                    continue  # Don't include query files themselves
                if cf.path in all_coupled:
                    # Take max coupling
                    if cf.coupling_score > all_coupled[cf.path].coupling_score:
                        all_coupled[cf.path] = cf
                else:
                    all_coupled[cf.path] = cf

        coupled = sorted(all_coupled.values(), key=lambda c: c.coupling_score, reverse=True)

        return BlastRadius(
            target_file=",".join(file_paths),
            coupled_files=coupled,
            total_commits_analyzed=total_analyzed,
            commits_excluded_squash=total_excluded,
        )

    def _get_commits_with_files(
        self, file_path: str, branch: str | None
    ) -> list[tuple[str, list[str], datetime]]:
        """Get commits touching file with ALL files changed per commit.

        Two-step approach:
        1. Get commit hashes touching target (with --follow for renames)
        2. For each commit, get all files changed

        Returns: list of (hash, [all_files_in_commit], date)
        """
        branch_arg = branch or "HEAD"

        # Step 1: Get commit hashes that touch this file (with rename tracking)
        output = self._git(
            "log",
            branch_arg,
            f"--max-count={self.lookback}",
            "--follow",
            "--format=%H|%aI",
            "--",
            file_path,
        )
        if not output:
            return []

        # Parse commit hashes + dates
        commit_refs: list[tuple[str, datetime]] = []
        for line in output.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|", 1)
            if len(parts[0]) >= 7 and all(c in "0123456789abcdef" for c in parts[0][:7]):
                try:
                    date = datetime.fromisoformat(parts[1].strip())
                except (ValueError, IndexError):
                    date = datetime.now(timezone.utc)
                commit_refs.append((parts[0].strip(), date))

        if not commit_refs:
            return []

        # Step 2: For each commit, get all files changed
        commits: list[tuple[str, list[str], datetime]] = []
        for commit_hash, date in commit_refs:
            files_output = self._git(
                "diff-tree", "--no-commit-id", "-r", "--name-only", commit_hash
            )
            if files_output:
                files = [f.strip() for f in files_output.splitlines() if f.strip()]
            else:
                files = []
            commits.append((commit_hash, files, date))

        return commits

    def _count_file_commits(self, file_path: str, branch: str | None) -> int:
        """Quick count of commits touching a file."""
        branch_arg = branch or "HEAD"
        output = self._git(
            "rev-list",
            "--count",
            branch_arg,
            "--",
            file_path,
        )
        if output:
            try:
                return int(output.strip())
            except ValueError:
                pass
        return 0

    def _detect_clusters(
        self,
        coupled: list[CoupledFile],
        commits: list[tuple[str, list[str], datetime]],
        target: str,
    ) -> list[ChangeCluster]:
        """Detect groups of files that consistently change together.

        Simple approach: files that appear together in >= 60% of the
        target file's commits form a cluster.
        """
        if not coupled or not commits:
            return []

        # Only consider top coupled files
        top_files = [c.path for c in coupled[:10]]
        if not top_files:
            return []

        # Count how often pairs appear together in target's commits
        valid_commits = [
            (h, files, d)
            for h, files, d in commits
            if len(files) <= self.max_files_per_commit
        ]
        if len(valid_commits) < 3:
            return []

        # Find files that appear in >= 60% of target's commits
        file_in_commits: dict[str, int] = {}
        for _, files, _ in valid_commits:
            for f in files:
                if f in top_files:
                    file_in_commits[f] = file_in_commits.get(f, 0) + 1

        cluster_members = [
            f
            for f, count in file_in_commits.items()
            if count / len(valid_commits) >= 0.6
        ]

        if len(cluster_members) >= 2:
            cohesion = sum(
                file_in_commits[f] / len(valid_commits) for f in cluster_members
            ) / len(cluster_members)
            return [
                ChangeCluster(
                    files=[target] + cluster_members,
                    cohesion_score=cohesion,
                    common_commits=min(file_in_commits[f] for f in cluster_members),
                )
            ]
        return []
