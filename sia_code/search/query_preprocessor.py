"""Query preprocessing for natural language questions in lexical search."""

import re
from typing import Set


class QueryPreprocessor:
    """Preprocess natural language queries for lexical search.

    Removes stop words and punctuation to improve BM25 matching
    while preserving code identifiers like CamelCase and snake_case.
    """

    # Stop words that don't contribute to code search
    # NOTE: Excludes code-critical terms like "function", "method", "class"
    # which are important for code-specific queries
    STOP_WORDS: Set[str] = {
        # Question words
        "how",
        "what",
        "where",
        "when",
        "why",
        "which",
        "who",
        "whom",
        # Common verbs
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "can",
        "could",
        "would",
        "should",
        "will",
        # Articles & prepositions
        "the",
        "a",
        "an",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "into",
        "through",
        "about",
        # Conjunctions
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        # Misc common words
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
    }

    def __init__(self, preserve_case: bool = True):
        """Initialize preprocessor.

        Args:
            preserve_case: Whether to preserve original case for code identifiers
        """
        self.preserve_case = preserve_case

    def preprocess(self, question: str) -> str:
        """Convert natural language question to keyword query.

        Examples:
            "How does chip counting work?" -> "chip counting"
            "What is ChipCountingService?" -> "ChipCountingService"
            "Where is load_config defined?" -> "load_config defined"

        Args:
            question: Natural language question

        Returns:
            Preprocessed query string (may be empty if all stop words)
        """
        if not question or not question.strip():
            return ""

        # Extract keywords
        keywords = self.extract_keywords(question)

        # Rejoin with spaces
        return " ".join(keywords)

    def expand_variants(self, question: str) -> list[str]:
        """Produce 2-3 query variants for research seeding.

        Variants aim to improve recall for code search by combining:
        - raw natural language
        - cleaned keyword query
        - synthesized code-like identifiers / framework-role forms
        """
        if not question or not question.strip():
            return []

        variants: list[str] = []
        raw = question.strip()
        variants.append(raw)

        keywords = self.extract_keywords(question)
        keyword_query = " ".join(keywords)
        if keyword_query and keyword_query.lower() != raw.lower():
            variants.append(keyword_query)

        focused_parts: list[str] = []
        identifiers = [t for t in keywords if self._is_code_identifier(t)]
        plain = [t for t in keywords if not self._is_code_identifier(t)]
        focused_parts.extend(identifiers[:4])
        focused_parts.extend(self._synthesize_code_forms(keywords))
        focused_parts.extend([t for t in plain if len(t) >= 4 and t.lower() not in {'work', 'works'}][:4])
        focused = " ".join(dict.fromkeys(p for p in focused_parts if p)).strip()
        if focused and focused.lower() not in {v.lower() for v in variants}:
            variants.append(focused)

        deduped: list[str] = []
        seen: set[str] = set()
        for v in variants:
            key = v.lower().strip()
            if key and key not in seen:
                deduped.append(v)
                seen.add(key)
        return deduped[:3]

    def _synthesize_code_forms(self, tokens: list[str]) -> list[str]:
        """Generate code-like identifier variants from plain-language tokens.

        Examples:
        - stable diffusion trainer -> StableDiffusionTrainer, stable_diffusion_trainer
        - task rest api -> TaskViewSet, TaskSerializer, TaskAPIView
        """
        if not tokens:
            return []

        lowered = [t.lower() for t in tokens]
        generic_noise = {'work', 'works', 'working'}
        results: list[str] = []

        role_terms = {
            "trainer": "Trainer",
            "config": "Config",
            "service": "Service",
            "manager": "Manager",
            "controller": "Controller",
            "model": "Model",
            "serializer": "Serializer",
            "viewset": "ViewSet",
            "handler": "Handler",
            "pipeline": "Pipeline",
        }

        role_idx = next((i for i, t in enumerate(lowered) if t in role_terms), None)
        if role_idx is not None and role_idx > 0:
            base_tokens = lowered[: role_idx + 1]
            camel = "".join(t.capitalize() for t in base_tokens)
            snake = "_".join(base_tokens)
            results.extend([camel, snake])

        # API-ish expansion: task REST API -> TaskViewSet / TaskSerializer / TaskAPIView
        api_tokens = {"api", "rest", "endpoint"}
        if any(t in api_tokens for t in lowered):
            base = next((t for t in lowered if t not in api_tokens and t not in generic_noise and len(t) > 2), None)
            if base:
                title = base.capitalize()
                results.extend([f"{title}ViewSet", f"{title}Serializer", f"{title}APIView"])

        # Generic multi-token camel/snake for first 2-3 meaningful words
        content = [t for t in lowered if len(t) > 2 and t not in api_tokens and t not in generic_noise][:3]
        if len(content) >= 2:
            results.append("".join(t.capitalize() for t in content))
            results.append("_".join(content))

        # Keep small and unique
        out: list[str] = []
        seen: set[str] = set()
        for r in results:
            k = r.lower()
            if r and k not in seen:
                out.append(r)
                seen.add(k)
        return out[:6]

    def extract_keywords(self, question: str) -> list[str]:
        """Extract meaningful keywords from a question.

        Args:
            question: Natural language question

        Returns:
            List of keywords (may be empty)
        """
        # Step 1: Tokenize while preserving code identifiers
        tokens = self._tokenize(question)

        # Step 2: Filter out stop words (case-insensitive check)
        # BUT: preserve tokens that look like code identifiers
        keywords = []
        for token in tokens:
            token_lower = token.lower()
            is_stop = token_lower in self.STOP_WORDS
            is_code = self._is_code_identifier(token)

            # Keep if:
            # - Not a stop word, OR
            # - It's a code identifier AND not a single capitalized word
            #   (avoid keeping "How", "What" which are just capitalized stop words)
            if not is_stop:
                keywords.append(token)
            elif is_code and not (len(token) > 1 and token[0].isupper() and token[1:].islower()):
                # Is code identifier but not just Title Case (like "How", "What")
                keywords.append(token)

        return keywords

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text while preserving code identifiers.

        Handles:
        - CamelCase (preserve as single token)
        - snake_case (preserve as single token)
        - Punctuation removal (except underscores)

        Args:
            text: Input text

        Returns:
            List of tokens
        """
        # Remove punctuation except underscores (for snake_case)
        # Keep alphanumeric and underscores
        cleaned = re.sub(r"[^\w\s]", " ", text)

        # Split on whitespace
        tokens = cleaned.split()

        return tokens

    def _is_code_identifier(self, token: str) -> bool:
        """Check if token looks like a code identifier.

        Code identifiers include:
        - CamelCase: ChipCountingService
        - snake_case: load_config
        - CONSTANTS: MAX_RETRIES
        - Mixed: get_API_url

        Args:
            token: Token to check

        Returns:
            True if token appears to be a code identifier
        """
        if not token:
            return False

        # Contains underscore = likely snake_case
        if "_" in token:
            return True

        # Has mixed case = likely CamelCase
        has_upper = any(c.isupper() for c in token)
        has_lower = any(c.islower() for c in token)
        if has_upper and has_lower:
            return True

        # All uppercase with length > 1 = likely CONSTANT
        if token.isupper() and len(token) > 1:
            return True

        return False
