---
date: 2026-03-22
type: context
topic: claude-agent-sdk-use-case-guide
sources: [examples/ (16 files), Context7 demos (345 snippets)]
sdk_version: 0.1.48
---

# Claude Agent SDK — Use Case Guide

## Use Case Categories

### 1. One-Shot Query (Automation, CI/CD, Scripts)
- **Entry point:** `query()`
- **Pattern:** Fire-and-forget, no session state, auto-cleanup
- **Examples:** `quick_start.py`, `system_prompt.py`, `max_budget_usd.py`, `agents.py`, `tools_option.py`, `plugin_example.py`, `stderr_callback_example.py`
- **When:** Single prompt, no follow-ups needed, batch processing
- **Key features:** system_prompt, max_turns, max_budget_usd, tools, agents, plugins

### 2. Interactive Conversation (Chat UI, REPL, Debugging)
- **Entry point:** `ClaudeSDKClient`
- **Pattern:** Session lifecycle (connect → query → receive → follow-up → disconnect), multi-turn context
- **Examples:** `streaming_mode.py` (10 patterns!), `streaming_mode_trio.py`, `streaming_mode_ipython.py`
- **When:** Multi-turn conversations, need to react to responses, real-time UIs
- **Key features:** multi-turn, interrupt, concurrent send/receive, trio support

### 3. Custom Tools via MCP (Extend Claude Capabilities)
- **Entry point:** `ClaudeSDKClient` (preferred) or `query()`
- **Pattern:** `@tool` decorator → `create_sdk_mcp_server()` → `mcp_servers` option
- **Examples:** `mcp_calculator.py`
- **Context7 demos:** Email server with search_inbox + read_emails tools (TypeScript)
- **When:** Need domain-specific tools, in-process execution, no subprocess overhead
- **Key features:** @tool, create_sdk_mcp_server, mcp_servers, allowed_tools naming (`mcp__{server}__{tool}`)

### 4. Hook-Based Control (Security, Logging, Validation)
- **Entry point:** `ClaudeSDKClient`
- **Pattern:** HookMatcher with async callbacks → approve/deny/modify tool use
- **Examples:** `hooks.py` (5 patterns: PreToolUse block, UserPromptSubmit inject, PostToolUse review, permission decisions, stop control)
- **Context7 demos:** File write path validation hook (TypeScript)
- **When:** Need deterministic safety gates, audit logging, content filtering
- **Key features:** hooks dict, HookMatcher, PreToolUse/PostToolUse/UserPromptSubmit/Stop events
- **Important:** Hook field names use `async_`/`continue_` in Python (keyword avoidance)

### 5. Permission & Safety Management (Access Control)
- **Entry point:** `ClaudeSDKClient`
- **Pattern:** `can_use_tool` callback → PermissionResultAllow/Deny → optional input rewriting
- **Examples:** `tool_permission_callback.py`
- **When:** Fine-grained tool access control, input sanitization, interactive approval
- **Key features:** can_use_tool, PermissionResultAllow (updated_input), PermissionResultDeny (message), permission_mode

### 6. Agent Orchestration (Multi-Agent, Delegation)
- **Entry point:** `query()` or `ClaudeSDKClient`
- **Pattern:** `AgentDefinition` with description/prompt/tools/model → agents dict in options
- **Examples:** `agents.py` (programmatic), `filesystem_agents.py` (disk-based .md files)
- **When:** Specialized sub-agents (reviewer, writer, analyzer), delegation patterns
- **Key features:** AgentDefinition, agents dict, setting_sources for filesystem agents

### 7. Streaming & Real-Time (Live UIs, Progress)
- **Entry point:** `ClaudeSDKClient`
- **Pattern:** `include_partial_messages=True` → StreamEvent messages during generation
- **Examples:** `include_partial_messages.py`, `streaming_mode.py`
- **Context7 demos:** WebSocket chat with MessageQueue (TypeScript)
- **When:** Need real-time UI updates, progressive rendering, typing indicators
- **Key features:** include_partial_messages, StreamEvent, receive_response async iteration

### 8. Budget & Cost Control
- **Entry point:** `query()`
- **Pattern:** `max_budget_usd` option → auto-terminates on exceed → detect via ResultMessage.subtype
- **Examples:** `max_budget_usd.py` (3 scenarios: no limit, reasonable, tight)
- **When:** Cost-sensitive applications, rate limiting, sandbox environments
- **Key features:** max_budget_usd, ResultMessage (total_cost_usd, subtype="error_max_budget_usd")

### 9. Configuration & Settings Management
- **Entry point:** `ClaudeSDKClient` or `query()`
- **Pattern:** `setting_sources` controls which filesystem configs load (None = isolated)
- **Examples:** `setting_sources.py`, `tools_option.py`, `system_prompt.py`
- **When:** Need predictable SDK behavior, settings isolation, custom tool sets
- **Key features:** setting_sources (["user","project","local"]), tools (array/preset/empty), system_prompt (string/preset/append)

### 10. Plugin System
- **Entry point:** `query()`
- **Pattern:** `plugins` option with local path → extends CLI capabilities
- **Examples:** `plugin_example.py`
- **When:** Custom commands, extending CLI behavior
- **Key features:** plugins list, SdkPluginConfig, local plugin paths

---

## Decision Matrix: query() vs ClaudeSDKClient

| Scenario | Recommended | Reason | Example File |
|----------|-------------|--------|--------------|
| Single prompt, no follow-up | `query()` | Simpler API, auto-cleanup, no session management | `quick_start.py` |
| Multi-turn conversation | `ClaudeSDKClient` | Maintains session state, context preserved | `streaming_mode.py` |
| CI/CD pipeline automation | `query()` | Stateless, fire-and-forget, batch-friendly | `quick_start.py` |
| Custom MCP tools | Either (prefer Client) | Both support MCP; Client allows dynamic server management | `mcp_calculator.py` |
| Hook-based security | `ClaudeSDKClient` | Hooks need session context for stateful decisions | `hooks.py` |
| Real-time streaming UI | `ClaudeSDKClient` | Partial messages, interrupt support, bidirectional | `include_partial_messages.py` |
| Agent orchestration | Either | Both support agents dict; query() simpler for one-shot delegation | `agents.py` |
| Budget-limited batch | `query()` | Simple budget cap, auto-terminate, detect via ResultMessage | `max_budget_usd.py` |
| Permission callback (approve/deny) | `ClaudeSDKClient` | can_use_tool requires streaming mode | `tool_permission_callback.py` |
| Settings isolation testing | Either | Both support setting_sources | `setting_sources.py` |
| Interrupt mid-generation | `ClaudeSDKClient` | Only Client supports interrupt() | `streaming_mode.py` |
| Trio async runtime | `ClaudeSDKClient` | Only Client demonstrated with trio | `streaming_mode_trio.py` |

**Rule of thumb:** Use `query()` for anything stateless. Use `ClaudeSDKClient` if you need ANY of: multi-turn, hooks, interrupts, partial messages, dynamic tool/model changes.

---

## Common Patterns (Do This)

### Pattern 1: Proper async context manager
```python
async with ClaudeSDKClient(options=options) as client:
    await client.query("prompt")
    async for msg in client.receive_response():
        process(msg)
# Resources auto-cleaned on exit
```

### Pattern 2: Type-safe message handling
```python
if isinstance(message, AssistantMessage):
    for block in message.content:
        if isinstance(block, TextBlock):
            print(block.text)
        elif isinstance(block, ToolUseBlock):
            print(f"Tool: {block.name}")
elif isinstance(message, ResultMessage):
    print(f"Cost: ${message.total_cost_usd:.4f}")
```

### Pattern 3: Hook callback structure
```python
async def my_hook(input_data, tool_use_id, context):
    if dangerous(input_data):
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Blocked"
        }}
    return {}  # Empty = allow
```

### Pattern 4: MCP tool definition
```python
@tool("name", "description", {"param": str})
async def my_tool(args):
    return {"content": [{"type": "text", "text": result}]}

server = create_sdk_mcp_server(name="server", tools=[my_tool])
options = ClaudeAgentOptions(
    mcp_servers={"srv": server},
    allowed_tools=["mcp__srv__name"]
)
```

### Pattern 5: Budget control
```python
options = ClaudeAgentOptions(max_budget_usd=0.50)
async for msg in query(prompt="...", options=options):
    if isinstance(msg, ResultMessage) and msg.subtype == "error_max_budget_usd":
        print("Budget exceeded!")
```

---

## Anti-Patterns (Avoid This)

### Anti-Pattern 1: Forgetting async context manager
```python
# BAD - resources may leak
client = ClaudeSDKClient(options=options)
await client.connect()
# ... forgot to disconnect

# GOOD - auto-cleanup
async with ClaudeSDKClient(options=options) as client:
    ...
```

### Anti-Pattern 2: Blocking in async callbacks
```python
# BAD - blocks event loop
async def my_hook(input_data, tool_use_id, context):
    time.sleep(5)  # BLOCKS!
    return {}

# GOOD - use async sleep
async def my_hook(input_data, tool_use_id, context):
    await anyio.sleep(5)  # Non-blocking
    return {}
```

### Anti-Pattern 3: Ignoring ResultMessage
```python
# BAD - miss cost data and errors
async for msg in query(prompt="..."):
    if isinstance(msg, AssistantMessage):
        print(msg)

# GOOD - always check ResultMessage
async for msg in query(prompt="..."):
    if isinstance(msg, ResultMessage):
        print(f"Cost: ${msg.total_cost_usd}, Duration: {msg.duration_ms}ms")
```

### Anti-Pattern 4: Hardcoding CLI path
```python
# BAD - breaks portability
options = ClaudeAgentOptions(cli_path="/usr/local/bin/claude")

# GOOD - let SDK discover (bundled > system > known paths)
options = ClaudeAgentOptions()  # Auto-discovery
```

### Anti-Pattern 5: Using can_use_tool with string prompt
```python
# BAD - raises ValueError
options = ClaudeAgentOptions(can_use_tool=my_callback)
async for msg in query(prompt="hello", options=options):  # ERROR!

# GOOD - use async iterable prompt or ClaudeSDKClient
async with ClaudeSDKClient(options=options) as client:
    await client.query("hello")
```

---

## Context7 Demo Patterns (Advanced, TypeScript)

1. **WebSocket Chat Session:** MessageQueue pattern for multi-turn via streaming query
2. **PreToolUse File Validation:** Restrict file writes to specific directories
3. **Custom Email MCP Server:** Domain-specific MCP tools with error handling
4. **Event-Driven AI Classification:** Listener pattern with `callAgent()` for AI decisions

These TypeScript patterns translate to Python using ClaudeSDKClient equivalents.
