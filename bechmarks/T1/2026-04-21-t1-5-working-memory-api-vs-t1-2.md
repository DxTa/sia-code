# T1 #5 Comparison vs T1 #2 Baseline

- Previous snapshot: `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-baseline.md`
- Current snapshot: `bechmarks/T1/2026-04-21-t1-5-working-memory-api-baseline.md`
- Quality references:
  - `bechmarks/T1/2026-03-05-t1-2-comprehension-gap-search-quality.md`
  - `bechmarks/T1/2026-04-21-t1-5-working-memory-api-search-quality.md`

## Speed Delta (T1 #5 - T1 #2)

| Metric | T1 #2 | T1 #5 | Delta |
|---|---:|---:|---:|
| Search avg latency | `480ms` | `514ms` | `+34ms` (`+7.1%`) |
| Search P50 | `491ms` | `511ms` | `+20ms` (`+4.1%`) |
| Search P95 | `507ms` | `527ms` | `+20ms` (`+3.9%`) |
| Index time | `299.1s` | `264.2s` | `-34.9s` (`-11.7%`) |
| Index throughput | `72 lines/s` | `86 lines/s` | `+14 lines/s` (`+19.4%`) |
| Sequential batch | `2.47s` | `2.60s` | `+0.13s` (`+5.3%`) |
| Concurrent batch | `1.21s` | `1.07s` | `-0.14s` (`-11.6%`) |
| Concurrent speedup | `2.03x` | `2.44x` | `+0.41x` (`+20.2%`) |
| Suite runtime | `327.29s` | `284.38s` | `-42.91s` (`-13.1%`) |

## Quality Delta (T1 #5 - T1 #2)

| Metric | T1 #2 | T1 #5 | Delta |
|---|---:|---:|---:|
| Recall@5 (ground-truth) | `0.172` | `0.172` | `0.000` |
| Precision@5 (ground-truth) | `0.053` | `0.053` | `0.000` |
| MRR (ground-truth) | `0.089` | `0.089` | `0.000` |
| Click semantic MRR@10 | `0.900` | `0.900` | `0.000` |
| p-queue semantic MRR@10 | `0.125` (failed threshold) | `0.125` (failed threshold) | `0.000` |

## New T1 #5 Output (Shared Working-Memory API)

- Added `sia-code memory working-set QUERY` to materialize query-scoped project memory into stable JSON.
- Payload includes:
  - `agent`
  - `session_id`
  - `query`
  - git context (`branch`, `commit_hash`, `commit_time`)
  - generated `project_memory` context for agent handoff
- This closes the remaining T1 #5 gap around a Prometheus-style shared working-memory surface without adding a separate subsystem.

## Feature Gate Verdict

- E2E status: pass (`22/22` Python, `22/22` TypeScript).
- Speed status: mixed but acceptable; search latency drift is modest while indexing/runtime improved.
- Quality status: stable vs T1 #2; known p-queue threshold failure unchanged.
- **Decision: accept T1 #5 and mark the full T1 feature set complete.**
