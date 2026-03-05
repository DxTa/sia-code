# T1 Baseline Comparison (local sqlite-vec vs remote main)

- Date: `2026-03-05`
- Compared snapshots:
  - Local: `bechmarks/T1/2026-03-05-sqlite-vec-baseline.md`
  - Remote main: `bechmarks/T1/2026-03-05-remote-main-baseline.md`
- Quality detail sources:
  - Local: `bechmarks/T1/2026-03-05-sqlite-vec-search-quality.md`
  - Remote main: `bechmarks/T1/2026-03-05-remote-main-search-quality.md`

## Speed Comparison

| Metric | Local sqlite-vec | Remote main | Delta (remote - local) |
|---|---:|---:|---:|
| Search avg latency | `326ms` | `304ms` | `-22ms` (`-6.7%`) |
| Search P50 | `315ms` | `305ms` | `-10ms` (`-3.2%`) |
| Search P95 | `372ms` | `345ms` | `-27ms` (`-7.3%`) |
| Index time | `338.9s` | `328.0s` | `-10.9s` (`-3.2%`) |
| Index throughput | `64 lines/s` | `66 lines/s` | `+2 lines/s` (`+3.1%`) |
| Sequential batch | `1.76s` | `1.70s` | `-0.06s` (`-3.4%`) |
| Concurrent batch | `0.89s` | `0.73s` | `-0.16s` (`-18.0%`) |
| Concurrent speedup | `1.99x` | `2.34x` | `+0.35x` (`+17.6%`) |
| Suite runtime | `361.31s` | `334.22s` | `-27.09s` (`-7.5%`) |

## Quality Comparison

| Metric | Local sqlite-vec | Remote main | Delta |
|---|---:|---:|---:|
| Recall@5 (ground-truth) | `0.172` | `0.172` | `0.000` |
| Precision@5 (ground-truth) | `0.053` | `0.053` | `0.000` |
| MRR (ground-truth) | `0.089` | `0.133` | `+0.044` |
| Click semantic MRR@10 | `0.900` | `0.900` | `0.000` |
| p-queue semantic MRR@10 | `0.125` (failed threshold) | `0.125` (failed threshold) | `0.000` |

## Gate Result Before Next T1 Feature

- Remote-main baseline is equal or better than the local sqlite-vec baseline across captured speed metrics.
- Recall@5 and Precision@5 are unchanged; ground-truth MRR is higher on remote main.
- Semantic quality profiles are unchanged (click strong, p-queue still below threshold in both snapshots).
- Use `bechmarks/T1/2026-03-05-remote-main-baseline.md` + linked quality snapshot as the baseline-of-record before continuing T1 feature work.

## Caveat

- The in-repo ground-truth dataset includes stale file paths for parts of the current tree, so absolute Recall@5/MRR values should be interpreted as relative trend signals between runs.
