# Indexing (Compact)

## Basic Commands

```bash
sia-code init
sia-code index .
```

## Index Modes

| Mode | Command | When to use |
| --- | --- | --- |
| Full | `sia-code index .` | First run or broad refresh |
| Incremental | `sia-code index --update` | Daily edits |
| Clean rebuild | `sia-code index --clean` | Backend/config reset, major refactor |
| Parallel | `sia-code index --parallel --workers 8` | Large repos |
| Watch | `sia-code index --watch --debounce 2.0` | Continuous local development |

## Multi-repo Workspaces

If you run from parent folder that contains multiple git repos, Sia Code can auto-detect sub-repos, index each one under workspace `.sia-code/repos/<repo>`, then aggregate `search`, `research`, and `status` across them.

```bash
sia-code init
sia-code index .
```

Use this for mono-workspaces made of separate repos.

## Worktrees and Multiple Agent Sessions

Sia Code also supports git worktrees and parallel LLM CLI sessions.

- Auto behavior in linked worktrees: shared index at `<git-common-dir>/sia-code`
- Override behavior with env vars:

```bash
export SIA_CODE_INDEX_SCOPE=shared   # share one index across worktrees
export SIA_CODE_INDEX_SCOPE=worktree # per-worktree .sia-code
export SIA_CODE_INDEX_DIR=/absolute/path/to/sia-index
```

Practical guidance:

- Many concurrent readers/searchers are fine
- For indexing, prefer one writer at a time on the same shared index
- If you need hard isolation per agent, use `SIA_CODE_INDEX_SCOPE=worktree`

## What gets indexed

- Code files for supported languages
- Exclusions from config + `.gitignore` patterns
- Dependency code (unless disabled by config/flags)

## Health and Maintenance

```bash
sia-code status
sia-code compact
sia-code compact --force
```

- Use `status` to check stale/valid chunk health.
- Use `compact` when stale chunks accumulate.

## Git Sync During Indexing

By default, indexing syncs git history into memory.

- disable with `sia-code index --no-git-sync`

## Common Issues

- **Uninitialized repo**: run `sia-code init`
- **Results feel old**: run `sia-code index --update`
- **Major drift/corruption**: run `sia-code index --clean`
- **Need repo isolation in shared workspace**: use `SIA_CODE_INDEX_SCOPE=worktree`

## Related Docs

- `docs/CLI_FEATURES.md`
- `docs/QUERYING.md`
- `docs/MEMORY_FEATURES.md`
