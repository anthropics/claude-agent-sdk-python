---
updated: 2026-03-22
type: context
topic: claude-agent-sdk-overview
---

# Claude Agent SDK Python — Executive Summary

## What It Is

The Claude Agent SDK (`claude-agent-sdk`, v0.1.48) is a Python library that wraps Claude Code CLI as a subprocess, communicating via JSON streaming over stdin/stdout. It provides the same tools, agent loop, and context management that power Claude Code — packaged as a library for building AI agents programmatically. Python 3.10+, async-only (anyio).

## Architecture at a Glance

```
Your Python App
  ├── query()              → one-shot stateless queries
  └── ClaudeSDKClient      → interactive multi-turn sessions
        ↓
      Query                → control protocol handler (hub)
        ↓
      SubprocessCLITransport → manages CLI subprocess
        ↓
      Claude Code CLI      → executes prompts, uses tools
```

**5 layers:** Public API → Internal Processing → Transport → CLI → Cross-cutting types/errors

## Two Entry Points

| | `query()` | `ClaudeSDKClient` |
|---|-----------|-------------------|
| **Use for** | One-shot tasks, scripts, pipelines | Multi-turn conversations, agents |
| **State** | Stateless — creates and tears down | Stateful — maintains session |
| **Features** | Simple, fire-and-forget | Hooks, custom tools, interrupts, follow-ups |

## Key Extension Points

1. **SDK MCP Tools** — `@tool` decorator + `create_sdk_mcp_server()` — in-process tool execution
2. **Hooks** — PreToolUse, PostToolUse, Stop, etc. — Python async callbacks for controlling behavior
3. **Permission Callbacks** — `can_use_tool` — dynamic per-tool approval/denial
4. **External MCP Servers** — stdio/sse/http — managed by CLI as separate processes

## Critical Design Decisions

- **Bash is the most powerful tool** — General computer use > bespoke tools
- **Context Engineering > Prompt Engineering** — File system as state manager
- **Control protocol** — Request/response matching via `request_id` for bidirectional async communication
- **Field name conversion** — Python `async_`/`continue_` → wire format `async`/`continue`

## File Map (15 source files)

| Most Important | File | Why |
|---------------|------|-----|
| `_internal/query.py` | ~500 lines | Central hub — routes all control messages |
| `types.py` | ~800 lines | All public types — largest file |
| `client.py` | ~400 lines | Full-featured stateful client |
| `subprocess_cli.py` | ~400 lines | CLI lifecycle + JSON streaming |

## Diagrams Reference

| Diagram | File |
|---------|------|
| 4 Sequence Diagrams | `tasks/claudeagentsdk-554-diagrams.md` |
| 6 Architecture Diagrams | `tasks/claudeagentsdk-fl0-diagrams.md` |
| Use Case Diagram | `tasks/claudeagentsdk-7mq-usecase.md` |

## Learning Path

See `context/learning-resources.md` — 18 resources, ~4.5 hours recommended path.
