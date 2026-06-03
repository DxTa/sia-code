# T1 Search Quality Snapshot (remote main)

- Timestamp: `2026-03-05`
- Branch: `main`
- Backend: `sqlite-vec`

## Reference Metrics From Docs

Based on `docs/BENCHMARK_RESULTS.md` and `docs/BENCHMARK_METHODOLOGY.md`:

- Reported RepoEval Recall@5: `89.9%`
- Reported delta vs cAST baseline: `+12.9 points`
- Methodology focus: keep `Recall@k` (especially `Recall@5`) with the same dataset/query set/config when comparing runs.

## Environment Constraint (This Machine)

Full RepoEval scripts still require local dataset/repositories under:

- `/tmp/CodeT/RepoCoder/datasets`
- `/tmp/CodeT/RepoCoder/repositories`

Those paths are currently unavailable. This snapshot therefore stores:

1. canonical reported RepoEval quality from docs,
2. local Recall@5 benchmark on the in-repo ground-truth dataset, and
3. semantic E2E proxy metrics.

## Local Quality Proxy Runs

### Command 1: Benchmark harness health

```bash
.venv/bin/pytest tests/benchmarks -q
```

- Result: `16 passed` (reused baseline harness run)
- Log: `bechmarks/T1/2026-03-05-quality-benchmark-harness.log`

### Command 2: Local Recall@5 benchmark (ground-truth dataset)

```bash
.venv/bin/python -m tests.benchmarks.run_academic_benchmarks \
  --tool sia-code \
  --dataset ground-truth-sia-code \
  --k-values 5 \
  --output /tmp/sia-code-main-baseline-20260305/results/academic \
  --index-path .sia-code \
  --codebase-path .
```

- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.133`
- Queries: `15`
- JSON: `bechmarks/T1/2026-03-05-remote-main-recall5-ground-truth.json`
- Grep JSON: `bechmarks/T1/2026-03-05-remote-main-grep-recall5-ground-truth.json`
- Log: `bechmarks/T1/2026-03-05-remote-main-recall5.log`

### Command 3: Click semantic quality

```bash
E2E_REPO_PATH=/tmp/sia-code-e2e/click \
E2E_LANGUAGE=python \
E2E_KEYWORD=def \
E2E_SYMBOL=Command \
.venv/bin/pytest tests/e2e/test_semantic_quality.py::TestSemanticQualityClick::test_semantic_search_mrr -q -s --run-semantic-quality --timeout=900
```

- MRR@10: `0.900`
- Hit@1: `80.0% (4/5)`
- Hit@5: `100.0% (5/5)`
- Log: `bechmarks/T1/2026-03-05-remote-main-quality-click.log`

### Command 4: p-queue semantic quality

```bash
E2E_REPO_PATH=/tmp/sia-code-e2e/p-queue \
E2E_LANGUAGE=typescript \
E2E_KEYWORD=function \
E2E_SYMBOL=PQueue \
.venv/bin/pytest tests/e2e/test_semantic_quality.py::TestSemanticQualityPQueue::test_semantic_search_mrr -q -s --run-semantic-quality --timeout=900
```

- MRR@10: `0.125`
- Hit@1: `0.0% (0/4)`
- Hit@5: `25.0% (1/4)`
- Outcome: threshold test failed (`MRR < 0.2`)
- Log: `bechmarks/T1/2026-03-05-remote-main-quality-pqueue.log`

## How To Compare Future T1 Feature Runs

- Keep the same benchmark speed command from `bechmarks/T1/2026-03-05-remote-main-baseline.md`.
- Keep the same Recall@5 command and semantic quality commands above.
- Compare speed and quality deltas together before moving to the next T1 feature.
