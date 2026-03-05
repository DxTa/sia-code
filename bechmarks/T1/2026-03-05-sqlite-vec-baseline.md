# T1 Baseline Snapshot (sqlite-vec)

- Timestamp: `2026-03-05T09:51:00+02:00`
- Branch: `main`
- Commit: `c71a850`
- Storage backend: `sqlite-vec`
- Raw log: `bechmarks/T1/2026-03-05-sqlite-vec-baseline.log`
- Search quality snapshot: `bechmarks/T1/2026-03-05-sqlite-vec-search-quality.md`

## Command

```bash
E2E_REPO_PATH=/tmp/sia-code-e2e/click \
E2E_LANGUAGE=python \
E2E_KEYWORD=def \
E2E_SYMBOL=Command \
.venv/bin/pytest tests/e2e/test_performance_benchmarks.py -q -s --timeout=900
```

## Results

### Search Latency Benchmark
- Queries: `5`
- Average: `326ms`
- P50: `315ms`
- P95: `372ms`
- Min/Max: `285ms` / `372ms`

### Index Throughput Benchmark
- Total lines: `21,610`
- Index time: `338.9s`
- Throughput: `64 lines/sec`

### Index Size Efficiency
- Source size: `685.4 KB`
- Index size: `5800.0 KB`
- Ratio: `8.46x`

### Concurrent Search Performance
- Sequential: `1.76s`
- Concurrent: `0.89s`
- Speedup: `1.99x`

### Suite Outcome
- `5 passed, 1 skipped`
- Total runtime: `361.31s` (`0:06:01`)

### Recall@5 Quality Snapshot
- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.089`
- Source: `bechmarks/T1/2026-03-05-recall5-ground-truth.json`

## Notes for Feature Comparison

- Use this snapshot as the sqlite-vec baseline for upcoming T1 feature comparisons.
- Keep the same benchmark command and env vars so deltas are meaningful.
- Capture future snapshots in `bechmarks/T1/` with date-stamped filenames and include raw logs.
- Compare speed with search-quality metrics together (see linked quality snapshot).
- Include Recall@5 (`run_academic_benchmarks --k-values 5`) in each feature gate.
