---
name: sia-code
description: Compact local-first code search skill for CLI agents using lexical, semantic, and hybrid retrieval, multi-hop research, history-aware memory, and agent handoff context.
license: MIT
compatibility: opencode
version: 0.8.0
---

# Sia-Code Skill (Compact)

Use this skill when an agent needs to explore a codebase, trace architecture, understand historical changes, or hand off stable repo context to another agent step.

This is fallback path for skill-based clients. If MCP is available, prefer MCP setup from repo `README.md`.

## Quick Start

```bash
# initialize once per repo
uvx sia-code init
uvx sia-code index .

# fast lexical search for exact identifiers
uvx sia-code search --regex "auth|login|token"

# multi-hop architecture research
uvx sia-code research "how does authentication flow work?"

# history-aware change research
uvx sia-code memory trace "why did auth behavior change" --format table
uvx sia-code memory git-context src/auth.py

# health check
uvx sia-code status
```

## Search Modes

- `uvx sia-code search "query"`: default hybrid search (lexical + semantic)
- `uvx sia-code search --regex "pattern"`: lexical search only, best for exact symbols
- `uvx sia-code search --semantic-only "query"`: semantic-only search
- `uvx sia-code research "question"`: multi-hop research across related files and symbols

Useful flags:

- `-k, --limit <N>`: result count
- `--no-deps`: project code only
- `--deps-only`: dependency code only
- `--format json|table|csv`: structured output

## History-Aware Workflow

```bash
# import git history into memory
uvx sia-code memory sync-git

# trace likely causal timeline behind behavior change
uvx sia-code memory trace "why did auth behavior change" --format table

# inspect file history, owners, reverts, narrative, and blast radius
uvx sia-code memory git-context src/auth.py

# hand off query-scoped working memory to another agent step
uvx sia-code memory working-set "auth flow" \
  --agent planner \
  --session-id ses-123 \
  -o shared-memory.json
```

- `memory working-set` emits stable JSON for agent handoff.
- `memory git-context` is CLI-only and useful before risky refactors.

## Multi-Repo and Worktrees

- Parent workspace with multiple git repos: run `uvx sia-code index .` there, then `search`, `research`, and `status` aggregate across indexed sub-repos
- Shared index across worktrees/agents: `export SIA_CODE_INDEX_SCOPE=shared`
- Isolated index per worktree/agent: `export SIA_CODE_INDEX_SCOPE=worktree`

## Agent-Friendly Session Pattern

```bash
# 1) verify index health
uvx sia-code status

# 2) initialize/index if needed
uvx sia-code init
uvx sia-code index .

# 3) investigate code and history
uvx sia-code search --regex "target_symbol"
uvx sia-code research "how does X work?"
uvx sia-code memory trace "why did X change" --format table

# 4) hand off stable context if needed
uvx sia-code memory working-set "X" --agent planner --session-id ses-123 -o shared-memory.json
```

## Troubleshooting

- If uninitialized: run `uvx sia-code init && uvx sia-code index .`
- If results look stale: run `uvx sia-code index --update` (or `--clean` after major refactors)
- If memory add/search fails with embedding issues: run `uvx sia-code embed start`
- If too much dependency noise: add `--no-deps`

## Notes

- Lexical search is often strongest for exact code identifiers.
- Hybrid/semantic retrieval helps with natural-language questions.
- Keep this file short and operational; see repo `README.md` for MCP setup and broader onboarding.
