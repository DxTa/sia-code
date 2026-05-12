# LLM CLI Integration

Use the packaged MCP server for clients that support MCP. Keep the skill file only as a fallback for environments that do not.

## 1) Preferred: install MCP support

```bash
pip install "sia-code[mcp]"
```

Or with `uv`:

```bash
uv tool install "sia-code[mcp]"
```

MCP entrypoint:

```bash
sia-code-mcp
```

See `docs/MCP_INTEGRATION.md` for client configuration examples.

## 2) Fallback: copy the skill file

Source file in this repo:

- `skills/sia-code/SKILL.md`

Copy it into your local CLI skill directory.

### OpenCode example

```bash
mkdir -p ~/.config/opencode/skills/sia-code
cp skills/sia-code/SKILL.md ~/.config/opencode/skills/sia-code/SKILL.md
```

Then restart OpenCode (or reload skills if your setup supports hot reload).

## 3) Use the skill in your prompt/session

Typical invocation:

```text
Load skill sia-code
```

## 4) Recommended MCP workflow

For MCP-capable clients, configure the server once and let the client call tools directly.

Recommended tool order for engineering tasks:

1. `engineering_bootstrap` for planning, debugging, review, and complex technical repo questions
2. `search` for exact symbol or file lookup when you already know what to search
3. `research` only when bootstrap/health indicates multi-hop tracing is ready

Conceptually, the client should do the equivalent of:

```text
engineering_bootstrap(
  workspace_root="/path/to/repo",
  task="Plan a fix for auth token refresh regression",
  research_mode="auto"
)
```

Fallback CLI flow when testing manually:

```bash
uvx sia-code status
uvx sia-code init
uvx sia-code index .
uvx sia-code search --regex "your_symbol"
uvx sia-code research "how does X work?"
```

## 5) Optional memory workflow

```bash
uvx sia-code memory sync-git
uvx sia-code memory search "topic"
uvx sia-code memory add-decision "Decision title" -d "Context" -r "Reason"
uvx sia-code memory working-set "auth flow" --agent planner --session-id ses-123 -o shared-memory.json
```

`memory working-set` is the recommended handoff artifact when one agent/session needs to pass query-scoped repo context to another.

## 5b) Local development build instead of PyPI

When testing unreleased local changes, prefer the checkout directly instead of `uvx`:

```bash
uv run --project /home/dxta/dev/sia-code python -m sia_code.cli status
uv run --project /home/dxta/dev/sia-code python -m sia_code.cli memory working-set "auth flow"
```

If you want a shell-level `sia-code` command backed by the local checkout:

```bash
uv tool install --force --from /home/dxta/dev/sia-code sia-code
```

## 6) Multiple worktrees / multiple Claude Code instances

Use one of these index strategies per session:

```bash
# Shared index across worktrees/instances (best for reuse)
export SIA_CODE_INDEX_SCOPE=shared

# Isolated index per worktree/instance
export SIA_CODE_INDEX_SCOPE=worktree
```

If you need full control, set an explicit directory:

```bash
export SIA_CODE_INDEX_DIR=/absolute/path/to/sia-index
```

Recommendation:

- Shared mode for many search/read sessions
- Worktree mode when you want strict isolation per branch/agent
- For shared mode, avoid many simultaneous index writers

## Notes

- Prefer MCP when the client supports it; it removes the need to distribute prompt-side workflow files.
- `engineering_bootstrap` is the intended portable first-call surface for engineering workflows when users only add `uvx sia-code-mcp` to their MCP config.
- Keep the skill file short and practical for fallback environments.
- Update this file when CLI behavior changes.
- Keep both PyPI and local-checkout workflows documented during active development.
