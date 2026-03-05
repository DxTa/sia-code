# T1 Search Quality Snapshot (T1 #1 conceptual layer)

- Timestamp: `2026-03-05`
- Branch: `main`
- Backend: `sqlite-vec`

## Reference Metrics From Docs

Based on `docs/BENCHMARK_RESULTS.md` and `docs/BENCHMARK_METHODOLOGY.md`:

- Reported RepoEval Recall@5: `89.9%`
- Reported delta vs cAST baseline: `+12.9 points`
- Methodology focus: compare with the same `Recall@k` setup and query set.

## Local Quality Proxy Runs

### Command 1: Benchmark harness health

```bash
PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH" \
.venv/bin/pytest tests/benchmarks -q
```

- Result: `16 passed`
- Log: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-quality-benchmark-harness.log`

### Command 2: Local Recall@5 benchmark (ground-truth dataset)

```bash
PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH" \
.venv/bin/python -m tests.benchmarks.run_academic_benchmarks \
  --tool sia-code \
  --dataset ground-truth-sia-code \
  --k-values 5 \
  --output bechmarks/T1 \
  --index-path .sia-code \
  --codebase-path .
```

- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.089`
- Queries: `15`
- JSON: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-recall5-ground-truth.json`
- Log: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-recall5.log`

### Command 3: Click semantic quality

```bash
PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH" \
E2E_REPO_PATH=/tmp/sia-code-e2e/click \
E2E_LANGUAGE=python \
E2E_KEYWORD=def \
E2E_SYMBOL=Command \
.venv/bin/pytest tests/e2e/test_semantic_quality.py::TestSemanticQualityClick::test_semantic_search_mrr -q -s --run-semantic-quality --timeout=900
```

- MRR@10: `0.900`
- Hit@1: `80.0% (4/5)`
- Hit@5: `100.0% (5/5)`
- Log: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-quality-click.log`

### Command 4: p-queue semantic quality

```bash
PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH" \
E2E_REPO_PATH=/tmp/sia-code-e2e/p-queue \
E2E_LANGUAGE=typescript \
E2E_KEYWORD=function \
E2E_SYMBOL=PQueue \
.venv/bin/pytest tests/e2e/test_semantic_quality.py::TestSemanticQualityPQueue::test_semantic_search_mrr -q -s --run-semantic-quality --timeout=900
```

- MRR@10: `0.125`
- Hit@1: `0.0% (0/4)`
- Hit@5: `25.0% (1/4)`
- Outcome: threshold test failed (`MRR < 0.2`, same profile as baseline)
- Log: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-quality-pqueue.log`

## Comparison Anchor

- Baseline-of-record: `bechmarks/T1/2026-03-05-remote-main-search-quality.md`
- Use this file with `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-baseline.md` for go/no-go on next T1 feature.
