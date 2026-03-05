"""Tests for academic benchmark path normalization behavior."""

import json

import pytest

from tests.benchmarks.datasets.ground_truth_sia_code import GroundTruthQuery
from tests.benchmarks import run_academic_benchmarks as academic


class _FakeRetriever:
    def __init__(self, chunks):
        self._chunks = chunks

    def retrieve(self, task, top_k=10):  # noqa: ARG002
        return self._chunks


def test_evaluate_retriever_normalizes_absolute_paths_with_codebase_root(tmp_path):
    """Absolute paths under the benchmarked repo should match relative ground truth."""
    codebase_root = tmp_path / "repo"
    codebase_root.mkdir(parents=True)

    abs_file = codebase_root / "sia_code" / "core" / "models.py"
    abs_file.parent.mkdir(parents=True)
    abs_file.write_text("# dummy\n")

    query = GroundTruthQuery(
        query_id="gt-test-001",
        query="Where is the Chunk dataclass defined?",
        relevant_files=["sia_code/core/models.py"],
        difficulty="easy",
        category="lookup",
    )

    retriever = _FakeRetriever([f"# File: {abs_file}\n# Lines: 1-1\n\nclass Chunk: pass\n"])

    result = academic.evaluate_retriever_on_query(
        retriever,
        query,
        k=5,
        codebase_root=codebase_root,
    )

    assert result["recall_at_k"] == 1.0
    assert result["precision_at_k"] > 0
    assert result["mrr"] == 1.0


def test_compute_comprehension_gap_groups_lookup_vs_comprehension():
    detailed_results = [
        {"k": 5, "category": "lookup", "recall_at_k": 1.0, "precision_at_k": 0.5, "mrr": 0.8},
        {"k": 5, "category": "lookup", "recall_at_k": 0.8, "precision_at_k": 0.4, "mrr": 0.6},
        {"k": 5, "category": "trace", "recall_at_k": 0.4, "precision_at_k": 0.2, "mrr": 0.3},
        {
            "k": 5,
            "category": "architecture",
            "recall_at_k": 0.2,
            "precision_at_k": 0.1,
            "mrr": 0.2,
        },
    ]

    report = academic.compute_comprehension_gap(detailed_results, k=5)

    assert report["lookup"]["num_queries"] == 2
    assert report["comprehension"]["num_queries"] == 2
    assert report["lookup"]["recall"] == pytest.approx(0.9)
    assert report["comprehension"]["recall"] == pytest.approx(0.3)
    assert report["gap"]["recall_gap"] == pytest.approx(0.6)
    assert report["gap"]["precision_gap"] == pytest.approx(0.3)
    assert report["gap"]["mrr_gap"] == pytest.approx(0.45)


def test_run_academic_evaluation_writes_comprehension_report(tmp_path, monkeypatch):
    queries = [
        GroundTruthQuery(
            query_id="q-lookup",
            query="find entry point",
            relevant_files=["app/main.py"],
            difficulty="easy",
            category="lookup",
        ),
        GroundTruthQuery(
            query_id="q-trace",
            query="trace command flow",
            relevant_files=["app/flow.py"],
            difficulty="medium",
            category="trace",
        ),
    ]

    class _DummyRetriever:
        def retrieve(self, task, top_k=10):  # noqa: ARG002
            if task.task_id == "q-lookup":
                return ["# File: app/main.py\n"]
            return ["# File: app/other.py\n"]

    monkeypatch.setattr(academic, "get_ground_truth_queries", lambda **kwargs: queries)
    monkeypatch.setattr(academic, "create_retriever", lambda **kwargs: _DummyRetriever())

    output_dir = tmp_path / "out"
    result = academic.run_academic_evaluation(
        tool_name="sia-code",
        dataset="ground-truth-sia-code",
        k_values=[5],
        output_dir=output_dir,
        index_path=tmp_path / "index.db",
        codebase_path=tmp_path,
        comprehension_report=True,
    )

    assert "k5" in result

    output_file = output_dir / "sia-code_ground-truth-sia-code_k5.json"
    payload = json.loads(output_file.read_text())
    assert "comprehension_gap" in payload
    assert "k5" in payload["comprehension_gap"]
