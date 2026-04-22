# LLM CLI Integration

Use this repo's compact skill to help LLM CLI agents work with `sia-code` reliably.

## 1) Copy the skill file

Source file in this repo:

- `skills/sia-code/SKILL.md`

Copy it into your local CLI skill directory.

### OpenCode example

```bash
mkdir -p ~/.config/opencode/skills/sia-code
cp skills/sia-code/SKILL.md ~/.config/opencode/skills/sia-code/SKILL.md
```

Then restart OpenCode (or reload skills if your setup supports hot reload).

## 2) Use the skill in your prompt/session

Typical invocation:

```text
Load skill sia-code
```

## 3) Recommended agent workflow

```bash
uvx sia-code status
uvx sia-code init
uvx sia-code index .
uvx sia-code search --regex "your symbol"
uvx sia-code research "how does X work?"
```

## 4) Optional memory workflow

```bash
uvx sia-code memory sync-git
uvx sia-code memory search "topic"
uvx sia-code memory add-decision "Decision title" -d "Context" -r "Reason"
uvx sia-code memory working-set "auth flow" --agent planner --session-id ses-123 -o shared-memory.json
```

`memory working-set` is the recommended handoff artifact when one agent/session needs to pass query-scoped repo context to another.

## 4b) Local development build instead of PyPI

When testing unreleased local changes, prefer the checkout directly instead of `uvx`:

```bash
uv run --project /home/dxta/dev/sia-code python -m sia_code.cli status
uv run --project /home/dxta/dev/sia-code python -m sia_code.cli memory working-set "auth flow"
```

If you want a shell-level `sia-code` command backed by the local checkout:

```bash
uv tool install --force --from /home/dxta/dev/sia-code sia-code
```

## 5) Multiple worktrees / multiple Claude Code instances

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

- Keep the skill file short and practical for agent speed.
- Update this file when CLI behavior changes.
- Keep both PyPI (`uvx`) and local-checkout workflows documented during active development.
