"""Semantic diff analysis with auto-escalating local model.

Derives evolution narratives from file history using:
1. Heuristic analysis (always runs, instant, grounded)
2. Local model rewrite (auto-opt-in when transformers importable)

Model auto-selects best flan-t5 variant for the machine:
- flan-t5-large (780M) on 16+ GB with MPS/CUDA
- flan-t5-base (250M) otherwise
- Heuristic-only if no transformers

The model is a FACT REWRITER — it never invents intent.
It rewrites structured heuristic output into fluent prose.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .git_dynamic import FileHistory, HistoricalCommit

logger = logging.getLogger(__name__)


@dataclass
class ChangeMeaning:
    """Semantic meaning for a single commit (heuristic-derived)."""

    intent: str
    summary: str  # first line of commit message (grounded)
    impact: str
    affected_concepts: list[str]


@dataclass
class EvolutionNarrative:
    """Model-generated or heuristic narrative of file evolution."""

    file_path: str
    narrative: str
    key_phases: list[str]
    change_meanings: list[ChangeMeaning]
    model_used: str | None = None  # which model produced the narrative (None = heuristic)


def _auto_select_model() -> str:
    """Auto-select best flan-t5 variant for this machine.

    - 16+ GB RAM + MPS/CUDA → flan-t5-large (780M, better quality)
    - Otherwise → flan-t5-base (250M, lighter)
    """
    try:
        import torch
        import psutil

        ram_gb = psutil.virtual_memory().total / (1024**3)
        has_accel = (
            (hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
            or torch.cuda.is_available()
        )
        if has_accel and ram_gb >= 16:
            return "google/flan-t5-large"
        return "google/flan-t5-base"
    except ImportError:
        return "google/flan-t5-base"


class DiffSemanticAnalyzer:
    """Analyze file evolution semantically.

    Auto-opts-in to local model when transformers is importable.
    Model only rewrites heuristic facts — never processes raw diffs.
    """

    def __init__(self, repo_path: Path, model_name: str | None = None):
        """
        Args:
            repo_path: Path to git repository.
            model_name: Override model (default: auto-select based on hardware).
        """
        self.repo_path = Path(repo_path).resolve()
        self._model_name = model_name
        self._model_checked = False
        self._can_model = False
        self._summarizer = None

    def _can_use_model(self) -> bool:
        """Cheap import check — auto-opt-in."""
        if not self._model_checked:
            try:
                import transformers  # noqa: F401

                self._can_model = True
            except ImportError:
                self._can_model = False
            self._model_checked = True
        return self._can_model

    def _get_model_name(self) -> str:
        """Resolve model name: explicit override or auto-select."""
        if self._model_name:
            return self._model_name
        return _auto_select_model()

    def _get_summarizer(self):
        """Lazy-load summarizer with auto-selected model."""
        if self._summarizer is None:
            from .summarizer import CommitSummarizer

            model = self._get_model_name()
            logger.info(f"Auto-selected narrative model: {model}")
            self._summarizer = CommitSummarizer(model)
        return self._summarizer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_change(self, commit: "HistoricalCommit") -> ChangeMeaning:
        """Derive per-commit meaning (heuristic only — fast, grounded)."""
        from .intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        intent_result = classifier.classify(
            commit.message, len(commit.files_changed), commit.insertions + commit.deletions
        )
        concepts = self._extract_concepts(commit)

        return ChangeMeaning(
            intent=intent_result.intent,
            summary=commit.message.split("\n")[0].strip(),
            impact=intent_result.impact,
            affected_concepts=concepts,
        )

    def summarize_evolution(self, history: "FileHistory") -> EvolutionNarrative:
        """Generate evolution narrative for a file.

        Always computes heuristic facts first. Then auto-rewrites with model
        if transformers is importable (no config needed).
        """
        commits = history.effective_commits
        if not commits:
            return EvolutionNarrative(
                file_path=history.file_path,
                narrative="No history available.",
                key_phases=[],
                change_meanings=[],
            )

        # 1. Heuristic analysis (always, instant)
        meanings = [self.analyze_change(c) for c in commits[:15]]
        phases = self._detect_phases(meanings)
        concepts = self._collect_concepts(meanings)
        heuristic_narrative = self._template_narrative(
            history.file_path, meanings, phases, concepts
        )

        # 2. Model rewrite (auto if importable)
        model_used = None
        narrative = heuristic_narrative
        if self._can_use_model():
            model_result = self._rewrite_with_model(heuristic_narrative, phases, concepts)
            if model_result:
                narrative = model_result
                model_used = self._get_model_name()

        return EvolutionNarrative(
            file_path=history.file_path,
            narrative=narrative,
            key_phases=phases,
            change_meanings=meanings,
            model_used=model_used,
        )

    # ------------------------------------------------------------------
    # Model rewriting (single call, fact-based input)
    # ------------------------------------------------------------------

    def _rewrite_with_model(
        self, facts: str, phases: list[str], concepts: list[str]
    ) -> str | None:
        """Single generate() — model rewrites structured facts into fluent prose.

        The model NEVER sees raw diffs. Input is heuristic-derived text only.
        This prevents hallucinated intent.
        """
        try:
            summarizer = self._get_summarizer()
            prompt = (
                "Rewrite this file evolution summary as a fluent developer-facing paragraph:\n\n"
                f"{facts}\n"
            )
            if phases:
                prompt += f"Development phases: {', '.join(phases)}\n"
            if concepts:
                prompt += f"Key concepts: {', '.join(concepts[:6])}\n"
            prompt += "\nEvolution paragraph:"

            # Single call, greedy decode (fast), max 150 tokens
            result = summarizer.generate(prompt, max_length=150, num_beams=1)
            if result and len(result) > 20:
                return result
            return None
        except Exception as e:
            logger.debug(f"Model rewrite failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Heuristic internals
    # ------------------------------------------------------------------

    def _template_narrative(
        self,
        file_path: str,
        meanings: list[ChangeMeaning],
        phases: list[str],
        concepts: list[str],
    ) -> str:
        """Deterministic template narrative from heuristic facts."""
        total = len(meanings)
        intent_counts: dict[str, int] = {}
        for m in meanings:
            if m.intent != "unknown":
                intent_counts[m.intent] = intent_counts.get(m.intent, 0) + 1

        # Build parts
        parts = []
        for intent, count in sorted(intent_counts.items(), key=lambda x: -x[1]):
            parts.append(f"{count} {intent}{'s' if count > 1 else ''}")

        intent_str = ", ".join(parts) if parts else "mixed changes"
        concept_str = ", ".join(concepts[:5]) if concepts else "general"
        phase_str = "; ".join(phases) if phases else "single phase"

        return (
            f"{file_path}: {total} changes ({intent_str}). "
            f"Phases: {phase_str}. "
            f"Concepts: {concept_str}."
        )

    def _detect_phases(self, meanings: list[ChangeMeaning]) -> list[str]:
        """Detect development phases from sequential intent patterns."""
        if not meanings:
            return []

        phases: list[str] = []
        current_intent = meanings[0].intent
        current_count = 1

        for m in meanings[1:]:
            if m.intent == current_intent:
                current_count += 1
            else:
                if current_count >= 2:
                    phases.append(f"{current_intent} ({current_count})")
                current_intent = m.intent
                current_count = 1

        if current_count >= 2:
            phases.append(f"{current_intent} ({current_count})")

        return phases

    def _collect_concepts(self, meanings: list[ChangeMeaning]) -> list[str]:
        """Merge concepts from all commit meanings."""
        all_concepts: dict[str, int] = {}
        for m in meanings:
            for c in m.affected_concepts:
                all_concepts[c] = all_concepts.get(c, 0) + 1
        # Sort by frequency
        return [c for c, _ in sorted(all_concepts.items(), key=lambda x: -x[1])][:8]

    def _extract_concepts(self, commit: "HistoricalCommit") -> list[str]:
        """Extract domain concepts from a single commit."""
        concepts: set[str] = set()

        # From file paths
        for fp in commit.files_changed[:5]:
            parts = Path(fp).parts
            for part in parts:
                if part in ("src", "lib", "app", "tests", "test", "__init__.py", "v1"):
                    continue
                stem = Path(part).stem
                if len(stem) > 3 and stem != "__init__":
                    words = re.findall(r"[a-z]+", stem.lower())
                    concepts.update(w for w in words if len(w) > 3)

        # From message
        msg_words = re.findall(r"[a-z]+", commit.message.lower())
        noise = {"the", "for", "and", "this", "that", "with", "from", "into", "have", "been"}
        concepts.update(w for w in msg_words if len(w) > 4 and w not in noise)

        return sorted(concepts)[:8]
