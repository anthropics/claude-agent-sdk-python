---
date: 2026-03-21
type: context
topic: claude-agent-sdk-code-architecture
sdk_version: 0.1.48
source_files: 15
---

# Claude Agent SDK — Code Architecture

## 1. Annotated Directory Tree

```
src/claude_agent_sdk/
├── __init__.py              # Public exports + @tool decorator + create_sdk_mcp_server()
│                            # Defines SdkMcpTool dataclass, tool() decorator, MCP server factory
│                            # 445 lines — the main "glue" file
├── query.py                 # query() async generator — one-shot entry point
│                            # Delegates to InternalClient.process_query()
│                            # 124 lines — thin wrapper
├── client.py                # ClaudeSDKClient — stateful bidirectional session manager
│                            # Supports: connect, query, receive_response, interrupt,
│                            # set_permission_mode, set_model, add/remove MCP servers
│                            # ~400 lines
├── types.py                 # ALL public types: Message union, ClaudeAgentOptions (35+ fields),
│                            # hooks (HookEvent, HookMatcher, HookCallback), content blocks,
│                            # MCP configs, permissions, agents, sandbox, plugins
│                            # ~800 lines — largest file
├── _errors.py               # Error hierarchy: ClaudeSDKError → 5 subclasses
│                            # 57 lines
├── _version.py              # SDK version string (e.g., "0.1.48")
├── _cli_version.py          # Bundled CLI version string (e.g., "2.1.79")
│
├── _internal/
│   ├── __init__.py           # Exports Transport abstract class
│   ├── client.py             # InternalClient — used by query() only
│   │                         # Creates transport, Query, handles prompt modes
│   │                         # 146 lines
│   ├── query.py              # Query — CORE: control protocol handler
│   │                         # Manages: initialize handshake, request/response routing,
│   │                         # hook callback dispatch, tool permission callbacks,
│   │                         # SDK MCP tool calls, message streaming via anyio task groups
│   │                         # ~500 lines — most complex file
│   ├── message_parser.py     # parse_message() — JSON dict → typed Message objects
│   │                         # Handles: user, assistant, system, result, stream_event,
│   │                         # rate_limit_event, task_started/progress/notification
│   │                         # Unknown types → returns None (forward-compatible)
│   ├── sessions.py           # list_sessions(), get_session_messages()
│   │                         # Reads historical data from ~/.claude/projects/
│   │                         # Ported from TypeScript SDK
│   ├── session_mutations.py  # rename_session(), tag_session()
│   │                         # Appends metadata entries to session JSONL files
│   │
│   └── transport/
│       ├── __init__.py       # Transport abstract class (connect, write, read_messages, close)
│       └── subprocess_cli.py # SubprocessCLITransport — THE transport implementation
│                             # CLI binary discovery (bundled > system > known paths)
│                             # Subprocess lifecycle (stdin/stdout/stderr pipes)
│                             # JSON streaming with buffered parsing, 1MB buffer limit
│                             # ~400 lines
```

**Total: 15 Python source files** (confirmed via Glob)

## 2. Flow: query() One-Shot

```
User Code                query.py           _internal/client.py      _internal/query.py        transport/subprocess_cli.py    CLI Process
   │                        │                      │                        │                            │                         │
   │ query(prompt, opts)    │                      │                        │                            │                         │
   │───────────────────────>│                      │                        │                            │                         │
   │                        │ InternalClient()     │                        │                            │                         │
   │                        │─────────────────────>│                        │                            │                         │
   │                        │                      │ create transport       │                            │                         │
   │                        │                      │ SubprocessCLITransport(prompt, options)              │                         │
   │                        │                      │───────────────────────────────────────────────────>  │                         │
   │                        │                      │ transport.connect()    │                            │                         │
   │                        │                      │───────────────────────────────────────────────────>  │                         │
   │                        │                      │                        │                            │ spawn subprocess        │
   │                        │                      │                        │                            │ --input-format stream-json
   │                        │                      │                        │                            │──────────────────────>  │
   │                        │                      │ Query(transport, hooks, sdk_mcp_servers)             │                         │
   │                        │                      │───────────────────────>│                            │                         │
   │                        │                      │ query.start()          │                            │                         │
   │                        │                      │───────────────────────>│ create task_group           │                         │
   │                        │                      │                        │ start _read_messages()      │                         │
   │                        │                      │ query.initialize()     │                            │                         │
   │                        │                      │───────────────────────>│ send {"subtype":"initialize", hooks, agents}          │
   │                        │                      │                        │─────────────────────────>  │──────────────────────>  │
   │                        │                      │                        │                            │ <── initialize response │
   │                        │                      │                        │ <── control_response match request_id                │
   │                        │                      │ write user message     │                            │                         │
   │                        │                      │───────────────────────────────────────────────────>  │──────────────────────>  │
   │                        │                      │                        │                            │                         │
   │                        │                      │                        │                            │ <── JSON messages       │
   │                        │                      │                        │ _read_messages() routes:    │                         │
   │                        │                      │                        │   control_response → pending│                         │
   │                        │                      │                        │   control_request → handle  │                         │
   │                        │                      │                        │   other → message stream    │                         │
   │                        │                      │ receive_messages()     │                            │                         │
   │                        │                      │<──────────────────────│ yield from stream           │                         │
   │                        │ parse_message(data)  │                        │                            │                         │
   │<── yield Message ──────│                      │                        │                            │                         │
   │                        │                      │ query.close()          │                            │                         │
   │                        │                      │───────────────────────>│ cancel task_group           │                         │
   │                        │                      │                        │ transport.close()           │                         │
   │                        │                      │                        │─────────────────────────>  │ kill subprocess         │
```

**Key steps:**
1. `query()` creates `InternalClient` → `InternalClient.process_query()`
2. `InternalClient` creates `SubprocessCLITransport(prompt, options)` → `transport.connect()` spawns CLI subprocess with `--input-format stream-json`
3. `InternalClient` creates `Query(transport, hooks, sdk_mcp_servers)` → `query.start()` creates anyio task group, spawns `_read_messages()` background task
4. `query.initialize()` sends initialize control request via stdin JSON → CLI responds with capabilities
5. User message written to stdin → CLI processes → streams JSON messages on stdout
6. `_read_messages()` routes: `control_response` → pending dict (request_id match), `control_request` → handler, regular messages → memory stream
7. `receive_messages()` yields from memory stream → `parse_message()` converts to typed `Message` objects → yielded to user
8. On stream end, `query.close()` → cancel task group → `transport.close()` → kill subprocess

## 3. Flow: ClaudeSDKClient Interactive

```
1. async with ClaudeSDKClient(options) as client:
   → client.__aenter__() → client.connect()

2. connect():
   → Create empty async iterable for keepalive
   → SubprocessCLITransport(empty_stream, options) → transport.connect() → spawn CLI
   → Query(transport, ...) → query.start() → query.initialize()
   → If initial prompt: start streaming in task group

3. client.query("first prompt"):
   → Writes {"type":"user", "message":{"role":"user","content":"..."}} to stdin via transport

4. async for msg in client.receive_response():
   → client.receive_messages() → query.receive_messages() → parse_message()
   → Yields AssistantMessage, ToolUseBlock, ResultMessage, etc.

5. client.query("follow-up"):
   → Same as step 3 — session context maintained by CLI

6. client.interrupt():
   → query.interrupt() → sends control request {"subtype":"interrupt"} to CLI

7. client.set_permission_mode("acceptEdits"):
   → query.set_permission_mode() → control request to CLI

8. client.set_model("claude-sonnet-4-5"):
   → query.set_model() → control request to CLI

9. client.__aexit__():
   → query.close() → transport.close() → kill subprocess
```

**Key difference from query():** ClaudeSDKClient keeps the subprocess alive across multiple query/receive cycles. Session state is maintained by the CLI process.

## 4. Flow: Hook Callback Dispatch

```
1. CLI encounters hook point (e.g., about to use Bash tool)
   → CLI sends control_request to stdout:
     {"type":"control_request", "request_id":"uuid-123",
      "request":{"subtype":"hook_callback", "callback_id":"hook_0",
                 "input":{"tool_name":"Bash","tool_input":{"command":"ls"}},
                 "tool_use_id":"tool-456"}}

2. Query._read_messages() receives control_request
   → Routes to _handle_control_request() in task group

3. _handle_control_request() checks subtype == "hook_callback"
   → Looks up callback_id "hook_0" in self.hook_callbacks dict
   → Callback was registered during initialize() from HookMatcher.hooks list

4. Calls user Python async function:
   result = await callback(input_data, tool_use_id, {"signal": None})

   User function returns e.g.:
   {"hookSpecificOutput": {"hookEventName": "PreToolUse",
    "permissionDecision": "deny", "permissionDecisionReason": "Blocked"}}

5. _convert_hook_output_for_cli() converts Python field names:
   async_ → async, continue_ → continue

6. Query sends control_response to CLI via stdin:
   {"type":"control_response", "response":{"subtype":"success",
    "request_id":"uuid-123", "response":{...converted hook output...}}}

7. CLI receives response → blocks tool use (deny) or continues (approve)
```

**Hook events:** PreToolUse, PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop, SubagentStart, SubagentStop, PreCompact, Notification, PermissionRequest

**can_use_tool callback:** Similar flow but subtype is "can_use_tool" → callback returns PermissionResultAllow or PermissionResultDeny

## 5. Flow: MCP In-Process Tool Call

```
1. Claude (via CLI) decides to call MCP tool "mcp__calc__add"
   → CLI sends control_request:
     {"type":"control_request", "request_id":"uuid-789",
      "request":{"subtype":"mcp_message", "server_name":"calc",
                 "message":{"method":"tools/call", "params":{"name":"add","arguments":{"a":1,"b":2}}}}}

2. Query._handle_control_request() checks subtype == "mcp_message"
   → Extracts server_name="calc" and mcp_message

3. Query._handle_sdk_mcp_request(server_name, mcp_message):
   → Looks up server in self.sdk_mcp_servers["calc"] → gets MCP Server instance
   → For "tools/call": creates CallToolRequest, calls server.call_tool()
   → For "tools/list": creates ListToolsRequest, calls server.list_tools()

4. MCP Server.call_tool("add", {"a":1, "b":2}):
   → Looks up "add" in tool_map (registered by create_sdk_mcp_server)
   → Calls @tool-decorated async function: add_numbers({"a":1, "b":2})
   → Returns {"content": [{"type":"text", "text":"Sum: 3"}]}

5. Query wraps result as MCP response:
   → {"mcp_response": {"result": {"content": [...]}}}

6. Sends control_response with matching request_id to CLI

7. CLI processes tool result → continues generation
```

**Key insight:** SDK MCP tools run IN-PROCESS (same Python process). External MCP servers run as separate subprocesses managed by CLI — those never hit Query._handle_sdk_mcp_request().

## 6. Error Hierarchy

```
ClaudeSDKError (base)
├── CLIConnectionError          — Cannot connect to CLI
│   └── CLINotFoundError        — CLI binary not found (bundled/system/known paths)
├── ProcessError                — CLI process failed (exit_code, stderr)
├── CLIJSONDecodeError          — Cannot parse JSON from CLI stdout
└── MessageParseError           — Cannot parse Message from valid JSON dict
```

## 7. Type System Overview

### Message Types (union)
- `UserMessage` — user input with content blocks
- `AssistantMessage` — Claude response with content blocks
- `SystemMessage` — system messages
- `ResultMessage` — conversation turn result (cost, duration, stop_reason)
- `StreamEvent` — partial message streaming events (when include_partial_messages=True)
- `RateLimitEvent` — rate limit information
- `TaskStartedMessage`, `TaskProgressMessage`, `TaskNotificationMessage` — task lifecycle

### Content Blocks
- `TextBlock` — text content
- `ThinkingBlock` — extended thinking content
- `ToolUseBlock` — tool call (id, name, input)
- `ToolResultBlock` — tool result (tool_use_id, content, is_error)

### Configuration
- `ClaudeAgentOptions` — 35+ fields (see docs-summary.md for full inventory)
- `McpServerConfig` / `McpSdkServerConfig` — MCP server definitions
- `AgentDefinition` — subagent definitions
- `SandboxSettings` / `SandboxNetworkConfig` — sandbox config
- `ThinkingConfig` — extended thinking (enabled/disabled/adaptive)

### Hook Types
- `HookEvent` = Literal["PreToolUse", "PostToolUse", ...] (10 events)
- `HookMatcher` — matcher pattern + list of callback functions
- `HookCallback` — async function(input_data, tool_use_id, context) → dict
- Typed inputs: `PreToolUseHookInput`, `PostToolUseHookInput`, etc.

### Permission Types
- `PermissionMode` = Literal["default", "acceptEdits", "plan", "bypassPermissions"]
- `CanUseTool` — callback type for dynamic permission decisions
- `PermissionResultAllow` / `PermissionResultDeny` — callback return types
- `PermissionUpdate` — permission rule modifications

### Patterns
- Dataclasses for simple value types (TextBlock, ToolUseBlock, AgentDefinition)
- TypedDict for wire-format types (SDKControlRequest, SDKControlResponse)
- Union types for Message (Message = UserMessage | AssistantMessage | ...)
- Literal types for enums (PermissionMode, HookEvent)
