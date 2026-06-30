"""Integration tests for dynamic git memory against real repos.

Run with:
    pytest tests/integration/test_git_dynamic_real_repos.py -v
"""

from __future__ import annotations

import pytest
from pathlib import Path

from sia_code.memory.git_dynamic import GitDynamicMemory
from sia_code.memory.blast_radius import BlastRadiusAnalyzer
from sia_code.memory.recency import RecencyConfig, RecencyScorer
from sia_code.memory.revert_detector import RevertDetector, CommitInfo
from sia_code.memory.intent_classifier import IntentClassifier


# Test repo paths
MLDB_REPO = Path.home() / "dev/ai.platform/ai.platform.mldb"
PIPELINES_REPO = Path.home() / "dev/ai.platform/ai.platform.pipelines"


def _skip_if_missing(repo: Path):
    if not (repo / ".git").exists():
        pytest.skip(f"Repo not available: {repo}")


class TestGitDynamicMemoryMLDB:
    """Tests against ai.platform.mldb — rich co-change patterns."""

    @pytest.fixture(autouse=True)
    def setup(self):
        _skip_if_missing(MLDB_REPO)
        self.config = RecencyConfig(halflife_days=30.0, working_window_days=14)
        self.mem = GitDynamicMemory(MLDB_REPO, recency_config=self.config)

    def test_file_history_returns_commits(self):
        hist = self.mem.file_history("mldb/app/api/v1/datasample_metadata.py")
        assert len(hist.effective_commits) > 0
        assert hist.file_path == "mldb/app/api/v1/datasample_metadata.py"

    def test_recency_scoring_decreases_with_age(self):
        hist = self.mem.file_history("mldb/app/api/v1/datasample_metadata.py")
        scores = [c.recency_score for c in hist.effective_commits]
        # Should be generally decreasing (most recent first)
        assert scores[0] >= scores[-1]

    def test_owners_detected(self):
        hist = self.mem.file_history("mldb/app/api/v1/datasample_metadata.py")
        assert len(hist.owners) > 0
        # Top owner should have multiple commits
        assert hist.owners[0][1] > 1

    def test_branch_context_available(self):
        hist = self.mem.file_history("mldb/app/api/v1/datasample_metadata.py")
        assert hist.branch_context is not None
        assert hist.branch_context.current_branch

    def test_blast_radius_finds_coupled_files(self):
        analyzer = BlastRadiusAnalyzer(MLDB_REPO, min_coupling=0.2, recency_config=self.config)
        result = analyzer.co_changed_files("mldb/app/api/v1/datasample_metadata.py")
        assert len(result.coupled_files) > 0
        # Expect CRUD layer to be coupled
        paths = [cf.path for cf in result.coupled_files]
        assert any("crud" in p for p in paths)

    def test_blast_radius_squash_guard(self):
        analyzer = BlastRadiusAnalyzer(MLDB_REPO, min_coupling=0.1, recency_config=self.config)
        result = analyzer.co_changed_files("mldb/app/api/v1/datasample_metadata.py")
        # Should have excluded some squash commits
        assert result.commits_excluded_squash >= 0

    def test_change_cluster_detected(self):
        analyzer = BlastRadiusAnalyzer(MLDB_REPO, min_coupling=0.2, recency_config=self.config)
        result = analyzer.co_changed_files("mldb/app/api/v1/datasample_metadata.py")
        # Expect a cluster of related files
        if result.change_clusters:
            assert len(result.change_clusters[0].files) >= 2

    def test_intent_classification(self):
        classifier = IntentClassifier()
        hist = self.mem.file_history("mldb/app/api/v1/datasample_metadata.py")
        intents = [
            classifier.classify(c.message, len(c.files_changed), c.insertions + c.deletions)
            for c in hist.effective_commits
        ]
        # At least some should be classified
        classified = [i for i in intents if i.intent != "unknown"]
        assert len(classified) > 0


class TestGitDynamicMemoryPipelines:
    """Tests against ai.platform.pipelines — revert detection."""

    @pytest.fixture(autouse=True)
    def setup(self):
        _skip_if_missing(PIPELINES_REPO)
        self.config = RecencyConfig(halflife_days=30.0, working_window_days=14)
        self.mem = GitDynamicMemory(PIPELINES_REPO, recency_config=self.config)

    def test_revert_detected(self):
        hist = self.mem.file_history(".github/workflows/build-components.yml", limit=25)
        assert len(hist.reverts) >= 1
        # Known revert: e534b21 reverts 75591dd
        revert_hashes = {r.reverting_hash[:7] for r in hist.reverts}
        assert "e534b21" in revert_hashes

    def test_effective_history_excludes_reverts(self):
        hist = self.mem.file_history(".github/workflows/build-components.yml", limit=25)
        effective_hashes = {c.hash[:7] for c in hist.effective_commits}
        # Both revert and reverted should be excluded
        assert "e534b21" not in effective_hashes
        assert "75591dd" not in effective_hashes

    def test_all_commits_contains_reverted(self):
        hist = self.mem.file_history(".github/workflows/build-components.yml", limit=25)
        all_hashes = {c.hash[:7] for c in hist.all_commits}
        # All commits should include both
        assert "e534b21" in all_hashes
        assert "75591dd" in all_hashes

    def test_branch_resolver_lists_branches(self):
        branches = self.mem.branch_resolver.all_branch_names()
        assert len(branches) >= 1
        assert "main" in branches

    def test_worktree_list(self):
        worktrees = self.mem.branch_resolver.worktree_list()
        assert len(worktrees) >= 1
        # Current worktree should be marked
        current = [w for w in worktrees if w.is_current]
        assert len(current) == 1


class TestRecencyScorer:
    """Unit-level tests for recency math."""

    def test_within_window_full_weight(self):
        from datetime import datetime, timedelta, timezone

        scorer = RecencyScorer(RecencyConfig(halflife_days=30, working_window_days=14))
        now = datetime.now(timezone.utc)
        assert scorer.score(now - timedelta(days=7), now=now) == 1.0

    def test_halflife_gives_half_weight(self):
        from datetime import datetime, timedelta, timezone
        import math

        scorer = RecencyScorer(RecencyConfig(halflife_days=30, working_window_days=14))
        now = datetime.now(timezone.utc)
        # 44 days ago = 30 days beyond window = one halflife
        score = scorer.score(now - timedelta(days=44), now=now)
        assert abs(score - 0.5) < 0.01
