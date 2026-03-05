#!/usr/bin/env python3
"""CLI runner for academic benchmark evaluation (Option 1: ChunkHound methodology).

This script runs Recall@k and Precision@k evaluation on sia-code using ground-truth
datasets, enabling direct comparison with ChunkHound's reported metrics.

Usage:
    # Run sia-code on ground-truth dataset
    python -m tests.benchmarks.run_academic_benchmarks \
      --tool sia-code \
      --dataset ground-truth-sia-code \
      --k-values 1,3,5,10 \
      --output results/academic/
    
    # Compare multiple tools
    python -m tests.benchmarks.run_academic_benchmarks \
      --compare sia-code,grep \
      --dataset ground-truth-sia-code \
      --k-values 5 \
      --output results/academic/
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from .datasets.ground_truth_sia_code import (
    get_ground_truth_queries,
    get_dataset_stats,
    GroundTruthQuery,
)
from .retrievers import create_retriever
from .metrics import recall_at_k, precision_at_k


LOOKUP_CATEGORIES = {"lookup"}
COMPREHENSION_CATEGORIES = {"trace", "architecture", "integration"}

ResultRow = Dict[str, Any]
MetricSummary = Dict[str, float | int]


def _group_results_by_category(results: List[ResultRow]) -> Dict[str, List[ResultRow]]:
    """Group results by non-empty category."""
    grouped: Dict[str, List[ResultRow]] = {}

    for result in results:
        category = result.get("category")
        if not category:
            continue

        grouped.setdefault(category, []).append(result)

    return grouped


def _compute_metric_gap(lookup: MetricSummary, comprehension: MetricSummary) -> Dict[str, float]:
    """Compute metric deltas between lookup and comprehension summaries."""
    return {
        "recall_gap": lookup["recall"] - comprehension["recall"],
        "precision_gap": lookup["precision"] - comprehension["precision"],
        "mrr_gap": lookup["mrr"] - comprehension["mrr"],
    }


def _summarize_results_group(results: List[ResultRow]) -> MetricSummary:
    """Summarize recall/precision/MRR for a result group."""
    if not results:
        return {
            "recall": 0.0,
            "precision": 0.0,
            "mrr": 0.0,
            "num_queries": 0,
        }

    count = len(results)
    return {
        "recall": sum(r["recall_at_k"] for r in results) / count,
        "precision": sum(r["precision_at_k"] for r in results) / count,
        "mrr": sum(r["mrr"] for r in results) / count,
        "num_queries": count,
    }


def compute_comprehension_gap(detailed_results: List[ResultRow], k: int) -> Dict[str, Any]:
    """Compute lookup-vs-comprehension performance gap for a given k."""
    k_results = [result for result in detailed_results if result.get("k") == k]
    grouped_results = _group_results_by_category(k_results)

    lookup_results: List[ResultRow] = []
    comprehension_results: List[ResultRow] = []

    for category, category_results in grouped_results.items():
        if category in LOOKUP_CATEGORIES:
            lookup_results.extend(category_results)
        if category in COMPREHENSION_CATEGORIES:
            comprehension_results.extend(category_results)

    lookup_summary = _summarize_results_group(lookup_results)
    comprehension_summary = _summarize_results_group(comprehension_results)

    by_category = {
        category: _summarize_results_group(category_results)
        for category, category_results in sorted(grouped_results.items())
    }

    return {
        "k": k,
        "lookup": lookup_summary,
        "comprehension": comprehension_summary,
        "gap": _compute_metric_gap(lookup_summary, comprehension_summary),
        "by_category": by_category,
        "lookup_categories": sorted(LOOKUP_CATEGORIES),
        "comprehension_categories": sorted(COMPREHENSION_CATEGORIES),
    }


def print_comprehension_gap_report(report: Dict[str, Any]) -> None:
    """Print human-readable comprehension gap report."""
    k = report["k"]
    lookup = report["lookup"]
    comprehension = report["comprehension"]
    gap = report["gap"]

    print(f"\n=== Comprehension Gap at k={k} ===")
    print(
        "Lookup          "
        f"(n={lookup['num_queries']}): "
        f"Recall={lookup['recall']:.3f}, "
        f"Precision={lookup['precision']:.3f}, "
        f"MRR={lookup['mrr']:.3f}"
    )
    print(
        "Comprehension   "
        f"(n={comprehension['num_queries']}): "
        f"Recall={comprehension['recall']:.3f}, "
        f"Precision={comprehension['precision']:.3f}, "
        f"MRR={comprehension['mrr']:.3f}"
    )
    print(
        "Gap (lookup - comprehension): "
        f"Recall={gap['recall_gap']:+.3f}, "
        f"Precision={gap['precision_gap']:+.3f}, "
        f"MRR={gap['mrr_gap']:+.3f}"
    )


def evaluate_retriever_on_query(
    retriever,
    query: GroundTruthQuery,
    k: int = 5,
    codebase_root: Path | None = None,
) -> Dict[str, Any]:
    """Evaluate a retriever on a single ground-truth query.

    Args:
        retriever: Retriever instance
        query: Ground-truth query with labeled relevant files
        k: Number of top results to evaluate

    Returns:
        Dictionary with evaluation results
    """
    # Create a minimal task-like object for retriever
    from .tasks.architectural_tasks import ArchitecturalTask

    task = ArchitecturalTask(
        task_id=query.query_id,
        question=query.query,
        codebase="sia-code",
        expected_files=query.relevant_files,
        expected_concepts=[],
        difficulty=query.difficulty,
        task_type=query.category,
    )

    # Retrieve chunks
    chunks = retriever.retrieve(task, top_k=k)

    # Extract file paths from retrieved chunks
    # Format: "# File: path/to/file.py\n..."
    retrieved_files = []
    codebase_root = codebase_root.resolve() if codebase_root else None

    for chunk in chunks:
        lines = chunk.split("\n")
        for line in lines:
            if line.startswith("# File: "):
                filepath = line.replace("# File: ", "").strip()

                # Normalize to relative path if absolute
                filepath_obj = Path(filepath)
                if filepath_obj.is_absolute() and codebase_root is not None:
                    try:
                        filepath = str(filepath_obj.relative_to(codebase_root))
                    except ValueError:
                        # Not under codebase_root, keep as-is
                        pass

                if filepath not in retrieved_files:
                    retrieved_files.append(filepath)
                break

    # Calculate metrics using actual metrics functions
    relevant_set = set(query.relevant_files)

    # Calculate position of first relevant result (for MRR)
    first_relevant_position = None
    for i, filepath in enumerate(retrieved_files[:k], 1):
        if filepath in relevant_set:
            first_relevant_position = i
            break

    # Use metrics functions with correct signatures
    recall = recall_at_k(retrieved_files, relevant_set, k)
    precision = precision_at_k(retrieved_files, relevant_set, k)
    mrr = 1.0 / first_relevant_position if first_relevant_position else 0.0

    # Count relevant files in top-k
    retrieved_set = set(retrieved_files[:k])
    num_retrieved_relevant = len(relevant_set & retrieved_set)

    return {
        "query_id": query.query_id,
        "query": query.query,
        "difficulty": query.difficulty,
        "category": query.category,
        "relevant_files": query.relevant_files,
        "retrieved_files": retrieved_files[:k],
        "recall_at_k": recall,
        "precision_at_k": precision,
        "mrr": mrr,
        "num_relevant": len(relevant_set),
        "num_retrieved_relevant": num_retrieved_relevant,
    }


def run_academic_evaluation(
    tool_name: str,
    dataset: str,
    k_values: List[int],
    output_dir: Path,
    index_path: Path,
    codebase_path: Path,
    difficulty: str | None = None,
    category: str | None = None,
    comprehension_report: bool = False,
) -> Dict[str, Any]:
    """Run academic metric evaluation on a tool.

    Args:
        tool_name: Name of tool to evaluate (sia-code, grep)
        dataset: Dataset name (ground-truth-sia-code)
        k_values: List of k values to evaluate (e.g., [1, 3, 5, 10])
        output_dir: Directory to save results
        index_path: Path to index file
        codebase_path: Path to codebase root
        difficulty: Optional difficulty filter
        category: Optional category filter

    Returns:
        Dictionary with aggregated results
    """
    print(f"\n=== Academic Evaluation: {tool_name} ===")
    print(f"Dataset: {dataset}")
    print(f"K values: {k_values}")

    # Load queries
    if dataset == "ground-truth-sia-code":
        queries = get_ground_truth_queries(difficulty=difficulty, category=category)
    else:
        raise ValueError(f"Unknown dataset: {dataset}")

    if not queries:
        print("No queries found for the given filters")
        return {}

    print(f"Loaded {len(queries)} queries")

    # Create retriever
    retriever = create_retriever(
        tool_name=tool_name,
        index_path=index_path if tool_name == "sia-code" else None,
        codebase_path=codebase_path if tool_name == "grep" else None,
    )

    # Evaluate on each query for each k
    all_results = []

    for k in k_values:
        print(f"\n--- Evaluating at k={k} ---")

        for i, query in enumerate(queries, 1):
            print(f"  [{i}/{len(queries)}] {query.query_id}: {query.query[:60]}...")

            result = evaluate_retriever_on_query(
                retriever,
                query,
                k=k,
                codebase_root=codebase_path,
            )
            result["k"] = k
            all_results.append(result)

            print(
                f"    Recall@{k}: {result['recall_at_k']:.3f}, "
                f"Precision@{k}: {result['precision_at_k']:.3f}, "
                f"MRR: {result['mrr']:.3f}"
            )

    # Aggregate results by k
    aggregated = {}
    comprehension_gap = {}
    for k in k_values:
        k_results = [r for r in all_results if r["k"] == k]

        avg_recall = sum(r["recall_at_k"] for r in k_results) / len(k_results)
        avg_precision = sum(r["precision_at_k"] for r in k_results) / len(k_results)
        avg_mrr = sum(r["mrr"] for r in k_results) / len(k_results)

        aggregated[f"k{k}"] = {
            "recall": avg_recall,
            "precision": avg_precision,
            "mrr": avg_mrr,
            "num_queries": len(k_results),
        }

        print(f"\n=== Aggregated Results at k={k} ===")
        print(f"Recall@{k}: {avg_recall:.3f}")
        print(f"Precision@{k}: {avg_precision:.3f}")
        print(f"MRR: {avg_mrr:.3f}")

        if comprehension_report:
            report = compute_comprehension_gap(all_results, k)
            comprehension_gap[f"k{k}"] = report
            print_comprehension_gap_report(report)

    # Save detailed results
    output_dir.mkdir(parents=True, exist_ok=True)

    results_file = output_dir / f"{tool_name}_{dataset}_k{'_'.join(map(str, k_values))}.json"
    payload = {
        "tool": tool_name,
        "dataset": dataset,
        "k_values": k_values,
        "aggregated": aggregated,
        "detailed_results": all_results,
    }
    if comprehension_report:
        payload["comprehension_gap"] = comprehension_gap

    with open(results_file, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"\n=== Results saved to {results_file} ===")

    return aggregated


def run_comparison(
    tool_names: List[str],
    dataset: str,
    k: int,
    output_dir: Path,
    index_path: Path,
    codebase_path: Path,
    comprehension_report: bool = False,
) -> None:
    """Run side-by-side comparison of multiple tools.

    Args:
        tool_names: List of tools to compare
        dataset: Dataset name
        k: K value for evaluation
        output_dir: Directory to save results
        index_path: Path to index file
        codebase_path: Path to codebase root
    """
    print(f"\n=== Comparing {len(tool_names)} tools at k={k} ===")

    results = {}
    for tool_name in tool_names:
        aggregated = run_academic_evaluation(
            tool_name=tool_name,
            dataset=dataset,
            k_values=[k],
            output_dir=output_dir,
            index_path=index_path,
            codebase_path=codebase_path,
            comprehension_report=comprehension_report,
        )
        results[tool_name] = aggregated[f"k{k}"]

    # Print comparison table
    print(f"\n=== Comparison at k={k} ===")
    print(f"{'Tool':<15} {'Recall@' + str(k):<12} {'Precision@' + str(k):<14} {'MRR':<8}")
    print("-" * 55)

    for tool_name, metrics in results.items():
        print(
            f"{tool_name:<15} {metrics['recall']:<12.3f} {metrics['precision']:<14.3f} {metrics['mrr']:<8.3f}"
        )

    # Calculate deltas (vs first tool as baseline)
    if len(tool_names) >= 2:
        baseline_tool = tool_names[0]
        baseline = results[baseline_tool]

        print(f"\n=== Deltas vs {baseline_tool} ===")
        print(f"{'Tool':<15} {'ΔRecall@' + str(k):<12} {'ΔPrecision@' + str(k):<14} {'ΔMRR':<8}")
        print("-" * 55)

        for tool_name, metrics in results.items():
            if tool_name == baseline_tool:
                print(f"{tool_name:<15} {'(baseline)':<12} {'(baseline)':<14} {'(baseline)':<8}")
            else:
                delta_recall = metrics["recall"] - baseline["recall"]
                delta_precision = metrics["precision"] - baseline["precision"]
                delta_mrr = metrics["mrr"] - baseline["mrr"]

                print(
                    f"{tool_name:<15} {delta_recall:+<12.3f} {delta_precision:+<14.3f} {delta_mrr:+<8.3f}"
                )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run academic benchmark evaluation (ChunkHound methodology)"
    )

    parser.add_argument(
        "--tool",
        help="Tool to evaluate (sia-code, grep)",
    )
    parser.add_argument(
        "--compare",
        help="Comma-separated list of tools to compare (e.g., sia-code,grep)",
    )
    parser.add_argument(
        "--dataset",
        default="ground-truth-sia-code",
        help="Dataset to use (ground-truth-sia-code)",
    )
    parser.add_argument(
        "--k-values",
        default="5",
        help="Comma-separated k values to evaluate (e.g., 1,3,5,10)",
    )
    parser.add_argument(
        "--difficulty",
        choices=["easy", "medium", "hard"],
        help="Filter queries by difficulty",
    )
    parser.add_argument(
        "--category",
        choices=["lookup", "trace", "architecture", "integration"],
        help="Filter queries by category",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/academic"),
        help="Output directory for results",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=Path(".sia-code/index.db"),
        help="Path to .sia-code/index.db file",
    )
    parser.add_argument(
        "--codebase-path",
        type=Path,
        default=Path("."),
        help="Path to codebase root directory",
    )
    parser.add_argument(
        "--dataset-stats",
        action="store_true",
        help="Show dataset statistics and exit",
    )
    parser.add_argument(
        "--comprehension-report",
        action="store_true",
        help="Include lookup-vs-comprehension gap report in output",
    )

    args = parser.parse_args()

    # Show dataset stats
    if args.dataset_stats:
        stats = get_dataset_stats()
        print("\n=== Ground-Truth Dataset Statistics ===")
        print(f"Total queries: {stats['total_queries']}")
        print("\nBy difficulty:")
        for diff, count in stats["by_difficulty"].items():
            print(f"  {diff}: {count}")
        print("\nBy category:")
        for cat, count in stats["by_category"].items():
            print(f"  {cat}: {count}")
        print(f"\nAvg relevant files per query: {stats['avg_relevant_files_per_query']:.1f}")
        return

    # Parse k values
    k_values = [int(k) for k in args.k_values.split(",")]

    # Run comparison
    if args.compare:
        if len(k_values) > 1:
            print("Error: --compare only supports a single k value")
            sys.exit(1)

        tools = args.compare.split(",")
        run_comparison(
            tool_names=tools,
            dataset=args.dataset,
            k=k_values[0],
            output_dir=args.output,
            index_path=args.index_path,
            codebase_path=args.codebase_path,
            comprehension_report=args.comprehension_report,
        )
        return

    # Run single tool evaluation
    if args.tool:
        run_academic_evaluation(
            tool_name=args.tool,
            dataset=args.dataset,
            k_values=k_values,
            output_dir=args.output,
            index_path=args.index_path,
            codebase_path=args.codebase_path,
            difficulty=args.difficulty,
            category=args.category,
            comprehension_report=args.comprehension_report,
        )
        return

    # No valid arguments
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
