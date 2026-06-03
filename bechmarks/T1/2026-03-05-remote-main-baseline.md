# T1 Baseline Snapshot (remote main)

- Timestamp: `2026-03-05T10:43:01+02:00`
- Branch: `main`
- Commit: `c71a850`
- Storage backend: `sqlite-vec`
- Raw log: `bechmarks/T1/2026-03-05-remote-main-speed.log`
- Index clean log: `bechmarks/T1/2026-03-05-remote-main-index-clean.log`
- Search quality snapshot: `bechmarks/T1/2026-03-05-remote-main-search-quality.md`

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
- Average: `304ms`
- P50: `305ms`
- P95: `345ms`
- Min/Max: `269ms` / `345ms`

### Index Throughput Benchmark
- Total lines: `21,610`
- Index time: `328.0s`
- Throughput: `66 lines/sec`

### Index Size Efficiency
- Source size: `685.4 KB`
- Index size: `5800.0 KB`
- Ratio: `8.46x`

### Concurrent Search Performance
- Sequential: `1.70s`
- Concurrent: `0.73s`
- Speedup: `2.34x`

### Suite Outcome
- `5 passed, 1 skipped`
- Total runtime: `334.22s` (`0:05:34`)

### Recall@5 Quality Snapshot
- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.133`
- Source: `bechmarks/T1/2026-03-05-remote-main-recall5-ground-truth.json`

## Notes for Feature Comparison

- Use this snapshot as the remote-main baseline for upcoming T1 feature comparisons.
- Keep the same benchmark command and env vars so deltas are meaningful.
- Capture future snapshots in `bechmarks/T1/` with date-stamped filenames and include raw logs.
- Compare speed with search-quality metrics together (see linked quality snapshot).
- Include Recall@5 (`run_academic_benchmarks --k-values 5`) in each feature gate.
