---
updated: 2026-03-22
type: context
topic: claude-agent-sdk-cheatsheet
---

# Claude Agent SDK — Quick Reference Card

## Install

```bash
pip install claude-agent-sdk
# Requires: Python 3.10+, Node.js 18+ (for CLI)
```

## 1. Simple One-Shot Query

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for msg in query(prompt="What is 2+2?"):
    print(msg)
```

## 2. Query with Options

```python
options = ClaudeAgentOptions(
    system_prompt="You are a Python expert",
    max_turns=3,
    permission_mode="bypassPermissions",
    cwd="/path/to/project",
)
async for msg in query(prompt="Fix the bug in main.py", options=options):
    print(msg)
```

## 3. Interactive Multi-Turn Session

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

async with ClaudeSDKClient(options=ClaudeAgentOptions()) as client:
    await client.query("Analyze this codebase")
    async for msg in client.receive_response():
        print(msg)

    await client.query("Now fix the bug you found")
    async for msg in client.receive_response():
        print(msg)
```

## 4. Type-Safe Message Handling

```python
from claude_agent_sdk import (
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
)

async for msg in client.receive_response():
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(block.text)
            elif isinstance(block, ToolUseBlock):
                print(f"Tool: {block.name}({block.input})")
    elif isinstance(msg, ResultMessage):
        print(f"Cost: ${msg.total_cost_usd:.4f}")
        print(f"Duration: {msg.duration_ms}ms")
```

## 5. Custom MCP Tool (In-Process)

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("get_weather", "Get weather for a city", {"city": str})
async def get_weather(args):
    return {"content": [{"type": "text", "text": f"Sunny in {args['city']}"}]}

server = create_sdk_mcp_server(name="weather", tools=[get_weather])
options = ClaudeAgentOptions(
    mcp_servers={"weather": server},
    allowed_tools=["mcp__weather__get_weather"],
)
```

## 6. Hook: Block Dangerous Tools

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher

async def block_rm(input_data, tool_use_id, context):
    cmd = input_data.get("command", "")
    if "rm -rf" in cmd:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "rm -rf blocked"
        }}
    return {}

options = ClaudeAgentOptions(
    hooks={"PreToolUse": [HookMatcher(
        matcher={"tool_name": "Bash"},
        hooks=[block_rm]
    )]}
)
```

## 7. Permission Callback (Approve/Deny per Tool)

```python
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

async def can_use_tool(tool_name, tool_input, context):
    if tool_name == "Bash" and "sudo" in tool_input.get("command", ""):
        return PermissionResultDeny(message="No sudo allowed")
    return PermissionResultAllow()

options = ClaudeAgentOptions(can_use_tool=can_use_tool)
```

## 8. Budget Control

```python
options = ClaudeAgentOptions(max_budget_usd=0.50)
async for msg in query(prompt="...", options=options):
    if isinstance(msg, ResultMessage) and msg.subtype == "error_max_budget_usd":
        print("Budget exceeded!")
```

## 9. Interrupt Mid-Generation

```python
async with ClaudeSDKClient(options=options) as client:
    await client.query("Write a long essay...")
    # After receiving some messages:
    await client.interrupt()
```

## 10. Agent Orchestration (Sub-Agents)

```python
from claude_agent_sdk import AgentDefinition

options = ClaudeAgentOptions(
    agents={
        "reviewer": AgentDefinition(
            description="Reviews code for bugs",
            prompt="You are a code reviewer. Find bugs.",
            model="claude-sonnet-4-5",
        ),
    }
)
```

## 11. Runtime Model & Permission Changes

```python
async with ClaudeSDKClient(options=options) as client:
    await client.set_model("claude-sonnet-4-5")
    await client.set_permission_mode("acceptEdits")
```

## 12. Partial Message Streaming (Real-Time UI)

```python
from claude_agent_sdk import StreamEvent

options = ClaudeAgentOptions(include_partial_messages=True)
async with ClaudeSDKClient(options=options) as client:
    await client.query("Write a story")
    async for msg in client.receive_response():
        if isinstance(msg, StreamEvent):
            print(msg)  # Partial content as it's generated
```

## 13. Settings Isolation

```python
# No filesystem configs loaded — fully isolated
options = ClaudeAgentOptions(setting_sources=[])

# Only user-level settings
options = ClaudeAgentOptions(setting_sources=["user"])
```

## 14. Read Historical Sessions

```python
from claude_agent_sdk import list_sessions, get_session_messages

sessions = list_sessions(cwd="/path/to/project")
messages = get_session_messages(session_id="...", cwd="/path/to/project")
```

---

## Error Hierarchy

```
ClaudeSDKError (base)
├── CLIConnectionError → CLINotFoundError
├── ProcessError (exit_code, stderr)
├── CLIJSONDecodeError
└── MessageParseError
```

## Message Types

```
Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage
        | StreamEvent | RateLimitEvent
        | TaskStartedMessage | TaskProgressMessage | TaskNotificationMessage
```

## Content Blocks

```
TextBlock          — text content
ThinkingBlock      — extended thinking
ToolUseBlock       — tool call (id, name, input)
ToolResultBlock    — tool result (tool_use_id, content, is_error)
```

## Hook Events

```
PreToolUse, PostToolUse, PostToolUseFailure,
UserPromptSubmit, Stop, SubagentStart, SubagentStop,
PreCompact, Notification, PermissionRequest
```

## Decision Guide

```
Stateless, one-shot?           → query()
Multi-turn, hooks, interrupt?  → ClaudeSDKClient
In-process tools?              → @tool + create_sdk_mcp_server()
External services?             → MCP servers (stdio/sse/http)
Control tool execution?        → Hooks (PreToolUse) or can_use_tool
```
