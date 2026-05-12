# Sia Code

Local-first codebase intelligence for CLI workflows.

Sia Code indexes your repo and lets you:

- search code fast (lexical, semantic, or hybrid)
- trace architecture with multi-hop research
- store/retrieve project decisions and timeline context

## Why teams use it

- Works directly on local code (`.sia-code/` index per repo/worktree)
- Great for symbol-level search (`--regex`) and architecture questions (`research`)
- Supports 12 AST-aware languages (Python, JS/TS, Go, Rust, Java, C/C++, C#, Ruby, PHP)
- Integrates well with LLM CLI agents

## Install

```bash
# pip
pip install sia-code

# pip with MCP server support
pip install "sia-code[mcp]"

# or uv tool
uv tool install sia-code

# MCP entrypoint from PyPI/local checkout
uv tool install "sia-code[mcp]"

# verify
sia-code --version
sia-code-mcp --help
```

## Quick Start (2 minutes)

```bash
# in your project
sia-code init
sia-code index .

# search
sia-code search --regex "auth|login|token"

# architecture trace
sia-code research "how does authentication work?"

# index health
sia-code status
```

## Command Cheatsheet

| Command | What it does |
| --- | --- |
| `sia-code init` | Initialize `.sia-code/` in current project |
| `sia-code index .` | Build index |
| `sia-code index --update` | Incremental re-index |
| `sia-code index --clean` | Rebuild index from scratch |
| `sia-code search "query"` | Hybrid search (default) |
| `sia-code search --regex "pattern"` | Lexical search |
| `sia-code research "question"` | Multi-hop relationship discovery |
| `sia-code memory sync-git` | Import timeline/changelog from git |
| `sia-code memory search "topic"` | Search stored project memory |
| `sia-code memory working-set "query"` | Build shared working-memory JSON for agents |
| `sia-code memory trace "query"` | Trace likely causal timeline events for query |
| `sia-code config show` | Print active configuration |

## Search Modes (important)

- Default command is hybrid: `sia-code search "query"`
- Lexical mode: `sia-code search --regex "pattern"`
- Semantic-only mode: `sia-code search --semantic-only "query"`

Use `--no-deps` when you want only your project code.

## Git Sync Memory + Semantic Changelog

`sia-code memory sync-git` is the fastest way to build project memory from git history.

- Scans tags into changelog entries
- Scans merge commits into timeline events
- Stores `files_changed` and diff stats (`insertions`, `deletions`, `files`)
- Optionally enhances sparse summaries using a local summarization model

How semantic summary generation works:

1. `sync-git` collects git context (tags, merges, commit ranges, diff stats)
2. It gathers commit subjects for each release/merge window
3. A local model (default `google/flan-t5-base`) generates a concise summary sentence
4. The enhanced summary is stored in memory and later exposed by `memory changelog`

```bash
sia-code memory sync-git
sia-code memory working-set "auth flow" --agent planner --session-id ses-123
sia-code memory trace "why did command parsing change" --format table
sia-code memory add-decision "Keep sqlite-vec default" \
  -d "Need a stable local-first backend baseline" \
  -r "Consistent behavior across environments" \
  --link-file sia_code/config.py \
  --link-symbol default_backend \
  --link-timeline "feature/sqlite-vec->main"
sia-code memory changelog --format markdown
```

## LLM CLI Integration

Primary integration is now the packaged MCP server:

- `sia-code-mcp`

For engineering workflows, the preferred first MCP call is:

- `engineering_bootstrap`

It bundles readiness checks, lightweight search, optional memory retrieval, and guarded multi-hop research so MCP-aware clients can use Sia Code effectively with only a bare MCP server configuration.

Integration guide:

- `docs/MCP_INTEGRATION.md`
- `docs/LLM_CLI_INTEGRATION.md`

Fallback skill file for environments without MCP support:

- `skills/sia-code/SKILL.md`

## Configuration

Config path:

- `.sia-code/config.json`

Useful commands:

```bash
sia-code config show
sia-code config get search.vector_weight
sia-code config set search.vector_weight 0.0
```

Note: backend selection is auto by default (`sqlite-vec` for new indexes, legacy `usearch` supported).

## Multi-Worktree / Multi-Agent Setup

Yes - Sia Code works with multiple git worktrees and multiple LLM CLI instances.

Scope resolution order:

1. `SIA_CODE_INDEX_DIR` (explicit path override)
2. `SIA_CODE_INDEX_SCOPE` (`shared`, `worktree`, or `auto`)
3. `auto` fallback:
   - linked worktree -> shared index at `<git-common-dir>/sia-code`
   - normal checkout -> local `.sia-code`

```bash
# Shared index across worktrees/agents
export SIA_CODE_INDEX_SCOPE=shared

# Isolated index per worktree
export SIA_CODE_INDEX_SCOPE=worktree

# Full explicit control
export SIA_CODE_INDEX_DIR=/absolute/path/to/sia-index
```

Typical worktree workflow:

```bash
# in main checkout (build shared index once)
export SIA_CODE_INDEX_SCOPE=shared
sia-code init
sia-code index .

# create feature worktree
git worktree add ../feat-auth feat/auth
cd ../feat-auth

# reuse same shared index, then incrementally refresh
sia-code status
sia-code index --update
```

When a worktree is merged/removed:

- `shared` scope: index stays in git common dir and remains usable
- `worktree` scope: index lives in that worktree directory and is removed with it
- after merge, run `sia-code index --update` in the remaining checkout

Practical guidance:

- Many readers/searchers are fine in shared mode
- Prefer one active index writer per shared index
- For strict branch/agent isolation, use `worktree`
- Teams on different machines should keep local indexes and sync context via git + `sia-code memory sync-git`

## Documentation

- `docs/CLI_FEATURES.md` - concise CLI command reference
- `docs/MCP_INTEGRATION.md` - MCP setup and transport notes
- `docs/CODE_STRUCTURE.md` - repo/module map
- `docs/ARCHITECTURE.md` - core runtime architecture
- `docs/INDEXING.md` - indexing behavior and maintenance
- `docs/QUERYING.md` - search modes and tuning
- `docs/MEMORY_FEATURES.md` - memory workflow
- `docs/BENCHMARK_RESULTS.md` - benchmark summary

## License

MIT
