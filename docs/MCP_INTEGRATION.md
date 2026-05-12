# MCP Integration

Use `sia-code-mcp` when your client supports Model Context Protocol.

## Install

```bash
pip install "sia-code[mcp]"
```

Or with `uv`:

```bash
uv tool install "sia-code[mcp]"
```

## Entry Point

```bash
sia-code-mcp
```

This runs a stdio MCP server.

## Transport

- v1 transport: stdio only
- One MCP server process per client session is expected
- Shared embedding reuse still comes from the machine-level embed daemon (`sia-code embed start`)

## Workspace Routing

Repo-scoped tools require an explicit `workspace_root`.

Optional override:

- `index_dir`: use a specific `.sia-code` directory instead of resolving from the workspace root

Resolution stays local-first and matches existing CLI behavior.

## Tool Coverage

Exposed in v1:

- `init`, `index`, `status`, `health_check`, `engineering_bootstrap`, `search`, `research`, `compact`
- `memory_sync_git`, `memory_add_decision`, `memory_list`, `memory_approve`, `memory_reject`
- `memory_search`, `memory_working_set`, `memory_trace`, `memory_timeline`, `memory_changelog`
- `memory_export`, `memory_import`
- `config_show`, `config_path`, `config_get`, `config_set`
- `embed_start`, `embed_status`, `embed_stop`

## Recommended First Call

For engineering work in Claude Code, OpenCode, Codex, or any MCP-aware client, prefer:

- `engineering_bootstrap` for planning, debugging, review, and complex repo questions
- `health_check` when you only want readiness/probe validation
- `search` for exact symbol lookup when you already know the target

`engineering_bootstrap` is designed to be the portable workflow entrypoint when the user has only configured `uvx sia-code-mcp` and no client-specific plugin or prompt customization.

Not exposed in v1:

- interactive CLI mode
- watch mode indexing
- config editor launching

## Local Models Only

Current MCP support is local-model-only.

- semantic search uses local sentence-transformers models
- shared model reuse uses the local embed daemon
- no remote embedding provider is required

## Token Efficiency

Defaults are optimized for MCP use:

- repo tools return structured JSON
- search/research default to compact result payloads
- large artifacts should be requested only when needed

## Fallback

If your client does not support MCP yet, use the fallback skill at `skills/sia-code/SKILL.md`.
