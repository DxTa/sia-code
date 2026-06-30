"""Recency scoring for git commits.

Exponential time decay with a configurable working window.
Commits within the working window get full weight (1.0).
Beyond the window, score decays exponentially with configurable halflife.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class RecencyConfig:
    """Configuration for recency scoring."""

    halflife_days: float = 30.0
    """Time in days for score to decay to 50% beyond working window."""

    working_window_days: int = 14
    """Commits within this many days get full weight (no decay)."""


class RecencyScorer:
    """Score commits by recency using exponential decay.

    Behaviour:
    - Commits within working_window_days → weight = 1.0
    - Beyond window: weight = exp(-lambda * (days_ago - window))
    - lambda = ln(2) / halflife_days

    Examples (default config: window=14d, halflife=30d):
      - 7 days old  → 1.0
      - 14 days old → 1.0 (still in window)
      - 44 days old → 0.5 (30 days beyond window = one halflife)
      - 74 days old → 0.25 (60 days beyond)
      - 134 days old → 0.0625 (120 days beyond)
    """

    def __init__(self, config: RecencyConfig | None = None):
        self.config = config or RecencyConfig()
        self._decay_lambda = math.log(2) / self.config.halflife_days

    @property
    def halflife_days(self) -> float:
        return self.config.halflife_days

    @property
    def working_window_days(self) -> int:
        return self.config.working_window_days

    def score(self, commit_date: datetime, now: datetime | None = None) -> float:
        """Compute recency weight for a commit.

        Args:
            commit_date: When the commit was authored (timezone-aware preferred).
            now: Reference time (default: utcnow). Pass for deterministic tests.

        Returns:
            Weight in (0.0, 1.0].
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # Normalize to UTC for comparison
        if commit_date.tzinfo is None:
            commit_date = commit_date.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        days_ago = (now - commit_date).total_seconds() / 86400.0
        if days_ago <= 0:
            return 1.0
        if days_ago <= self.config.working_window_days:
            return 1.0

        effective_days = days_ago - self.config.working_window_days
        return math.exp(-self._decay_lambda * effective_days)

    def weighted_score(
        self,
        base_score: float,
        commit_date: datetime,
        branch_relevance: float = 1.0,
        now: datetime | None = None,
    ) -> float:
        """Combined scoring: base * recency * branch_relevance.

        Args:
            base_score: Raw score (e.g., coupling, importance).
            commit_date: When the commit was authored.
            branch_relevance: Branch relevance weight (1.0 = current, 0.8 = base, etc.).
            now: Reference time for deterministic tests.

        Returns:
            Weighted score incorporating all factors.
        """
        return base_score * self.score(commit_date, now=now) * branch_relevance
