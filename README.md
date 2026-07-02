# Sia Code

Local-first codebase intelligence for CLI workflows.

Sia Code helps you:

- search and research code with lexical, semantic, or hybrid retrieval
- run multi-hop architecture research across files, symbols, and repos
- trace historical file and behavior changes (`memory trace`, `memory git-context`)
- inspect blast radius before refactors
- work across big repos, multi-repo workspaces, and git worktrees

## Why use it

- Local index. No hosted service required.
- Works well on big repos (think ~5,000 files, not only small projects).
- Aggregates `search`, `research`, and `status` across multiple repos in one workspace.
- Handles both architecture questions and history-aware change questions.
- Good fit for MCP clients and skill-based CLI agents.

## Install

```bash
# MCP + CLI
uv tool install "sia-code[mcp]"

# CLI only
uv tool install sia-code
```

If you prefer pip, `pip install sia-code` and `pip install "sia-code[mcp]"` also work.

## Quick start

### 1) Skill first

Source skill file:

- `skills/sia-code/SKILL.md`

Ask your agent to install from the file link.

### 2) MCP

```bash
uv tool install "sia-code[mcp]"
sia-code-mcp
```

Simple JSON reference:

```json
{
  "mcpServers": {
    "sia-code": {
      "command": "uvx",
      "args": ["--from", "sia-code[mcp]", "sia-code-mcp"]
    }
  }
}
```

Then point your MCP client at `sia-code-mcp`.
See `docs/MCP_INTEGRATION.md` and `docs/LLM_CLI_INTEGRATION.md`.

### 3) Direct CLI

```bash
# initialize + index
uvx sia-code init
uvx sia-code index .

# lexical search for exact symbols
uvx sia-code search --regex "AuthService|token"

# hybrid / semantic-style research
uvx sia-code research "how does authentication work?"

# historical change research
uvx sia-code memory trace "why did auth behavior change" --format table
uvx sia-code memory git-context src/auth.py
```

## Big repos, multiple repos, worktrees

- **Big repo:** built for real codebases with thousands of files, including ~5,000-file projects
- **Multi-repo workspace:** run `index .` from parent folder; Sia Code auto-detects git sub-repos, indexes each, then aggregates `search`, `research`, and `status`
- **Git worktrees / multi-agent sessions:** use shared or isolated indexes with `SIA_CODE_INDEX_SCOPE=shared|worktree`

```bash
# shared index across worktrees/agents
export SIA_CODE_INDEX_SCOPE=shared

# isolated index per worktree
export SIA_CODE_INDEX_SCOPE=worktree
```

## History-aware understanding

Use Sia Code for both code lookup and change understanding:

- `search` -> lexical, semantic, or hybrid retrieval
- `research` -> multi-hop architecture tracing across related files and symbols
- `memory sync-git` -> imports merges/tags as searchable timeline + changelog context
- `memory trace` -> explains likely causal history for behavior or symbol changes
- `memory git-context <file>` -> shows effective file history, likely owners, reverts, evolution narrative, and co-change blast radius
- `memory working-set "query"` -> emits compact shared JSON context for agent handoff

## More docs

- `docs/CLI_FEATURES.md`
- `docs/INDEXING.md`
- `docs/QUERYING.md`
- `docs/MEMORY_FEATURES.md`
- `docs/MCP_INTEGRATION.md`
- `docs/LLM_CLI_INTEGRATION.md`
- `docs/ARCHITECTURE.md`

## License

MIT
