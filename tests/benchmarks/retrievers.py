"""Retriever implementations for benchmark evaluations.

This module provides concrete retriever implementations for comparing different
code search approaches: sia-code (semantic + multi-hop), grep (lexical baseline),
and future integrations (ChunkHound, etc.).
"""

import re
import subprocess
from pathlib import Path
from typing import Any, List, Optional, Protocol

from .tasks.architectural_tasks import ArchitecturalTask


class CodeRetriever(Protocol):
    """Protocol for code retriever implementations."""

    def retrieve(self, task: ArchitecturalTask, top_k: int = 10) -> List[str]:
        """Retrieve code chunks relevant to the task.

        Args:
            task: The architectural task to retrieve code for
            top_k: Number of chunks to retrieve

        Returns:
            List of retrieved code chunks (as strings)
        """
        ...


class SiaCodeRetriever:
    """Sia-code retriever using semantic search + multi-hop exploration."""

    def __init__(self, index_path: Path, max_hops: int = 2, max_results_per_hop: int = 5):
        """Initialize sia-code retriever.

        Args:
            index_path: Path to .pci/index.db file
            max_hops: Maximum hops for multi-hop search
            max_results_per_hop: Results to explore per hop
        """
        self.index_path = index_path
        self.max_hops = max_hops
        self.max_results_per_hop = max_results_per_hop
        self._backend = None
        self._searcher: Any | None = None

    def _resolve_index_dir(self) -> Path:
        """Normalize benchmark index path to .sia-code directory."""
        if self.index_path.is_dir():
            return self.index_path
        if self.index_path.name == "index.db":
            return self.index_path.parent
        return self.index_path

    def _ensure_initialized(self) -> None:
        """Lazy initialize backend and searcher."""
        if self._backend is not None:
            return

        from sia_code.config import Config
        from sia_code.storage import factory
        from sia_code.search.multi_hop import MultiHopSearchStrategy

        index_dir = self._resolve_index_dir()
        config_path = index_dir / "config.json"
        config = Config.load(config_path) if config_path.exists() else Config()

        # Open existing index using auto-detection (sqlite-vec by default,
        # usearch only for legacy indexes still carrying vectors.usearch).
        self._backend = factory.create_backend(
            path=index_dir,
            backend_type="auto",
            embedding_enabled=config.embedding.enabled,
            embedding_model=config.embedding.model,
            ndim=config.embedding.dimensions,
        )
        self._backend.open_index()

        # Create multi-hop searcher
        self._searcher = MultiHopSearchStrategy(backend=self._backend, max_hops=self.max_hops)

    def retrieve(self, task: ArchitecturalTask, top_k: int = 10) -> List[str]:
        """Retrieve code chunks using sia-code multi-hop search.

        Args:
            task: The architectural task
            top_k: Number of chunks to retrieve (used for max_total_chunks)

        Returns:
            List of code chunks with file paths and line numbers
        """
        self._ensure_initialized()
        searcher = self._searcher
        if searcher is None:
            raise RuntimeError("Retriever searcher failed to initialize")

        # Perform multi-hop research
        result = searcher.research(
            question=task.question,
            max_results_per_hop=self.max_results_per_hop,
            max_total_chunks=top_k * 2,  # Allow some buffer for deduplication
        )

        # Format chunks with file context
        chunks = []
        seen_chunks = set()  # Deduplicate

        for chunk in result.chunks[:top_k]:
            chunk_id = f"{chunk.file_path}:{chunk.start_line}"
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)

            # Format: file path, line range, code
            formatted = (
                f"# File: {chunk.file_path}\n"
                f"# Lines: {chunk.start_line}-{chunk.end_line}\n"
                f"# Type: {chunk.chunk_type}\n\n"
                f"{chunk.code}\n"
            )
            chunks.append(formatted)

        return chunks


class GrepRetriever:
    """Baseline grep-based retriever using ripgrep."""

    def __init__(self, codebase_path: Path, context_lines: int = 5, max_files: int = 10):
        """Initialize grep retriever.

        Args:
            codebase_path: Path to codebase root directory
            context_lines: Lines of context around matches
            max_files: Maximum number of files to search
        """
        self.codebase_path = codebase_path
        self.context_lines = context_lines
        self.max_files = max_files

    def _extract_keywords(self, question: str) -> List[str]:
        """Extract search keywords from the question.

        Simple heuristic:
        - Remove common question words
        - Extract quoted terms
        - Split on whitespace
        - Filter short words

        Args:
            question: The question text

        Returns:
            List of keywords to search for
        """
        # Extract quoted terms first
        quoted = re.findall(r'"([^"]+)"', question)

        # Remove quoted sections and common words
        cleaned = question
        for q in quoted:
            cleaned = cleaned.replace(f'"{q}"', "")

        # Common stop words to filter
        stop_words = {
            "what",
            "how",
            "when",
            "where",
            "why",
            "who",
            "which",
            "does",
            "do",
            "did",
            "is",
            "are",
            "was",
            "were",
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "from",
            "by",
            "with",
        }

        # Split and filter
        words = cleaned.lower().split()
        keywords = [w for w in words if len(w) > 2 and w not in stop_words]

        # Add quoted terms back (prioritize exact phrases)
        return quoted + keywords

    def retrieve(self, task: ArchitecturalTask, top_k: int = 10) -> List[str]:
        """Retrieve code using ripgrep keyword search.

        Args:
            task: The architectural task
            top_k: Maximum number of chunks to return

        Returns:
            List of grep results with context
        """
        keywords = self._extract_keywords(task.question)

        if not keywords:
            return [f"# No keywords extracted from question: {task.question}\n"]

        chunks = []
        seen_files = set()

        # Search for each keyword
        for keyword in keywords[:5]:  # Limit to top 5 keywords
            if len(seen_files) >= self.max_files:
                break

            try:
                # Run ripgrep with context
                result = subprocess.run(
                    [
                        "rg",
                        "--context",
                        str(self.context_lines),
                        "--max-count",
                        "3",  # Max 3 matches per file
                        "--no-heading",
                        "--with-filename",
                        "--line-number",
                        keyword,
                        str(self.codebase_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0 and result.stdout:
                    # Parse ripgrep output
                    output = result.stdout

                    # Group by file
                    file_chunks = {}

                    for line in output.split("\n"):
                        if not line:
                            continue

                        # Parse: filepath:line_number:content or filepath-line_number-content
                        match = re.match(r"^([^:]+):(\d+)[:|-](.*)$", line)
                        if match:
                            filepath, line_num, content = match.groups()

                            if filepath not in file_chunks:
                                if len(seen_files) >= self.max_files:
                                    break
                                file_chunks[filepath] = []
                                seen_files.add(filepath)

                            file_chunks[filepath].append((line_num, content))

                    # Format chunks
                    for filepath, lines in file_chunks.items():
                        if len(chunks) >= top_k:
                            break

                        content = "\n".join(f"{ln}: {c}" for ln, c in lines)
                        formatted = (
                            f"# File: {filepath}\n"
                            f"# Keyword: {keyword}\n"
                            f"# Matches: {len(lines)}\n\n"
                            f"{content}\n"
                        )
                        chunks.append(formatted)

            except (subprocess.TimeoutExpired, FileNotFoundError):
                # Skip this keyword if grep fails
                continue

        if not chunks:
            return [
                f"# No grep results found for keywords: {', '.join(keywords[:5])}\n"
                f"# Codebase: {self.codebase_path}\n"
            ]

        return chunks[:top_k]


class ChunkHoundRetriever:
    """ChunkHound retriever (placeholder for future integration)."""

    def __init__(self, index_path: Path):
        """Initialize ChunkHound retriever.

        Args:
            index_path: Path to ChunkHound index
        """
        self.index_path = index_path

    def retrieve(self, task: ArchitecturalTask, top_k: int = 10) -> List[str]:
        """Retrieve code using ChunkHound.

        TODO: Integrate with actual ChunkHound API when available.

        Args:
            task: The architectural task
            top_k: Number of chunks to retrieve

        Returns:
            List of code chunks
        """
        return [
            f"# ChunkHound integration not yet implemented\n"
            f"# Task: {task.task_id}\n"
            f"# Question: {task.question}\n"
        ]


def create_retriever(
    tool_name: str,
    index_path: Optional[Path] = None,
    codebase_path: Optional[Path] = None,
    **kwargs,
) -> CodeRetriever:
    """Factory function to create a retriever.

    Args:
        tool_name: Name of the tool (sia-code, grep, chunkhound)
        index_path: Path to index file (for sia-code, chunkhound)
        codebase_path: Path to codebase root (for grep)
        **kwargs: Additional tool-specific parameters

    Returns:
        Configured retriever instance

    Raises:
        ValueError: If tool name is unknown or required paths are missing
    """
    if tool_name == "sia-code":
        if index_path is None:
            raise ValueError("index_path required for sia-code retriever")
        return SiaCodeRetriever(
            index_path=index_path,
            max_hops=kwargs.get("max_hops", 2),
            max_results_per_hop=kwargs.get("max_results_per_hop", 5),
        )

    elif tool_name == "grep":
        if codebase_path is None:
            raise ValueError("codebase_path required for grep retriever")
        return GrepRetriever(
            codebase_path=codebase_path,
            context_lines=kwargs.get("context_lines", 5),
            max_files=kwargs.get("max_files", 10),
        )

    elif tool_name == "chunkhound":
        if index_path is None:
            raise ValueError("index_path required for chunkhound retriever")
        return ChunkHoundRetriever(index_path=index_path)

    else:
        raise ValueError(f"Unknown tool: {tool_name}. Supported tools: sia-code, grep, chunkhound")
