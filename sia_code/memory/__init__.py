"""Dynamic git memory system for sia-code.

Provides on-demand file history, blast radius, revert detection,
recency scoring, branch/worktree awareness, and semantic file grouping.
"""

from .blast_radius import BlastRadius, BlastRadiusAnalyzer, CoupledFile
from .diff_analyzer import ChangeMeaning, DiffSemanticAnalyzer, EvolutionNarrative
from .git_dynamic import (
    BranchContext,
    BranchInfo,
    BranchResolver,
    FileHistory,
    GitDynamicMemory,
    HistoricalCommit,
    WorktreeInfo,
)
from .intent_classifier import CommitIntent, IntentClassifier
from .recency import RecencyConfig, RecencyScorer
from .revert_detector import CommitInfo, RevertDetector, RevertPair
from .semantic_grouper import EnrichedRelation, SemanticFileGrouper, SemanticRelation

__all__ = [
    "BlastRadius",
    "BlastRadiusAnalyzer",
    "BranchContext",
    "BranchInfo",
    "BranchResolver",
    "ChangeMeaning",
    "CommitInfo",
    "CommitIntent",
    "CoupledFile",
    "DiffSemanticAnalyzer",
    "EnrichedRelation",
    "EvolutionNarrative",
    "FileHistory",
    "GitDynamicMemory",
    "HistoricalCommit",
    "IntentClassifier",
    "RecencyConfig",
    "RecencyScorer",
    "RevertDetector",
    "RevertPair",
    "SemanticFileGrouper",
    "SemanticRelation",
    "WorktreeInfo",
]
