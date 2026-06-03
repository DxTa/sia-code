# T1 Feature Snapshot (T1 #1 Code Digital Twin conceptual layer)

- Timestamp: `2026-03-05`
- Branch: `main`
- Commit: `c71a850`
- Storage backend: `sqlite-vec`
- Feature scope: conceptual intent/rationale links on decisions (code/timeline/changelog artifacts)
- Raw speed log: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-speed.log`
- Quality snapshot: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-search-quality.md`

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
- Average: `464ms`
- P50: `476ms`
- P95: `480ms`
- Min/Max: `434ms` / `480ms`

### Index Throughput Benchmark
- Total lines: `21,610`
- Index time: `280.5s`
- Throughput: `77 lines/sec`

### Index Size Efficiency
- Source size: `685.4 KB`
- Index size: `5800.0 KB`
- Ratio: `8.46x`

### Concurrent Search Performance
- Sequential: `2.57s`
- Concurrent: `1.15s`
- Speedup: `2.23x`

### Suite Outcome
- `5 passed, 1 skipped`
- Total runtime: `307.66s` (`0:05:07`)

### E2E Gates
- Python E2E: `22 passed` (`bechmarks/T1/2026-03-05-t1-1-conceptual-layer-e2e-python.log`)
- TypeScript E2E: `22 passed` (`bechmarks/T1/2026-03-05-t1-1-conceptual-layer-e2e-typescript.log`)

### Recall@5 Quality Snapshot
- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.089`
- Source JSON: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-recall5-ground-truth.json`
- Source log: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-recall5.log`

## Notes

- Command environment uses `PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH"` so E2E subprocess calls to `sia-code` resolve reliably.
- Compare this snapshot against `bechmarks/T1/2026-03-05-remote-main-baseline.md` before moving to the next T1 feature.
- Investigation addendum: see `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-vs-remote-main.md` and matched-path recheck log `bechmarks/T1/2026-03-05-remote-main-speed-recheck-matched-path.log`.
