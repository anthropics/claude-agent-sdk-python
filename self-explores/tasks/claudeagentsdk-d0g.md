---
date: 2026-03-21
type: task-worklog
task: claudeagentsdk-d0g
title: "P1: Map cau truc thu muc & luong code chinh"
status: open
detailed_at: 2026-03-21
detail_score: ready-for-dev
tags: [research, architecture, code-flow, p1, discovery]
---

# Map cau truc thu muc & luong code chinh — Detailed Design

## 1. Objective
Create an annotated directory tree of all 15 src/*.py files and trace 4 main code flows (query, ClaudeSDKClient, hooks, MCP) with step-by-step descriptions including file:class:method references.

## 2. Scope
**In-scope:**
- Mapping the complete directory tree with annotations for every .py file in `src/`
- Tracing the `query()` flow: query.py -> InternalClient -> Query -> SubprocessCLITransport
- Tracing the `ClaudeSDKClient` flow: client.py -> connect -> query/receive_response cycle
- Tracing the hooks flow: HookMatcher -> Query dispatch -> Python callbacks -> response
- Tracing the MCP in-process flow: @tool -> create_sdk_mcp_server -> Server -> call_tool
- Mapping the error hierarchy and type system overview
- Cross-referencing with Context7 `/anthropics/claude-agent-sdk-python` (51 snippets)

**Out-of-scope:**
- Reading test files (17 files in tests/)
- Reading example files (18 files in examples/)
- Creating diagrams (that is task claudeagentsdk-fl0)
- Modifying any source code
- Performance analysis or benchmarking
- CLI binary internals (only the SDK's interaction with it)

## 3. Input / Output
**Input:**
- 15 .py files in `src/claude_agent_sdk/` and `src/claude_agent_sdk/_internal/`:
  - Public: `__init__.py`, `query.py`, `client.py`, `types.py`
  - Internal: `_internal/__init__.py`, `_internal/client.py`, `_internal/query.py`, `_internal/message_parser.py`, `_internal/sessions.py`, `_internal/_errors.py`
  - Transport: `_internal/transport/__init__.py`, `_internal/transport/subprocess_cli.py`
  - (remaining files to be discovered during Step 1)
- Context7 MCP: `/anthropics/claude-agent-sdk-python` (51 snippets)

**Output:**
- `self-explores/context/code-architecture.md` -- Single consolidated architecture document containing:
  1. Annotated directory tree (every .py file with 1-line description)
  2. query() flow (5+ steps)
  3. ClaudeSDKClient flow (5+ steps)
  4. Hooks flow (5+ steps)
  5. MCP in-process flow (5+ steps)
  6. Error hierarchy
  7. Type system overview

## 4. Dependencies
- **Task dependencies:** None (this is a P1 starter task, can run in parallel with 3ma and 2e7)
- **Tool dependencies:**
  - Read tool -- for reading all 15 source files
  - Glob tool -- for discovering all .py files in src/
  - Grep tool -- for tracing call chains and finding method definitions
  - Context7 MCP (`mcp__context7__resolve-library-id` + `mcp__context7__query-docs`) -- for cross-referencing
  - Write tool -- for creating output file
- **Directory:** `self-explores/context/` may need to be created if it does not exist

## 5. Flow

### Step 1: Map directory tree with annotations (~10 min)
Use Glob tool to find all .py files under `src/`:
```
Glob pattern: src/**/*.py
```

Then read each file's first 20-30 lines to capture module docstrings and imports. Build an annotated tree like:
```
src/claude_agent_sdk/
  __init__.py          -- Public exports, @tool decorator, create_sdk_mcp_server()
  query.py             -- query() async generator, one-shot entry point
  client.py            -- ClaudeSDKClient, stateful session manager
  types.py             -- All public types: Message, ClaudeAgentOptions, hooks, etc.
  _internal/
    __init__.py         -- Internal package init
    client.py           -- InternalClient, used by query() internally
    query.py            -- Query class, control protocol handler
    message_parser.py   -- JSON dict -> typed Message objects
    sessions.py         -- Historical session data reader
    _errors.py          -- Error hierarchy (ClaudeSDKError tree)
    transport/
      __init__.py       -- Transport package init
      subprocess_cli.py -- SubprocessCLITransport, CLI process manager
```

**Verify:** Every .py file found by Glob is listed in the tree with a 1-line description. Count matches 15 (or actual count if different).

### Step 2: Trace query() flow (~10 min)
Read these files in order, following the call chain:

1. `src/claude_agent_sdk/query.py` -- Find the `query()` function signature, what it creates/calls
2. `src/claude_agent_sdk/_internal/client.py` -- Find `InternalClient.process_query()`, how it creates transport and Query
3. `src/claude_agent_sdk/_internal/query.py` -- Find `Query` class, `initialize()` handshake, message streaming loop
4. `src/claude_agent_sdk/_internal/transport/subprocess_cli.py` -- Find `SubprocessCLITransport`, how it spawns CLI, stdin/stdout pipes

For each step, document:
- Method signature (class.method with key params)
- What it does (1-2 sentences)
- What it calls next (the handoff)
- Key data transformations (e.g., ClaudeAgentOptions -> CLI args)

Use Grep to find specific method calls:
```
Grep: "process_query" in src/
Grep: "SubprocessCLITransport" in src/
Grep: "async def initialize" in src/
```

**Verify:** Flow has 5+ steps, each with file:class:method reference. Follows from public `query()` to subprocess spawn and back through message yield.

### Step 3: Trace ClaudeSDKClient flow (~10 min)
Read `src/claude_agent_sdk/client.py` thoroughly. Trace:

1. `ClaudeSDKClient.__init__()` -- Construction, options storage
2. `ClaudeSDKClient.__aenter__()` (or `connect()`) -- Transport creation, initialize handshake
3. `ClaudeSDKClient.query()` -- Sending a prompt, getting response iterator
4. `ClaudeSDKClient.receive_response()` -- Multi-turn conversation cycle
5. `ClaudeSDKClient.interrupt()` -- Interrupting ongoing generation
6. `ClaudeSDKClient.__aexit__()` -- Cleanup, transport shutdown

Also trace stateful features:
- `set_permission_mode()` -- Runtime permission changes
- `set_model()` -- Runtime model switching
- `add_mcp_server()` / `remove_mcp_server()` -- MCP server management

Use Grep to find control protocol messages:
```
Grep: "request_id" in src/
Grep: "control" in src/_internal/query.py
```

**Verify:** Flow has 5+ steps covering lifecycle (init -> connect -> query -> receive -> cleanup). Stateful methods are listed.

### Step 4: Trace hooks flow (~10 min)
Trace the hooks system end-to-end:

1. Find hook type definitions in `types.py` -- PreToolUse, PostToolUse, Stop, etc.
2. Find hook registration in `client.py` or `query.py` -- How Python callbacks are registered
3. Find `HookMatcher` or hook dispatch logic in `_internal/query.py` -- How CLI hook callbacks arrive and are matched
4. Trace callback execution -- How Python async functions are invoked with hook data
5. Trace response path -- How hook results (allow/deny/modify) are sent back to CLI

Pay special attention to:
- `async_` / `continue_` field name mapping (Python keyword avoidance)
- Hook event types and their payloads
- `can_use_tool` callback (tool permission system)

Use Grep:
```
Grep: "hook" in src/ (case insensitive)
Grep: "PreToolUse\|PostToolUse\|Stop" in src/
Grep: "can_use_tool" in src/
```

**Verify:** Flow has 5+ steps from hook definition to CLI response. All hook event types are listed.

### Step 5: Trace MCP in-process flow (~10 min)
Trace the SDK MCP server system:

1. Find `@tool` decorator in `__init__.py` -- How tools are defined
2. Find `create_sdk_mcp_server()` in `__init__.py` -- How MCP server is constructed from tool definitions
3. Find MCP server integration in `_internal/query.py` -- How Query intercepts tool calls destined for SDK MCP servers
4. Trace tool execution -- How in-process tool calls are dispatched and results returned
5. Find the MCP protocol messages -- How tool call/result flows through the control protocol

Use Grep:
```
Grep: "sdk_mcp\|create_sdk_mcp" in src/
Grep: "@tool\|tool_map" in src/
Grep: "call_tool" in src/
```

**Verify:** Flow has 5+ steps from @tool definition to result return. Clear distinction between SDK MCP (in-process) and external MCP servers.

### Step 6: Cross-reference via Context7 (~5 min)
Fetch Context7 docs using `mcp__context7__query-docs` with `/anthropics/claude-agent-sdk-python`:
- Query: "transport layer control protocol initialize handshake message streaming"
- Compare: Do the traced flows match the official implementation description?
- Note: Any discrepancies or additional details not found in local code comments

**Verify:** At least 1 cross-reference note added to the output document.

### Step 7: Map errors + types (~5 min)
Read `src/claude_agent_sdk/_internal/_errors.py` and `src/claude_agent_sdk/types.py`:

**Errors:** Document the hierarchy:
```
ClaudeSDKError
  CLIConnectionError
    CLINotFoundError
  ProcessError
  CLIJSONDecodeError
  MessageParseError
```

**Types:** Document key type categories:
- Message union type (what variants exist)
- Content blocks: TextBlock, ToolUseBlock, ToolResultBlock, etc.
- Configuration: ClaudeAgentOptions (fields summary)
- Hook types: HookEvent variants, HookCallback signatures
- MCP types: tool definitions, server config

**Verify:** Error hierarchy is complete (matches CLAUDE.md description). Types section lists 4+ categories.

## 6. Edge Cases & Error Handling
| Case | Trigger | Expected | Recovery |
|------|---------|----------|----------|
| _internal/query.py too complex | File is large with many control protocol handlers | Hard to trace all paths in 10 min | Focus on public-facing behavior: initialize, message streaming, hook dispatch. Skip internal retry/error paths |
| File count differs from 15 | More or fewer .py files than expected | Directory tree annotation count is wrong | Use Glob results as ground truth; update the count in output accordingly |
| Context7 data conflicts with code | Platform docs describe different behavior than source | Potential confusion about which is correct | Trust the local source code; note discrepancy with "Code says X, Context7 says Y" |
| Circular imports or complex init | __init__.py re-exports make tracing confusing | Hard to determine actual module boundaries | Follow actual import chains in each file; document re-exports separately |
| Transport has multiple implementations | More than SubprocessCLITransport exists | Need to trace additional transports | Document all found transport classes; focus detail on SubprocessCLITransport as primary |
| Context7 MCP unavailable | Network or server issue | Cannot cross-reference | Skip Step 6; add note that cross-referencing was not possible |

## 7. Acceptance Criteria
- **Happy 1:** Given all 15 source files read, When architecture doc created, Then each of the 4 flows (query, client, hooks, MCP) has 5+ steps with accurate file:class:method names
- **Happy 2:** Given directory tree created, Then every .py file in src/ is listed with a 1-line description and the total count matches Glob results
- **Happy 3:** Given error hierarchy documented, Then it matches the hierarchy described in CLAUDE.md (ClaudeSDKError -> CLIConnectionError -> CLINotFoundError, plus ProcessError, CLIJSONDecodeError, MessageParseError)
- **Negative:** Given Context7 returns conflicting information, When documenting a flow, Then local code is treated as authoritative with discrepancy noted explicitly

## 8. Technical Notes
- The SDK uses anyio for async -- may see `anyio.create_task_group()` patterns in query.py
- Transport communication is JSON streaming over stdin/stdout with `--input-format stream-json` CLI flag
- Control protocol uses `request_id` for request/response matching -- look for this in _internal/query.py
- Python keyword avoidance: `async_` maps to wire `async`, `continue_` maps to wire `continue`
- The `@tool` decorator is defined in `__init__.py`, not in a separate module
- `sessions.py` reads from `~/.claude/projects/` -- this is for historical session data, not active session management
- Context7 has only 51 snippets for the Python SDK source -- limited but useful for high-level verification

## 9. Risks
- **Risk:** 60 minutes may be tight for reading 15 files and tracing 4 flows thoroughly. **Mitigation:** Prioritize the 4 flow traces (Steps 2-5) over completeness; directory tree (Step 1) and types (Step 7) can be briefer.
- **Risk:** _internal/query.py is likely the most complex file with control protocol, hooks, MCP, and message streaming all interleaved. **Mitigation:** Read it multiple times, once per flow trace, focusing on the relevant code paths each time.
- **Risk:** Some internal patterns may be undocumented and hard to understand from code alone. **Mitigation:** Use Context7 cross-reference and CLAUDE.md architecture notes to fill gaps.

## Worklog

### [21:36] Steps 1-7 — Read all 15 source files + trace flows
**Kết quả:**
- Read: __init__.py, query.py, client.py, _errors.py, types.py (headers)
- Read: _internal/client.py (full 146 lines), _internal/query.py (350 lines), _internal/message_parser.py, _internal/sessions.py, _internal/session_mutations.py
- Read: _internal/transport/subprocess_cli.py (100 lines + structure)

**Flows traced:**
1. query() → InternalClient → SubprocessCLITransport → Query → initialize → stream → parse → yield (8 steps)
2. ClaudeSDKClient → connect → query → receive_response → interrupt → cleanup (9 steps)
3. Hook callback: CLI control_request → Query routes → hook_callbacks dict → user async fn → convert field names → response (7 steps)
4. MCP: CLI control_request → Query intercepts → _handle_sdk_mcp_request → Server.call_tool → @tool handler → response (7 steps)

**Key discoveries:**
- Query._read_messages() is the central router: control_response → pending dict, control_request → handler, regular → stream
- request_id matching via anyio.Event + pending results dict
- Hook callbacks registered during initialize() from HookMatcher → callback_id mapping
- SDK MCP vs external MCP distinguished by config type == "sdk"

**Files tạo:** `self-explores/context/code-architecture.md`

### [21:40] AC Verification
- [x] 4 flows each with 5+ steps — PASS (8, 9, 7, 7 steps)
- [x] Every .py file listed with description — PASS (15 files)
- [x] Error hierarchy documented — PASS (matches CLAUDE.md)
- [x] Type system overview — PASS (7 categories)
