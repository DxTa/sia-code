# T1 Feature Snapshot (T1 #5 shared working-memory API)

- Timestamp: `2026-04-21`
- Branch: `feat/t1-conceptual-layer-benchmark-loop`
- Commit: `c19d51c`
- Storage backend: `sqlite-vec`
- Feature scope: shared agent working-memory JSON payload via `memory working-set`
- Raw speed log: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-speed.log`
- Quality snapshot: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-search-quality.md`

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
- Average: `514ms`
- P50: `511ms`
- P95: `527ms`
- Min/Max: `494ms` / `527ms`

### Index Throughput Benchmark
- Total lines: `22,615`
- Index time: `264.2s`
- Throughput: `86 lines/sec`

### Index Size Efficiency
- Source size: `717.5 KB`
- Index size: `5976.0 KB`
- Ratio: `8.33x`

### Concurrent Search Performance
- Sequential: `2.60s`
- Concurrent: `1.07s`
- Speedup: `2.44x`

### Suite Outcome
- `5 passed, 1 skipped`
- Total runtime: `284.38s` (`0:04:44`)

### E2E Gates
- Python E2E: `22 passed in 72.00s` (`bechmarks/T1/2026-04-21-t1-5-working-memory-api-e2e-python.log`)
- TypeScript E2E: `22 passed in 32.58s` (`bechmarks/T1/2026-04-21-t1-5-working-memory-api-e2e-typescript.log`)

### Recall@5 Quality Snapshot
- Recall@5: `0.172`
- Precision@5: `0.053`
- MRR: `0.089`
- Source JSON: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-recall5.json`
- Source log: `bechmarks/T1/2026-04-21-t1-5-working-memory-api.log`

### Comprehension Gap Snapshot (k=5)
- Lookup (n=4): Recall `0.500`, Precision `0.100`, MRR `0.125`
- Comprehension (n=11): Recall `0.053`, Precision `0.036`, MRR `0.076`
- Gap (lookup - comprehension): Recall `+0.447`, Precision `+0.064`, MRR `+0.049`

## Notes

- Command environment uses `PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH"` so E2E subprocess calls to `sia-code` resolve reliably.
- Semantic quality remains stable against the earlier accepted baseline: `click` stays at `MRR@10=0.900`; `p-queue` stays at the known failing threshold (`MRR@10=0.125`).
- Compare this snapshot against `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-baseline.md` before treating T1 as fully complete.
