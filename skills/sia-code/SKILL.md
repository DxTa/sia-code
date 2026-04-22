---
name: sia-code
description: Compact local-first code search skill for CLI agents using BM25, optional semantic search, multi-hop research, and project memory.
license: MIT
compatibility: opencode
version: 0.7.1
---

# Sia-Code Skill (Compact)

Use this skill when an agent needs to explore a codebase quickly, trace architecture, or store/retrieve project decisions and shared working-memory context.

This is a compact, repo-local variant intended for easy copy/paste into LLM CLI skill directories.

## Quick Start

```bash
# initialize once per repo
uvx sia-code init
uvx sia-code index .

# fast lexical search (great for identifiers)
uvx sia-code search --regex "auth|login|token"

# architecture exploration
uvx sia-code research "how does authentication flow work?"

# health check
uvx sia-code status
```

## Search Modes

- `uvx sia-code search "query"`: default hybrid search (BM25 + semantic)
- `uvx sia-code search --regex "pattern"`: lexical search only (usually best for exact symbols)
- `uvx sia-code search --semantic-only "query"`: semantic-only search

Useful flags:

- `-k, --limit <N>`: result count
- `--no-deps`: project code only
- `--deps-only`: dependency code only
- `--format json|table|csv`: structured output

## Multi-Hop Research

```bash
uvx sia-code research "how is config loaded?" --hops 3 --graph
```

- Use for dependency tracing, call flow mapping, and architecture questions.

## Memory Workflow

```bash
# import timeline/changelogs from git
uvx sia-code memory sync-git

# store a pending decision
uvx sia-code memory add-decision "Adopt sqlite-vec by default" \
  -d "Align with current backend defaults" \
  -r "Lower operational overhead"

# review and triage
uvx sia-code memory list --type decision --status pending
uvx sia-code memory approve 1 --category architecture

# recall context
uvx sia-code memory search "backend default" --type all

# hand off query-scoped working memory to another agent step
uvx sia-code memory working-set "auth flow" \
  --agent planner \
  --session-id ses-123 \
  -o shared-memory.json
```

- `memory working-set` emits stable JSON for agent handoff and includes approved decisions as well as the pending decision queue.

## Agent-Friendly Session Pattern

```bash
# 1) verify index health
uvx sia-code status

# 2) initialize/index if needed
uvx sia-code init
uvx sia-code index .

# 3) investigate code
uvx sia-code search --regex "target_symbol"
uvx sia-code research "how does X work?"

# 4) record reusable decision
uvx sia-code memory add-decision "..." -d "..." -r "..."
```

## Troubleshooting

- If uninitialized: run `uvx sia-code init && uvx sia-code index .`
- If results look stale: run `uvx sia-code index --update` (or `--clean` after major refactors)
- If memory add/search fails with embedding issues: run `uvx sia-code embed start`
- If too much dependency noise: add `--no-deps`

## Notes

- Lexical search is often strong for code due to exact identifiers.
- Hybrid/semantic search may require embedding setup depending on configuration.
- Keep this file short and operational; move deep theory to project docs.
