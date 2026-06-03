# T1 #2 Comparison vs T1 #1 Baseline

- Previous snapshot: `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-baseline.md`
- Current snapshot: `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-baseline.md`
- Quality references:
  - `bechmarks/T1/2026-03-05-t1-1-conceptual-layer-search-quality.md`
  - `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-search-quality.md`

## Speed Delta (T1 #2 - T1 #1)

| Metric | T1 #1 | T1 #2 | Delta |
|---|---:|---:|---:|
| Search avg latency | `464ms` | `480ms` | `+16ms` (`+3.4%`) |
| Search P50 | `476ms` | `491ms` | `+15ms` (`+3.2%`) |
| Search P95 | `480ms` | `507ms` | `+27ms` (`+5.6%`) |
| Index time | `280.5s` | `299.1s` | `+18.6s` (`+6.6%`) |
| Index throughput | `77 lines/s` | `72 lines/s` | `-5 lines/s` (`-6.5%`) |
| Sequential batch | `2.57s` | `2.47s` | `-0.10s` (`-3.9%`) |
| Concurrent batch | `1.15s` | `1.21s` | `+0.06s` (`+5.2%`) |
| Concurrent speedup | `2.23x` | `2.03x` | `-0.20x` (`-9.0%`) |
| Suite runtime | `307.66s` | `327.29s` | `+19.63s` (`+6.4%`) |

## Quality Delta (T1 #2 - T1 #1)

| Metric | T1 #1 | T1 #2 | Delta |
|---|---:|---:|---:|
| Recall@5 (ground-truth) | `0.172` | `0.172` | `0.000` |
| Precision@5 (ground-truth) | `0.053` | `0.053` | `0.000` |
| MRR (ground-truth) | `0.089` | `0.089` | `0.000` |
| Click semantic MRR@10 | `0.900` | `0.900` | `0.000` |
| p-queue semantic MRR@10 | `0.125` (failed threshold) | `0.125` (failed threshold) | `0.000` |

## New T1 #2 Output (Comprehension Gap Loop)

- Added explicit comprehension reporting at `k=5`:
  - Lookup (n=4): Recall `0.500`, Precision `0.100`, MRR `0.125`
  - Comprehension (n=11): Recall `0.053`, Precision `0.036`, MRR `0.076`
  - Gap (lookup - comprehension): Recall `+0.447`, Precision `+0.064`, MRR `+0.049`
- This provides the missing comprehension score loop requested by T1 #2.

## Feature Gate Verdict

- E2E status: pass (`22/22` Python, `22/22` TypeScript).
- Speed status: modest drift vs T1 #1, but no quality regression.
- Quality status: stable vs T1 #1; known p-queue threshold failure unchanged.
- **Decision: accept T1 #2 and mark T1 feature sequence complete (excluding T1 #5 by instruction).**
