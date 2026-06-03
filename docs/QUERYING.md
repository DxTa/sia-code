# Querying (Compact)

## Search Commands

```bash
# default hybrid
sia-code search "authentication flow"

# lexical / symbol-heavy
sia-code search --regex "AuthService|token"

# semantic only
sia-code search --semantic-only "handle login failures"
```

## Useful Flags

- `-k, --limit <N>`: number of results
- `--no-deps`: only project code
- `--deps-only`: only dependency code
- `--no-filter`: include stale chunks
- `--format text|json|table|csv`
- `--output <path>`: write results to file

## Multi-Hop Research

```bash
sia-code research "how does auth middleware work?" --hops 3 --graph
```

Use this for architecture tracing, call-path discovery, and unfamiliar code.

## Temporal Causal Trace

```bash
sia-code memory trace "why did auth behavior change" --format table
```

Use this when you need timeline-aware clues (likely commits/merges) connected to query-relevant files and symbols.

## Shared Working Memory

```bash
sia-code memory working-set "auth flow" \
  --agent planner \
  --session-id ses-123 \
  --output shared-working-memory.json
```

Use this when multiple agents or long-running steps need the same query-scoped repo context in a stable JSON payload.

## Conceptual Decision Links

```bash
sia-code memory add-decision "Model auth rationale" \
  -d "Capture intent and tradeoffs for auth flow changes" \
  --link-file sia_code/cli.py \
  --link-symbol memory_trace \
  --link-timeline "feature/auth->main" \
  --link-changelog v0.7.0

sia-code memory list --type decision --status pending --format json
```

Use this to tie rationale/intent to concrete code and timeline artifacts, improving comprehension in later retrieval.

## Comprehension Gap Benchmark

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

Use this when you want explicit lookup-vs-comprehension quality deltas in addition to Recall@5.

## Practical Tuning

- `search.vector_weight = 0.0` => lexical-heavy behavior
- `search.vector_weight = 1.0` => semantic-heavy behavior
- defaults come from `.sia-code/config.json`

```bash
sia-code config get search.vector_weight
sia-code config set search.vector_weight 0.0
```

## Output Tips

- Use `--format json` for scripts/agents.
- Use `--format table` for quick terminal scanning.
- Use `--no-deps` in large repos to reduce noise.

## Related Docs

- `docs/CLI_FEATURES.md`
- `docs/INDEXING.md`
