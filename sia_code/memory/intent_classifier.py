"""Heuristic commit intent classification.

Classifies commit intent from conventional commit prefixes
and estimates impact from diff statistics. No model required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Conventional commit prefix → intent category
PREFIX_MAP: dict[str, str] = {
    "feat": "feature",
    "fix": "bugfix",
    "refactor": "refactor",
    "perf": "performance",
    "docs": "documentation",
    "test": "testing",
    "tests": "testing",
    "chore": "maintenance",
    "ci": "ci",
    "build": "build",
    "revert": "revert",
    "style": "style",
    "release": "release",
}

# Pattern to extract conventional commit prefix
_PREFIX_RE = re.compile(r"^(\w+)(?:\(.+?\))?[!:]")


@dataclass
class CommitIntent:
    """Classified intent and impact for a commit."""

    intent: str  # "feature", "bugfix", "refactor", etc.
    impact: str  # "high", "medium", "low"
    confidence: float  # 0.0-1.0


class IntentClassifier:
    """Classify commit intent from message prefix + diff stats.

    Pure heuristic — no model needed. Fast and deterministic.
    """

    def classify(
        self, message: str, files_changed: int = 0, lines_changed: int = 0
    ) -> CommitIntent:
        """Classify a commit's intent and impact.

        Args:
            message: Commit message (first line).
            files_changed: Number of files in commit.
            lines_changed: Total insertions + deletions.

        Returns:
            CommitIntent with category, impact level, and confidence.
        """
        intent = self._classify_intent(message)
        impact = self._estimate_impact(files_changed, lines_changed)
        confidence = 0.9 if intent != "unknown" else 0.3
        return CommitIntent(intent=intent, impact=impact, confidence=confidence)

    def _classify_intent(self, message: str) -> str:
        """Extract intent from conventional commit prefix."""
        first_line = message.split("\n")[0].strip().lower()

        # Try conventional commit pattern: type(scope): description
        m = _PREFIX_RE.match(first_line)
        if m:
            prefix = m.group(1)
            if prefix in PREFIX_MAP:
                return PREFIX_MAP[prefix]

        # Fallback: keyword detection in message
        if any(w in first_line for w in ("fix", "bug", "patch", "hotfix")):
            return "bugfix"
        if any(w in first_line for w in ("add", "implement", "feature", "new")):
            return "feature"
        if any(w in first_line for w in ("refactor", "restructure", "clean")):
            return "refactor"
        if any(w in first_line for w in ("perf", "optim", "speed", "fast")):
            return "performance"
        if any(w in first_line for w in ("revert", "undo", "rollback")):
            return "revert"
        if any(w in first_line for w in ("release", "bump", "version")):
            return "release"
        if any(w in first_line for w in ("doc", "readme", "comment")):
            return "documentation"
        if any(w in first_line for w in ("test", "spec", "coverage")):
            return "testing"

        return "unknown"

    def _estimate_impact(self, files_changed: int, lines_changed: int) -> str:
        """Estimate impact from diff size."""
        if lines_changed > 100 or files_changed > 5:
            return "high"
        if lines_changed > 30 or files_changed > 2:
            return "medium"
        return "low"
