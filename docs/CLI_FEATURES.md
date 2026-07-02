# CLI Features (Compact)

Short reference for daily `sia-code` usage.

## Typical Workflow

```bash
sia-code init
sia-code index .
sia-code search --regex "auth|token"
sia-code research "how does auth flow work?"
sia-code memory trace "why did auth behavior change" --format table
sia-code status
```

## Core Commands

| Command | Purpose | Key options |
| --- | --- | --- |
| `init` | Create `.sia-code/` index workspace | `--path`, `--dry-run` |
| `index [PATH]` | Build index (auto-fan-out for multi-repo workspaces) | `--update`, `--clean`, `--parallel`, `--workers`, `--watch`, `--debounce`, `--no-git-sync` |
| `search QUERY` | Search code (default hybrid) | `--regex`, `--semantic-only`, `-k/--limit`, `--no-filter`, `--no-deps`, `--deps-only`, `--format`, `--output` |
| `research QUESTION` | Multi-hop architecture exploration | `--hops`, `--graph`, `-k/--limit`, `--no-filter` |
| `status` | Index health and statistics | none |
| `compact [PATH]` | Remove stale chunks | `--threshold`, `--force` |
| `interactive` | Live query loop | `--regex`, `-k/--limit` |

## Memory Commands

| Command | Purpose | Key options |
| --- | --- | --- |
| `memory sync-git` | Import timeline/changelog from git (with diff stats and optional local semantic summaries) | `--since`, `--limit`, `--dry-run`, `--tags-only`, `--merges-only`, `--min-importance` |
| `memory add-decision TITLE` | Add pending decision with optional conceptual links to code/timeline artifacts | `-d/--description` (required), `-r/--reasoning`, `-a/--alternatives`, `--link-file`, `--link-symbol`, `--link-timeline`, `--link-changelog` |
| `memory list` | List memory items | `--type`, `--status`, `--limit`, `--format` |
| `memory approve ID` | Approve decision | `-c/--category` (required) |
| `memory reject ID` | Reject decision | none |
| `memory search QUERY` | Search memory | `--type`, `-k/--limit` |
| `memory working-set QUERY` | Build shared working-memory JSON for agent handoff | `--agent`, `--session-id`, `-o/--output` |
| `memory trace QUERY` | Trace likely causal timeline events for a code query (graph + timeline overlap) | `--hops`, `--seed-limit`, `--timeline-limit`, `-k/--limit`, `--format` |
| `memory git-context FILE...` | Show file history, reverts, owners, evolution narrative, and co-change blast radius | `--no-blast-radius`, `--no-narrative`, `--format` |
| `memory timeline` | View timeline events | `--since`, `--event-type`, `--importance`, `--format` |
| `memory changelog [RANGE]` | Generate changelog | `--format`, `--output` |
| `memory export` / `memory import` | Backup/restore memory | `-o/--output`, `-i/--input` |

`memory sync-git` is the entrypoint for semantic changelog generation: it extracts git context, then (if enabled) uses the local summarizer to enrich release and merge summaries stored in memory.

## Embed Daemon

| Command | Purpose |
| --- | --- |
| `embed start` | Start shared embedding daemon |
| `embed status` | Show daemon status |
| `embed stop` | Stop daemon |

Use daemon when you rely heavily on hybrid/semantic search or memory embedding operations.

## Config Commands

```bash
sia-code config show
sia-code config path
sia-code config get search.vector_weight
sia-code config set search.vector_weight 0.0
```

## Output Formats

- `text` (default)
- `json` (automation)
- `table` (human scanning)
- `csv` (export)

## Good Defaults

- First index: `sia-code index .`
- Parent folder with many repos: `sia-code index .` there too; search/research aggregate after indexing
- Ongoing work: `sia-code index --update`
- Exact symbols: `sia-code search --regex "pattern"`
- Project-only focus: `--no-deps`
- Architecture questions: `sia-code research "..." --hops 3`
- Change understanding: `sia-code memory git-context path/to/file.py`

## Related Docs

- `docs/INDEXING.md`
- `docs/QUERYING.md`
- `docs/MEMORY_FEATURES.md`
