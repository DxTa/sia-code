"""Revert detection for git commits.

Detects revert commits via message patterns and marks original commits as reverted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Patterns that identify a revert commit
REVERT_PATTERNS: list[re.Pattern] = [
    re.compile(r'^[Rr]evert "(.+)"'),  # Git default: Revert "original message"
    re.compile(r"^[Rr]evert:\s*(.+)"),  # Conventional commit: revert: message
    re.compile(r"[Tt]his reverts commit ([a-f0-9]{7,40})"),  # Body reference
]


@dataclass
class RevertPair:
    """A pair of commits: the original and the commit that reverts it."""

    reverted_hash: str
    """Hash of the commit that was reverted."""

    reverting_hash: str
    """Hash of the commit that performs the revert."""

    matched_by: str
    """Which pattern matched (for debugging)."""


@dataclass
class CommitInfo:
    """Minimal commit info for revert detection."""

    hash: str
    message: str
    body: str = ""


class RevertDetector:
    """Detects revert relationships between commits.

    Strategy (message-based, fast):
    1. Match revert patterns in commit message/body
    2. For message-match reverts: find original by matching quoted message
    3. For hash-reference reverts: direct hash lookup
    """

    def detect_reverts(self, commits: list[CommitInfo]) -> list[RevertPair]:
        """Find all revert relationships in a list of commits.

        Args:
            commits: List of commits to analyze (newer first).

        Returns:
            List of RevertPair identifying original→reverting relationships.
        """
        pairs: list[RevertPair] = []
        # Build message→hash index for message-matching
        msg_to_hash: dict[str, str] = {}
        for c in commits:
            # Use first line of message for matching
            first_line = c.message.split("\n")[0].strip()
            msg_to_hash[first_line] = c.hash

        for c in commits:
            full_text = f"{c.message}\n{c.body}"
            pair = self._check_commit(c, full_text, msg_to_hash)
            if pair:
                pairs.append(pair)

        return pairs

    def _check_commit(
        self, commit: CommitInfo, full_text: str, msg_to_hash: dict[str, str]
    ) -> RevertPair | None:
        """Check if a single commit is a revert."""
        first_line = commit.message.split("\n")[0].strip()

        # Pattern 1: Revert "original message" (exact quote match)
        m = REVERT_PATTERNS[0].match(first_line)
        if m:
            original_msg = m.group(1).strip()
            original_hash = msg_to_hash.get(original_msg)
            if original_hash and original_hash != commit.hash:
                return RevertPair(
                    reverted_hash=original_hash,
                    reverting_hash=commit.hash,
                    matched_by="message_quote",
                )

        # Pattern 2: revert: message (conventional commit)
        m = REVERT_PATTERNS[1].match(first_line)
        if m:
            revert_desc = m.group(1).strip()
            # Try exact match first
            original_hash = msg_to_hash.get(revert_desc)
            if original_hash and original_hash != commit.hash:
                return RevertPair(
                    reverted_hash=original_hash,
                    reverting_hash=commit.hash,
                    matched_by="conventional_prefix",
                )
            # Fuzzy: find recent commit with highest keyword overlap
            best = self._fuzzy_match(revert_desc, msg_to_hash, commit.hash)
            if best:
                return RevertPair(
                    reverted_hash=best,
                    reverting_hash=commit.hash,
                    matched_by="conventional_fuzzy",
                )

        # Pattern 3: "This reverts commit <hash>" in body
        m = REVERT_PATTERNS[2].search(full_text)
        if m:
            reverted_hash = m.group(1)
            return RevertPair(
                reverted_hash=reverted_hash,
                reverting_hash=commit.hash,
                matched_by="hash_reference",
            )

        return None

    def _fuzzy_match(
        self, revert_desc: str, msg_to_hash: dict[str, str], exclude_hash: str
    ) -> str | None:
        """Find the most likely original commit via keyword overlap.

        Uses Jaccard similarity on normalized word sets.
        Threshold: >= 0.3 overlap to consider a match.
        """
        revert_words = self._normalize_words(revert_desc)
        if len(revert_words) < 2:
            return None

        best_score = 0.0
        best_hash: str | None = None

        for msg, h in msg_to_hash.items():
            if h == exclude_hash:
                continue
            msg_words = self._normalize_words(msg)
            if not msg_words:
                continue
            # Jaccard similarity
            intersection = revert_words & msg_words
            union = revert_words | msg_words
            score = len(intersection) / len(union) if union else 0
            if score > best_score and score >= 0.3:
                best_score = score
                best_hash = h

        return best_hash

    @staticmethod
    def _normalize_words(text: str) -> set[str]:
        """Extract significant words (lowercase, strip punctuation, drop short)."""
        # Remove common prefixes
        for prefix in ("feat:", "fix:", "chore:", "refactor:", "revert:", "docs:", "ci:"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
        words = set(re.findall(r'[a-z0-9]+', text.lower()))
        # Drop very short words and common noise
        noise = {"the", "to", "for", "in", "of", "a", "an", "and", "or", "is", "was"}
        return {w for w in words if len(w) > 2 and w not in noise}

    def filter_reverted(self, commits: list[CommitInfo]) -> list[CommitInfo]:
        """Return commits with reverted ones removed.

        Removes BOTH the reverted commit AND the reverting commit from the
        effective history (neither adds meaningful signal).
        """
        pairs = self.detect_reverts(commits)
        excluded: set[str] = set()
        for pair in pairs:
            excluded.add(pair.reverted_hash)
            excluded.add(pair.reverting_hash)
        return [c for c in commits if c.hash not in excluded]

    def reverted_set(self, commits: list[CommitInfo]) -> set[str]:
        """Return set of commit hashes that have been reverted."""
        pairs = self.detect_reverts(commits)
        return {p.reverted_hash for p in pairs}
