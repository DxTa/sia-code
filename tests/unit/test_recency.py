"""Tests for RecencyScorer."""

import math
from datetime import datetime, timedelta, timezone

from sia_code.memory.recency import RecencyConfig, RecencyScorer


def _days_ago(days: float) -> datetime:
    """Create a commit date exactly N days ago (truncated to seconds to avoid fp edge)."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.replace(microsecond=0)


class TestRecencyScorer:
    def test_within_working_window_full_weight(self):
        scorer = RecencyScorer()
        assert scorer.score(_days_ago(0)) == 1.0
        assert scorer.score(_days_ago(7)) == 1.0
        assert scorer.score(_days_ago(13.9)) == 1.0  # just inside window

    def test_one_halflife_beyond_window(self):
        scorer = RecencyScorer()  # halflife=30, window=14
        # 44 days old = 30 days beyond window = one halflife → 0.5
        score = scorer.score(_days_ago(44))
        assert abs(score - 0.5) < 0.01

    def test_two_halflives_beyond_window(self):
        scorer = RecencyScorer()
        # 74 days old = 60 days beyond window = two halflives → 0.25
        score = scorer.score(_days_ago(74))
        assert abs(score - 0.25) < 0.01

    def test_future_commit_full_weight(self):
        scorer = RecencyScorer()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        assert scorer.score(future) == 1.0

    def test_custom_config(self):
        config = RecencyConfig(halflife_days=7.0, working_window_days=3)
        scorer = RecencyScorer(config)
        # 2.9 days old → still in window
        assert scorer.score(_days_ago(2.9)) == 1.0
        # 10 days old = 7 days beyond window = one halflife → 0.5
        score = scorer.score(_days_ago(10))
        assert abs(score - 0.5) < 0.01

    def test_weighted_score(self):
        scorer = RecencyScorer()
        date = _days_ago(44)  # one halflife beyond window → recency=0.5
        # base=0.8, recency=0.5, branch=0.8
        result = scorer.weighted_score(0.8, date, branch_relevance=0.8)
        expected = 0.8 * 0.5 * 0.8
        assert abs(result - expected) < 0.01

    def test_deterministic_with_now_param(self):
        scorer = RecencyScorer()
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        commit = datetime(2024, 5, 1, tzinfo=timezone.utc)  # 31 days ago
        # 31 - 14 = 17 days beyond window
        expected = math.exp(-math.log(2) / 30.0 * 17)
        score = scorer.score(commit, now=now)
        assert abs(score - expected) < 0.001

    def test_naive_datetime_treated_as_utc(self):
        scorer = RecencyScorer()
        naive_commit = datetime.now() - timedelta(days=7)
        # Should not crash, treated as UTC
        score = scorer.score(naive_commit)
        assert score == 1.0
