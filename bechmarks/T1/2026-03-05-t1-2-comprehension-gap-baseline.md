# T1 Feature Snapshot (T1 #2 Comprehension Gap loop)

- Timestamp: `2026-03-05`
- Branch: `main`
- Commit: `c71a850`
- Storage backend: `sqlite-vec`
- Feature scope: comprehension-oriented benchmark reporting (`lookup` vs `comprehension` gap)
- Raw speed log: `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-speed.log`
- Quality snapshot: `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-search-quality.md`

## Commands

```bash
PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH" \
E2E_REPO_PATH=/tmp/sia-code-e2e/click \
E2E_LANGUAGE=python \
E2E_KEYWORD=def \
E2E_SYMBOL=Command \
.venv/bin/pytest tests/e2e/test_performance_benchmarks.py -q -s --timeout=900
```

## Results

### Search Latency Benchmark
- Queries: `5`
- Average: `480ms`
- P50: `491ms`
- P95: `507ms`
- Min/Max: `431ms` / `507ms`

### Index Throughput Benchmark
- Total lines: `21,610`
- Index time: `299.1s`
- Throughput: `72 lines/sec`

### Index Size Efficiency
- Source size: `685.4 KB`
- Index size: `5800.0 KB`
- Ratio: `8.46x`

### Concurrent Search Performance
- Sequential: `2.47s`
- Concurrent: `1.21s`
- Speedup: `2.03x`

### Suite Outcome
- `5 passed, 1 skipped`
- Total runtime: `327.29s` (`0:05:27`)

### E2E Gates
- Python E2E: `22 passed` (`bechmarks/T1/2026-03-05-t1-2-comprehension-gap-e2e-python.log`)
- TypeScript E2E: `22 passed` (`bechmarks/T1/2026-03-05-t1-2-comprehension-gap-e2e-typescript.log`)

### Recall@5 Quality Snapshot
- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.089`
- Source JSON: `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-recall5.json`
- Source log: `bechmarks/T1/2026-03-05-t1-2-comprehension-gap.log`

### Comprehension Gap Snapshot (k=5)
- Lookup (n=4): Recall `0.500`, Precision `0.100`, MRR `0.125`
- Comprehension (n=11): Recall `0.053`, Precision `0.036`, MRR `0.076`
- Gap (lookup - comprehension): Recall `+0.447`, Precision `+0.064`, MRR `+0.049`

## Notes

- Command environment uses `PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH"` so E2E subprocess calls to `sia-code` resolve reliably.
- Compare this snapshot against `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-baseline.md` before moving to the next T1 stage.
