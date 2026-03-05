# T1 #1 Comparison vs Remote-Main Baseline

- Feature snapshot: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-baseline.md`
- Baseline-of-record: `bechmarks/T1/2026-03-05-remote-main-baseline.md`
- Quality references:
  - `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-search-quality.md`
  - `bechmarks/T1/2026-03-05-remote-main-search-quality.md`

## Speed Delta (feature - remote main)

| Metric | Remote main | T1 #1 | Delta |
|---|---:|---:|---:|
| Search avg latency | `304ms` | `464ms` | `+160ms` (`+52.6%`) |
| Search P50 | `305ms` | `476ms` | `+171ms` (`+56.1%`) |
| Search P95 | `345ms` | `480ms` | `+135ms` (`+39.1%`) |
| Index time | `328.0s` | `280.5s` | `-47.5s` (`-14.5%`) |
| Index throughput | `66 lines/s` | `77 lines/s` | `+11 lines/s` (`+16.7%`) |
| Sequential batch | `1.70s` | `2.57s` | `+0.87s` (`+51.2%`) |
| Concurrent batch | `0.73s` | `1.15s` | `+0.42s` (`+57.5%`) |
| Concurrent speedup | `2.34x` | `2.23x` | `-0.11x` (`-4.7%`) |
| Suite runtime | `334.22s` | `307.66s` | `-26.56s` (`-7.9%`) |

## Quality Delta (feature - remote main)

| Metric | Remote main | T1 #1 | Delta |
|---|---:|---:|---:|
| Recall@5 (ground-truth) | `0.172` | `0.172` | `0.000` |
| Precision@5 (ground-truth) | `0.053` | `0.053` | `0.000` |
| MRR (ground-truth) | `0.133` | `0.089` | `-0.044` |
| Click semantic MRR@10 | `0.900` | `0.900` | `0.000` |
| p-queue semantic MRR@10 | `0.125` (failed threshold) | `0.125` (failed threshold) | `0.000` |

## Feature Gate Verdict

- E2E status: pass (`22/22` Python, `22/22` TypeScript).
- Benchmark status: mixed.
  - Better indexing throughput/time.
  - Worse query latency and concurrent batch timings.
- Recall@5 status: unchanged, but MRR regressed vs remote-main baseline.

### Go/No-Go

- Initial result was **hold for review** due apparent latency regression vs the original remote-main snapshot.

## Regression Investigation (matched environment)

- Re-ran remote-main benchmark with the same CLI resolution environment used in this feature gate:
  - log: `bechmarks/T1/2026-03-05-remote-main-speed-recheck-matched-path.log`
  - key result: remote-main `Average=536ms`, `P50=530ms`, `P95=563ms`
- T1 #1 run remained `Average=464ms`, `P50=476ms`, `P95=480ms`, indicating the prior apparent regression was dominated by environment drift (CLI resolution/PATH), not by conceptual-layer changes.
- Recall@5 delta vs remote-main (`MRR 0.133 -> 0.089`) is real versus remote-main, but this same `0.089` level already existed in earlier local sqlite-vec snapshots, so it is not newly introduced by T1 #1.

### Updated Go/No-Go

- **Proceed to T1 #2** with this matched-environment note attached.
- Keep using explicit PATH in benchmark commands for consistency:
  - `PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH"`

## Environment Note

- E2E/benchmark runs required `PATH="/home/dxta/dev/sia-code/.venv/bin:$PATH"` so subprocess calls to `sia-code` could resolve during tests.
