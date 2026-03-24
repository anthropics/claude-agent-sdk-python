---
date: 2026-03-21
type: context
topic: claude-agent-sdk-docs-summary
sources: [README.md, CLAUDE.md, CHANGELOG.md, RELEASING.md, AGENTS.md, e2e-tests/README.md, Context7]
sdk_version: 0.1.48
---

# Claude Agent SDK (Python) — Documentation Summary

## 1. SDK Concepts & Mental Model

- **What:** Python SDK wrapping Claude Code CLI as subprocess, communicating via JSON streaming over stdin/stdout
- **Package:** `claude-agent-sdk` on PyPI (formerly `claude-code-sdk`, renamed at v0.1.0)
- **Runtime:** Async-only via `anyio` (supports both asyncio and trio)
- **Python:** 3.10+ required
- **CLI:** Bundled with package since v0.1.8 — no separate install needed
- **Two entry points:**
  - `query()` — one-shot, stateless, fire-and-forget (returns `AsyncIterator[Message]`)
  - `ClaudeSDKClient` — bidirectional, stateful, multi-turn conversations with interrupt support
- **Key principle:** Always streaming internally — both entry points use `--input-format stream-json`

## 2. Feature Inventory (35+ ClaudeAgentOptions fields)

### Core Configuration
- `system_prompt` — custom or preset (`claude_code`), merged from old `custom_system_prompt` + `append_system_prompt`
- `permission_mode` — `"default"` | `"acceptEdits"` | `"plan"` | `"bypassPermissions"`
- `model` / `fallback_model` — model selection with automatic fallback (since v0.1.7)
- `max_turns` — limit conversation turns
- `max_budget_usd` — spending cap in USD (since v0.1.6)
- `cwd` — working directory (str or Path)
- `cli_path` — custom CLI binary path
- `effort` — `"low"` | `"medium"` | `"high"` | `"max"` thinking effort

### Tool & Permission Control
- `tools` — tool preset or custom list
- `allowed_tools` — auto-approve allowlist (does NOT remove tools, only pre-approves)
- `disallowed_tools` — block specific tools
- `can_use_tool` — Python callback for dynamic permission decisions
- `permission_prompt_tool_name` — custom permission prompt tool

### Hooks System (since v0.1.2, typed inputs since v0.1.3)
- `hooks` — dict mapping `HookEvent` → list of `HookMatcher`
- Events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `PreCompact`, `Notification`, `PermissionRequest`
- Hook inputs include `agent_id` and `agent_type` (since v0.1.46)
- Field name mapping: `async_` → `async`, `continue_` → `continue` (Python keyword avoidance)

### MCP (Model Context Protocol)
- `mcp_servers` — dict of server configs (SDK in-process or external subprocess)
- In-process: `@tool` decorator + `create_sdk_mcp_server()` — no subprocess overhead
- External: `{"type": "stdio", "command": "...", "args": [...]}` config
- Runtime management: `add_mcp_server()`, `remove_mcp_server()` (since v0.1.46)
- Tool naming: `mcp__{server_name}__{tool_name}`

### Advanced Features
- `agents` — programmatic subagent definitions (`AgentDefinition` dataclass)
- `plugins` — load custom plugins from local paths (`SdkPluginConfig`, since v0.1.5)
- `sandbox` — sandbox settings (`SandboxSettings` with network config)
- `setting_sources` — control which filesystem settings load (`["user", "project", "local"]`)
- `thinking` — extended thinking config (replaced deprecated `max_thinking_tokens`)
- `betas` — SDK beta features list
- `output_format` — structured output schema (since v0.1.7)

### Session & Streaming
- `continue_conversation` — resume previous conversation
- `resume` — resume by session ID
- `fork_session` — fork to new session when resuming
- `include_partial_messages` — enable `StreamEvent` messages
- `enable_file_checkpointing` — file checkpointing support
- `user` — user identifier for session
- `env` — environment variables for subprocess
- `extra_args` — additional CLI arguments

### Deprecated
- `debug_stderr` — replaced by `stderr` callback
- `max_thinking_tokens` — replaced by `thinking` config

## 3. Installation & Quick Start

- **Install:** `pip install claude-agent-sdk`
- **Prerequisites:** Python 3.10+, CLI bundled (optional: install separately via `curl -fsSL https://claude.ai/install.sh | bash`)
- **Dependencies:** `anyio>=4.0.0`, `mcp>=0.1.0`, `typing_extensions>=4.0.0` (Python < 3.11)
- **Quick start:**
  ```python
  import anyio
  from claude_agent_sdk import query
  async def main():
      async for message in query(prompt="What is 2 + 2?"):
          print(message)
  anyio.run(main)
  ```
- **Dev dependencies:** `pytest>=7.0.0`, `pytest-asyncio>=0.20.0`, `mypy>=1.0.0`, `ruff>=0.1.0`

## 4. Version History (Major Milestones)

| Version | Key Change |
|---------|-----------|
| 0.0.13-0.0.22 | Early development, basic query/client functionality |
| **0.1.0** | **BREAKING:** Renamed ClaudeCodeOptions → ClaudeAgentOptions, merged system prompt, settings isolation, no default system prompt |
| 0.1.1 | CLI version check (requires 2.0.0+), aligned permission types |
| 0.1.2-0.1.3 | Hook system matured: typed inputs, field name conversion fix |
| 0.1.5 | Plugin support added |
| 0.1.6 | Budget control (`max_budget_usd`), extended thinking |
| 0.1.7 | Structured outputs, fallback model, local CLI support |
| 0.1.8 | CLI bundled in package by default |
| 0.1.20 | Session renaming/tagging, custom transport support |
| 0.1.29 | Subagent hooks, agent definitions, fork session |
| 0.1.31 | Sandbox settings, network config |
| 0.1.40 | Forward-compatible: unknown message types silently skipped |
| 0.1.46 | Session history API, MCP runtime management, typed task messages, hook agent_id |
| **0.1.48** | Current — fine-grained tool streaming fix, CLI v2.1.71 |

**Pattern:** Most releases (30+) are CLI version bumps only. Major feature additions roughly every 5-10 versions.

## 5. Release & Development Workflow

### Release Process
- **Two release paths:** Automatic (CLI version bump triggers patch release) and Manual (GitHub Actions UI for minor/major bumps)
- **Auto-release flow:** CLI bump commit → Test workflow → auto-release.yml → builds wheels → publishes to PyPI → creates git tag + GitHub Release
- **Wheel targets:** 5 platforms (Linux x86_64, Linux aarch64, macOS ARM, macOS Intel, Windows AMD64)
- **Two version numbers:** SDK version (`_version.py`) and bundled CLI version (`_cli_version.py`)
- **Required secrets:** `PYPI_API_TOKEN`, `ANTHROPIC_API_KEY` (for changelog + e2e tests), `DEPLOY_KEY`

### Development Workflow
- **Lint:** `python -m ruff check src/ tests/ --fix` then `python -m ruff format src/ tests/`
- **Typecheck:** `python -m mypy src/` (tests not typechecked)
- **Test:** `python -m pytest tests/` (unit) or `python -m pytest e2e-tests/ -v` (e2e, requires API key)
- **Config:** `pyproject.toml` — strict mypy, ruff with E/W/F/I/N/UP/B/C4/PTH/SIM rules

## 6. Testing Infrastructure

### Unit Tests (tests/)
- 17 test files covering: client, query, transport, types, errors, message parser, sessions, MCP integration, tool callbacks, streaming, build wheel, changelog
- Framework: pytest-asyncio with `asyncio_mode = "auto"`
- Most tests mock the transport layer (see `conftest.py` fixtures)
- No API key required

### E2E Tests (e2e-tests/)
- **Require:** `ANTHROPIC_API_KEY` environment variable (tests FAIL without it)
- Verify real-world functionality against actual Claude API
- Focus: MCP calculator tools (add, divide, square root, power, multi-step, permissions)
- Cost: < $0.10 per full suite run
- Run on: push to main + manual dispatch via GitHub Actions

## 7. Agent Configuration (from AGENTS.md)
- Project uses `bd` (beads) for issue tracking — not markdown TODOs
- Agent workflow: `bd ready` → `bd update --claim` → work → `bd close`
- Issue types: bug, feature, task, epic, chore
- Priorities: 0 (critical) to 4 (backlog)
- Auto-sync: exports to `.beads/issues.jsonl` after changes
- Session completion: MUST `git push` before considering work done

## Discrepancies & Notes

- **Context7 vs local:** Context7 platform docs show the same 35+ fields as local types.py — consistent
- **Naming:** Package is `claude-agent-sdk` but internal module is `claude_agent_sdk` (underscore) — Python convention
- **Hook system evolution:** Significant bug fixes in v0.1.2-0.1.3 for hook field conversion — earlier versions had broken hooks
- **Settings isolation:** v0.1.0 breaking change means SDK apps don't load local settings by default — must explicitly opt-in via `setting_sources`
