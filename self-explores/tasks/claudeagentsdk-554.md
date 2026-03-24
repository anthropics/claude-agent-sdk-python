---
date: 2026-03-21
type: task-worklog
task: claudeagentsdk-554
title: "Ve Sequence Diagrams"
status: in_progress
started_at: 2026-03-22 10:00
detailed_at: 2026-03-21
detail_score: ready-for-dev
tags: [diagram, sequence, mermaid, architecture, phase-2]
---

# Ve Sequence Diagrams — Detailed Design

## 1. Objective

Create 4 Mermaid sequence diagrams that accurately depict the runtime data flow for the SDK's four core interaction patterns: query() one-shot, ClaudeSDKClient interactive session, hook callback dispatch, and MCP in-process tool call.

## 2. Scope

**In-scope:**
- Sequence diagram for `query()` one-shot flow (create transport, initialize, stream, teardown)
- Sequence diagram for `ClaudeSDKClient` interactive session (connect, multi-turn, interrupt, disconnect)
- Sequence diagram for hook callback dispatch (CLI request, matcher routing, Python handler, response)
- Sequence diagram for MCP in-process tool call (CLI tool request, Query intercept, SDK MCP server, handler execution)
- Mermaid syntax validated for GitHub rendering
- Participant names matching actual class names in source code

**Out-of-scope:**
- Class diagrams or component diagrams (separate tasks)
- External MCP server communication (subprocess IPC, not in-process)
- Agent orchestration flows (too complex for single sequence diagram)
- Error/retry flows (note in diagram as alt blocks only)
- Interactive diagram tooling (Mermaid text only)

## 3. Input / Output

**Input:**
- `self-explores/context/code-architecture.md` (from task d0g)
- Source code files for verification:
  - `src/claude_agent_sdk/query.py` (query entry point)
  - `src/claude_agent_sdk/client.py` (ClaudeSDKClient)
  - `src/claude_agent_sdk/_internal/query.py` (Query control protocol handler)
  - `src/claude_agent_sdk/_internal/transport/subprocess_cli.py` (SubprocessCLITransport)
  - `src/claude_agent_sdk/_internal/message_parser.py` (message parsing)

**Output:**
- `self-explores/tasks/claudeagentsdk-554-diagrams.md` — single file containing all 4 Mermaid sequence diagrams with explanatory text between each

## 4. Dependencies

- `claudeagentsdk-d0g` (code architecture analysis) — MUST be completed first; provides the architectural understanding needed for accurate diagrams
- Tool dependencies: Mermaid syntax knowledge, GitHub Markdown renderer for validation

## 5. Flow

### Step 1: Sequence Diagram 1 — query() One-Shot Flow (~10 min)

Draw the complete lifecycle of a `query()` call from user invocation to final message yield.

**Participants (left to right):**
- `UserApp` — caller code
- `query()` — public API function in `query.py`
- `InternalClient` — `_internal/client.py`, orchestrates the query lifecycle
- `Query` — `_internal/query.py`, control protocol handler
- `Transport` — `SubprocessCLITransport`, manages CLI subprocess
- `CLI` — Claude Code CLI subprocess

**Interactions:**
1. UserApp calls `query(prompt, options)`
2. query() creates `InternalClient`
3. InternalClient creates `SubprocessCLITransport`
4. Transport starts CLI subprocess with `--input-format stream-json`
5. Transport sends initialize request via stdin
6. CLI responds with initialize result via stdout
7. Query sends user prompt as control request
8. CLI streams JSON messages (assistant text, tool use, etc.)
9. Transport reads stdout line-by-line, parses JSON
10. `MessageParser` converts raw dicts to typed `Message` objects
11. Messages yielded to UserApp via `AsyncIterator[Message]`
12. On stream end, Transport kills subprocess
13. InternalClient tears down all resources

Use `activate`/`deactivate` to show lifetimes. Add `note` for "JSON streaming over stdin/stdout".

**Verify:** Render Mermaid in a Markdown previewer or paste into github.com Mermaid live editor. All participants should appear, arrows should be sequential, no syntax errors.

### Step 2: Sequence Diagram 2 — ClaudeSDKClient Interactive Session (~10 min)

Draw a multi-turn conversation session showing the bidirectional nature.

**Participants:**
- `UserApp`
- `ClaudeSDKClient` — public API in `client.py`
- `Transport` — `SubprocessCLITransport`
- `Query` — `_internal/query.py`
- `CLI` — Claude Code CLI

**Interactions:**
1. UserApp enters `async with ClaudeSDKClient(options) as client:`
2. Client creates Transport, starts subprocess
3. Client sends initialize request
4. CLI returns initialize result (capabilities, session info)
5. UserApp calls `client.query("first prompt")`
6. Query sends prompt to CLI, CLI processes, streams response
7. UserApp receives response via `receive_response()`
8. UserApp calls `client.query("follow-up")` (multi-turn)
9. CLI continues conversation with context
10. UserApp calls `client.interrupt()` mid-stream
11. Query sends interrupt control request
12. CLI acknowledges interrupt, stops generation
13. `async with` block exits, Client disconnects, Transport kills subprocess

Use `opt` block for "Multi-turn follow-up" and `break` for interrupt flow.

**Verify:** Check that the `async with` context manager entry/exit is clearly shown. Verify interrupt flow matches actual `client.py` implementation.

### Step 3: Sequence Diagram 3 — Hook Callback Dispatch (~15 min)

This is the most complex diagram. Show how the CLI initiates a hook callback and the SDK dispatches it to user-defined Python functions.

**Participants:**
- `CLI` — initiates the hook request
- `Query` — `_internal/query.py`, receives and routes hook callbacks
- `HookMatcher` — matching logic within Query
- `UserHookFn` — user-provided async Python function

**Interactions:**
1. CLI encounters a hook point (e.g., PreToolUse for Bash)
2. CLI sends hook callback request via stdout (JSON with `request_id`, hook type, tool info)
3. Query receives the hook callback request from Transport
4. Query identifies hook type (PreToolUse, PostToolUse, Stop, etc.)
5. Query iterates registered HookMatchers for this hook type
6. HookMatcher checks if tool name / pattern matches
7. `alt` block: Match found vs. No match
8. If match: Query calls UserHookFn with hook event data
9. UserHookFn executes async logic (approve, reject, modify)
10. UserHookFn returns decision (e.g., `{"decision": "approve"}` or `{"decision": "block", "reason": "..."}`)
11. Query wraps decision in control response with matching `request_id`
12. Query sends response back to CLI via stdin
13. CLI processes decision: continues (approve) or blocks tool use (reject)

Add `note` explaining: "Field names: async_ and continue_ in Python, converted to async/continue on wire". Add `alt` block for approve vs. block decisions.

**Verify:** Cross-reference with `_internal/query.py` to confirm the hook dispatch mechanism. Verify that `request_id` matching is shown. Check that the async nature of the callback is clear.

### Step 4: Sequence Diagram 4 — MCP In-Process Tool Call (~10 min)

Show how SDK MCP servers handle tool calls entirely in-process without subprocess IPC.

**Participants:**
- `CLI` — Claude decides to use a tool
- `Query` — intercepts SDK MCP tool calls
- `MCPServer` — in-process MCP server created via `create_sdk_mcp_server()`
- `ToolHandler` — user-defined function decorated with `@tool`

**Interactions:**
1. Claude (via CLI) decides to call a tool (e.g., `get_weather`)
2. CLI sends tool call request via stdout (tool name, arguments, request_id)
3. Query receives the tool call request
4. Query checks: is this an SDK MCP server tool? (vs. external MCP)
5. `alt` block: SDK MCP tool vs. External MCP
6. If SDK MCP: Query routes to the matching MCPServer instance
7. MCPServer looks up the tool by name in its registry
8. MCPServer calls `call_tool()` which invokes the `@tool`-decorated ToolHandler
9. ToolHandler executes (may be async), returns result
10. MCPServer packages result as MCP tool response
11. Query wraps result in control response with matching `request_id`
12. Query sends response to CLI via stdin
13. CLI receives tool result, continues generation

Add `note` explaining: "SDK MCP tools run in the Python process, not as separate subprocesses".

**Verify:** Confirm the interception logic in `_internal/query.py`. Verify that `create_sdk_mcp_server()` and `@tool` decorator flow is accurate.

### Step 5: Review Consistency and Polish (~10 min)

- Verify all 4 diagrams use consistent participant naming (same class = same name across diagrams)
- Verify Mermaid syntax: proper `sequenceDiagram` declaration, correct arrow types (`->>` for async, `-->>` for response)
- Ensure each diagram has a title using `title:` or `%%` comment
- Add a summary table at the top of the output file listing all 4 diagrams with one-line descriptions
- Test each diagram renders without errors (paste into Mermaid live editor or GitHub preview)

**Verify:** All 4 diagrams render. Participant names are consistent. Each diagram has 5+ interactions. No Mermaid syntax errors.

## 6. Edge Cases & Error Handling

| Case | Trigger | Expected | Recovery |
|------|---------|----------|----------|
| Mermaid syntax error | Typo in arrow or participant | Diagram won't render | Test each diagram individually in Mermaid live editor before committing |
| Participants don't match code | Architecture doc outdated or wrong class names | Diagram misleads reader | Cross-reference participant names against actual class names in source code |
| Hook flow too complex | Multiple matchers, nested alt blocks | Diagram unreadable | Split into "happy path" main diagram + "detailed alt" sub-diagram if needed |
| MCP flow conflates external vs SDK | Mixing in-process and subprocess MCP | Confusion about where execution happens | Clearly label "in-process" in notes; keep external MCP out of scope |
| Long interaction chains | >15 arrows in one diagram | Renders too tall, hard to follow | Group related interactions with `rect` blocks; add `note` summaries |

## 7. Acceptance Criteria

- **Happy 1:** Given code-architecture.md and source code verification, When 4 sequence diagrams are created, Then each has accurate participants matching real class names, 5+ interactions per diagram, and renders correctly on GitHub Markdown
- **Happy 2:** Given the completed diagrams, When a developer reads them without looking at source code, Then they can accurately describe the data flow for query(), ClaudeSDKClient, hooks, and MCP tool calls
- **Negative:** Given a Mermaid syntax error in a diagram, When the diagram is tested, Then the error is caught and fixed before the file is finalized

## 8. Technical Notes

- Mermaid sequence diagram syntax reference: `sequenceDiagram`, `participant`, `->>` (solid arrow, async call), `-->>` (dashed arrow, response), `activate`/`deactivate`, `alt`/`else`/`end`, `opt`/`end`, `note right of`/`note over`
- GitHub renders Mermaid natively in `.md` files inside ` ```mermaid ` code blocks
- Arrow convention: `->>` for calls/requests, `-->>` for returns/responses, `--)` for async fire-and-forget
- Keep participant aliases short for readability (e.g., `participant Q as Query`)
- Max recommended diagram width: 6 participants (beyond that, consider splitting)
- Python field name conversions to note: `async_` -> `async`, `continue_` -> `continue` (wire format)

## 9. Risks

- **Risk:** Architecture doc from task d0g may have gaps or inaccuracies that propagate into diagrams. **Mitigation:** Cross-reference every participant and interaction against actual source code, not just the architecture doc.
- **Risk:** Mermaid rendering differences between GitHub and local previewer. **Mitigation:** Test final diagrams on GitHub directly (or use github.com's Mermaid support in issue/PR previews).
- **Risk:** Hook callback flow is complex enough that a single diagram becomes unreadable. **Mitigation:** Use Mermaid's `alt`/`opt` blocks judiciously; if still too complex, create a simplified "happy path" version and a detailed version.

## Worklog

### [10:00] Bat dau — Doc source code
- Doc 5 source files: query.py, client.py, _internal/query.py, _internal/client.py, subprocess_cli.py
- Xac nhan flow chinh xac tu code thuc te (khong chi dua vao architecture doc)

### [10:15] Hoan thanh — 4 Sequence Diagrams
**Ket qua:**
- Tao file `claudeagentsdk-554-diagrams.md` voi 4 Mermaid sequence diagrams
- Moi diagram co >= 5 interactions, participant names match class names trong code

**Diagrams da ve:**
1. **query() One-Shot Flow** — 6 participants, full lifecycle tu UserApp → InternalClient → Transport → Query → CLI
2. **ClaudeSDKClient Interactive Session** — multi-turn + interrupt flow, 3 rect blocks (Turn 1, Turn 2, Interrupt)
3. **Hook Callback Dispatch** — CLI-initiated callback, registration + trigger phases, alt block cho approve/block
4. **SDK MCP In-Process Tool Call** — JSONRPC routing, init + tool call phases, alt block cho server found/not found

**Key findings tu source code:**
- Transport LUON dung `--input-format stream-json` (subprocess_cli.py:331)
- String prompt: user message gui sau initialize, roi goi wait_for_result_and_end_input()
- Hook callbacks: callback_id generated sequentially ("hook_0", "hook_1"...), stored in dict
- MCP: manual JSONRPC routing vi Python MCP SDK thieu Transport abstraction
- Field conversion: async_ → async, continue_ → continue (query.py:34-50)

**Files tao:**
- self-explores/tasks/claudeagentsdk-554-diagrams.md
