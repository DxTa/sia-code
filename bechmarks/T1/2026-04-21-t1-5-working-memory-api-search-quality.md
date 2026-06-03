# T1 Search Quality Snapshot (T1 #5 shared working-memory API)

- Timestamp: `2026-04-21`
- Branch: `feat/t1-conceptual-layer-benchmark-loop`
- Backend: `sqlite-vec`

## Reference Metrics From Docs

Based on `docs/BENCHMARK_RESULTS.md` and `docs/BENCHMARK_METHODOLOGY.md`:

- Reported RepoEval Recall@5: `89.9%`
- Reported delta vs cAST baseline: `+12.9 points`
- Methodology focus: compare quality metrics with fixed query set and `k`.

## Local Quality Proxy Runs

### Command 1: Benchmark harness health

```bash
PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH" \
.venv/bin/pytest tests/benchmarks -q
```

- Result: `18 passed`
- Log: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-benchmark-harness.log`

### Command 2: Recall@5 + comprehension-gap report

```bash
PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH" \
.venv/bin/python -m tests.benchmarks.run_academic_benchmarks \
  --tool sia-code \
  --dataset ground-truth-sia-code \
  --k-values 5 \
  --comprehension-report \
  --output bechmarks/T1 \
  --index-path .sia-code \
  --codebase-path .
```

- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.089`
- Comprehension report:
  - Lookup (n=4): Recall `0.500`, Precision `0.100`, MRR `0.125`
  - Comprehension (n=11): Recall `0.053`, Precision `0.036`, MRR `0.076`
  - Gap (lookup - comprehension): Recall `+0.447`, Precision `+0.064`, MRR `+0.049`
- JSON: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-recall5.json`
- Log: `bechmarks/T1/2026-04-21-t1-5-working-memory-api.log`

### Command 3: Click semantic quality

```bash
source "/home/dxta/.config/opencode/scripts/load-mcp-credentials-safe.sh" && \
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
- Log: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-quality-click.log`

### Command 4: p-queue semantic quality

```bash
source "/home/dxta/.config/opencode/scripts/load-mcp-credentials-safe.sh" && \
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
- Outcome: threshold test failed (`MRR < 0.2`, unchanged vs prior snapshots)
- Log: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-quality-pqueue.log`

## Comparison Anchor

- Previous feature snapshot: `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-search-quality.md`
- Use with `bechmarks/T1/2026-04-21-t1-5-working-memory-api-baseline.md` to decide final T1 status.
