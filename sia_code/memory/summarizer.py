"""Commit message summarization using local LLM (flan-t5-base)."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CommitSummarizer:
    """Summarize commit messages using flan-t5-base (248MB)."""

    def __init__(self, model_name: str = "google/flan-t5-base"):
        """Initialize summarizer with model name.

        Args:
            model_name: HuggingFace model name (default: google/flan-t5-base)
        """
        self.model_name = model_name
        self._model = None
        self._tokenizer = None

    def _load_model(self):
        """Lazy load model on first use."""
        if self._model is not None:
            return

        try:
            from transformers import T5Tokenizer, T5ForConditionalGeneration
            import torch

            logger.info(f"Loading summarization model: {self.model_name}")
            self._tokenizer = T5Tokenizer.from_pretrained(self.model_name)
            self._model = T5ForConditionalGeneration.from_pretrained(self.model_name)

            # Use GPU if available
            if torch.cuda.is_available():
                self._model = self._model.cuda()
                logger.info(f"Loaded {self.model_name} on CUDA")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self._model = self._model.to("mps")
                logger.info(f"Loaded {self.model_name} on MPS (Apple Silicon)")
            else:
                logger.info(f"Loaded {self.model_name} on CPU")

        except ImportError as e:
            logger.warning(f"Could not load summarization model: {e}")
            logger.warning("Install transformers: pip install transformers")
            self._model = None
            self._tokenizer = None
        except Exception as e:
            logger.warning(f"Error loading summarization model: {e}")
            self._model = None
            self._tokenizer = None

    def summarize_commits(self, commits: list[str], max_length: int = 100) -> Optional[str]:
        """Summarize a list of commit messages into a concise description.

        Args:
            commits: List of commit messages
            max_length: Maximum length of summary

        Returns:
            Summarized description of changes, or None if model unavailable
        """
        if not commits:
            return None

        # Load model on first use
        self._load_model()
        if self._model is None or self._tokenizer is None:
            logger.debug("Summarization model not available, skipping")
            return None

        try:
            # Format commits as bullet points (limit to 20 to avoid token overflow)
            commit_list = commits[:20]
            commit_text = "\n".join(f"- {c.strip()}" for c in commit_list if c.strip())

            if not commit_text:
                return None

            prompt = (
                f"Write a single sentence summary of these git commits:\n{commit_text}\n\nSummary:"
            )

            inputs = self._tokenizer(prompt, return_tensors="pt", max_length=512, truncation=True)

            # Move to same device as model
            device = next(self._model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            outputs = self._model.generate(
                **inputs, max_length=max_length, num_beams=4, early_stopping=True
            )

            summary = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
            return summary.strip()

        except Exception as e:
            logger.warning(f"Error during summarization: {e}")
            return None

    def generate(self, prompt: str, max_length: int = 150, num_beams: int = 1) -> Optional[str]:
        """General-purpose generation with custom prompt.

        Used by DiffSemanticAnalyzer for narrative rewriting.
        Supports greedy (num_beams=1) for speed or beam search for quality.

        Args:
            prompt: Input prompt text.
            max_length: Maximum output tokens.
            num_beams: Beam width (1 = greedy, fast; 4 = beam search, better quality).

        Returns:
            Generated text, or None if model unavailable.
        """
        self._load_model()
        if self._model is None or self._tokenizer is None:
            return None

        try:
            inputs = self._tokenizer(
                prompt, return_tensors="pt", max_length=512, truncation=True
            )
            device = next(self._model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            outputs = self._model.generate(
                **inputs, max_length=max_length, num_beams=num_beams,
                early_stopping=(num_beams > 1),
            )
            result = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
            return result.strip() if result.strip() else None

        except Exception as e:
            logger.debug(f"Generation failed: {e}")
            return None

    def enhance_changelog(
        self, tag: str, original_summary: str, commits: list[str]
    ) -> str:
        """Enhance a sparse changelog entry with commit details.

        Args:
            tag: Git tag name
            original_summary: Original tag message (often sparse like "Bump version")
            commits: Commits since previous tag

        Returns:
            Enhanced summary combining original + AI-generated
        """
        if not commits:
            return original_summary

        ai_summary = self.summarize_commits(commits)

        # If no AI summary or error, return original
        if not ai_summary:
            return original_summary

        # If original is meaningful (>50 chars and not just "bump"), keep it
        if (
            len(original_summary) > 50
            and "bump" not in original_summary.lower()
            and "release" not in original_summary.lower()
        ):
            return original_summary

        # Otherwise use AI summary
        return ai_summary

    def enhance_timeline_event(self, event_summary: str, commits: list[str]) -> str:
        """Enhance a timeline event summary with commit details.

        Args:
            event_summary: Original event summary (often just merge message)
            commits: Commits in the merged branch

        Returns:
            Enhanced summary
        """
        if not commits:
            return event_summary

        ai_summary = self.summarize_commits(commits)

        # If no AI summary, return original
        if not ai_summary:
            return event_summary

        # If original is just a generic merge message, use AI summary
        if "merge" in event_summary.lower() and len(event_summary) < 100:
            return ai_summary

        # Otherwise combine both
        return f"{event_summary} — {ai_summary}"


# Singleton instance for reuse across operations
_summarizer: Optional[CommitSummarizer] = None


def get_summarizer(model_name: str = "google/flan-t5-base") -> CommitSummarizer:
    """Get or create singleton summarizer instance.

    Args:
        model_name: HuggingFace model name

    Returns:
        CommitSummarizer instance (shared across calls)
    """
    global _summarizer
    if _summarizer is None or _summarizer.model_name != model_name:
        _summarizer = CommitSummarizer(model_name)
    return _summarizer
