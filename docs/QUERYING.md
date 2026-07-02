# Querying (Compact)

Use Sia Code in this order:

1. `search` when you know symbol, file, or pattern
2. `research` when question spans multiple files
3. `memory trace` / `memory git-context` when you need change history and blast radius

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
- `--no-deps`: exclude dependency code
- `--deps-only`: show only dependency matches
- `--no-filter`: disable stale chunk filtering
- `--format json|table|csv`: structured output
- `--output <path>`: write results to file

## Multi-Hop Research

```bash
sia-code research "how does authentication work?" --hops 2
```

Use this for architecture tracing, call-path discovery, and unfamiliar code.

## Change Understanding

```bash
# likely causal history behind behavior change
sia-code memory trace "why did auth behavior change" --format table

# file history, reverts, owners, narrative, blast radius
sia-code memory git-context src/auth.py
```

Use `memory trace` for query-level history.
Use `memory git-context` for file-level history and impact.

## Shared Working Memory

```bash
sia-code memory working-set "auth flow" \
  --agent planner \
  --session-id ses-123 \
  -o shared-memory.json
```

Use this when multiple agents or long-running steps need same query-scoped repo context in stable JSON.

## Multi-Repo Querying

If you indexed parent folder with multiple git sub-repos, `search` and `research` aggregate across them automatically.

```bash
sia-code index .
sia-code search "AuthService"
sia-code research "where is token refresh handled?"
```

## Practical Tuning

- `search.vector_weight = 0.0` => lexical-heavy behavior
- `search.vector_weight = 1.0` => semantic-heavy behavior
- use `--no-deps` in large repos to cut noise
- use `memory git-context` before risky refactors

## Output Tips

- Use `--format json` for scripts and agents
- Use `--format table` for human review
- Use `memory working-set` for handoff, not raw pasted logs

## Related Docs

- `docs/CLI_FEATURES.md`
- `docs/INDEXING.md`
- `docs/MEMORY_FEATURES.md`
- `docs/LLM_CLI_INTEGRATION.md`
