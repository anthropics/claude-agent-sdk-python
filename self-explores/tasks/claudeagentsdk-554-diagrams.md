# Sequence Diagrams — Claude Agent SDK Python

| # | Diagram | Description | Participants |
|---|---------|-------------|--------------|
| 1 | query() One-Shot Flow | Complete lifecycle of a stateless one-shot query | UserApp, query(), InternalClient, Transport, Query, CLI |
| 2 | ClaudeSDKClient Interactive Session | Multi-turn bidirectional conversation with interrupt | UserApp, ClaudeSDKClient, Transport, Query, CLI |
| 3 | Hook Callback Dispatch | CLI-initiated hook callback routed to Python handler | CLI, Transport, Query, UserHookFn |
| 4 | SDK MCP In-Process Tool Call | In-process MCP tool execution via @tool decorator | CLI, Transport, Query, MCPServer, ToolHandler |

---

## 1. query() One-Shot Flow

Shows the complete lifecycle when a user calls `query(prompt="...", options=...)`. The SDK creates an `InternalClient`, which sets up transport and `Query`, performs the initialize handshake, sends the prompt, streams responses, and tears everything down.

```mermaid
sequenceDiagram
    title query() One-Shot Flow

    participant App as UserApp
    participant QF as query()
    participant IC as InternalClient
    participant T as SubprocessCLITransport
    participant Q as Query
    participant CLI as Claude CLI

    App->>QF: query(prompt, options)
    QF->>IC: InternalClient()
    QF->>IC: process_query(prompt, options)

    IC->>T: SubprocessCLITransport(prompt, options)
    IC->>T: connect()
    activate T
    T->>CLI: spawn subprocess<br/>--output-format stream-json<br/>--input-format stream-json
    activate CLI

    IC->>Q: Query(transport, is_streaming_mode=True)
    IC->>Q: start()
    Note over Q: Creates TaskGroup,<br/>starts _read_messages() task

    IC->>Q: initialize()
    Q->>T: write(control_request: initialize)
    T->>CLI: stdin: {"type":"control_request", "request":{"subtype":"initialize", ...}}
    CLI-->>T: stdout: {"type":"control_response", "response":{...capabilities...}}
    T-->>Q: control_response (initialize result)
    Q-->>IC: initialization complete

    Note over IC: String prompt path

    IC->>T: write(user_message JSON)
    T->>CLI: stdin: {"type":"user", "message":{"role":"user","content":"..."}}

    IC->>Q: wait_for_result_and_end_input()
    Note over Q: If hooks/MCP servers exist,<br/>waits for first result event

    loop Streaming Response
        CLI-->>T: stdout: JSON message (assistant, tool_use, etc.)
        T-->>Q: _read_messages() parses JSON
        Q-->>Q: routes to message stream
    end

    Q->>T: end_input()
    T->>CLI: close stdin

    CLI-->>T: stdout: {"type":"result", ...}
    T-->>Q: result message
    Q-->>Q: _first_result_event.set()

    loop Yield Messages
        IC->>Q: receive_messages()
        Q-->>IC: raw message dict
        IC->>IC: parse_message(data) → typed Message
        IC-->>QF: yield Message
        QF-->>App: yield Message
    end

    Note over Q: Stream ends with {"type":"end"}

    IC->>Q: close()
    Q->>Q: cancel TaskGroup
    Q->>T: close()
    T->>CLI: terminate()
    deactivate CLI
    deactivate T
```

**Key points from source code:**
- `query()` in `query.py:11` creates `InternalClient` and delegates to `process_query()`
- `InternalClient.process_query()` in `_internal/client.py:44` orchestrates the full lifecycle
- Transport always uses `--input-format stream-json` (line 331 in subprocess_cli.py)
- For string prompts, the user message is written to stdin after initialize (client.py:126-133)
- `wait_for_result_and_end_input()` keeps stdin open if hooks/MCP servers need bidirectional communication

---

## 2. ClaudeSDKClient Interactive Session

Shows a multi-turn conversation session using `ClaudeSDKClient` as an async context manager, including follow-up messages and interrupt capability.

```mermaid
sequenceDiagram
    title ClaudeSDKClient Interactive Session

    participant App as UserApp
    participant C as ClaudeSDKClient
    participant T as SubprocessCLITransport
    participant Q as Query
    participant CLI as Claude CLI

    App->>C: async with ClaudeSDKClient(options) as client:
    C->>C: __aenter__() → connect()

    C->>T: SubprocessCLITransport(prompt, options)
    C->>T: connect()
    activate T
    T->>CLI: spawn subprocess
    activate CLI

    C->>Q: Query(transport, is_streaming_mode=True,<br/>can_use_tool, hooks, sdk_mcp_servers)
    C->>Q: start()
    Note over Q: TaskGroup created,<br/>_read_messages() running

    C->>Q: initialize()
    Q->>T: write(initialize request)
    T->>CLI: stdin: control_request (initialize)
    CLI-->>T: stdout: control_response (capabilities)
    T-->>Q: initialize result
    Q-->>C: connected

    rect rgb(230, 245, 255)
        Note over App,CLI: Turn 1: First Query
        App->>C: client.query("first prompt")
        C->>T: write(user_message JSON)
        T->>CLI: stdin: {"type":"user", ...}

        CLI-->>T: stdout: streaming messages
        T-->>Q: parsed messages → stream

        App->>C: async for msg in client.receive_response()
        loop Until ResultMessage
            C->>Q: receive_messages()
            Q-->>C: message dict
            C->>C: parse_message() → Message
            C-->>App: yield Message
        end
        Note over App: ResultMessage received → iterator stops
    end

    rect rgb(230, 255, 230)
        Note over App,CLI: Turn 2: Follow-up (multi-turn)
        App->>C: client.query("follow-up question")
        C->>T: write(user_message JSON)
        T->>CLI: stdin: follow-up message
        Note over CLI: Continues with<br/>conversation context

        CLI-->>T: stdout: streaming response
        T-->>Q: parsed messages

        App->>C: async for msg in client.receive_response()
        loop Until ResultMessage
            C->>Q: receive_messages()
            Q-->>C: message dict
            C-->>App: yield Message
        end
    end

    rect rgb(255, 240, 230)
        Note over App,CLI: Interrupt Flow
        App->>C: client.query("long task...")
        C->>T: write(user_message)
        T->>CLI: stdin: user message

        CLI-->>T: stdout: partial response...
        App->>C: client.interrupt()
        C->>Q: interrupt()
        Q->>T: write(control_request: interrupt)
        T->>CLI: stdin: {"type":"control_request",<br/>"request":{"subtype":"interrupt"}}
        CLI-->>T: stdout: control_response (ack)
        T-->>Q: interrupt acknowledged
        CLI-->>T: stdout: result (interrupted)
    end

    App->>C: exit async with block
    C->>C: __aexit__() → disconnect()
    C->>Q: close()
    Q->>Q: cancel TaskGroup
    Q->>T: close()
    T->>CLI: terminate()
    deactivate CLI
    deactivate T
```

**Key points from source code:**
- `ClaudeSDKClient.__aenter__()` calls `connect()` with no prompt → uses empty async generator (client.py:102-107)
- `client.query()` in `client.py:197` writes user messages directly to transport via JSON
- `receive_response()` in `client.py:442` wraps `receive_messages()` and stops after `ResultMessage`
- `interrupt()` sends a control request with `subtype: "interrupt"` (query.py:536-538)
- `__aexit__()` always calls `disconnect()` → `query.close()` → `transport.close()`

---

## 3. Hook Callback Dispatch

Shows how the CLI initiates a hook callback (e.g., PreToolUse) and how the SDK's `Query` class dispatches it to user-defined Python async functions. This is the most complex flow because the CLI is the initiator.

```mermaid
sequenceDiagram
    title Hook Callback Dispatch

    participant CLI as Claude CLI
    participant T as SubprocessCLITransport
    participant Q as Query
    participant Fn as UserHookFn

    Note over Q: During initialize(),<br/>hooks registered with callback_ids:<br/>hook_callbacks[id] = user_function

    rect rgb(245, 245, 255)
        Note over CLI,Fn: Registration Phase (during initialize)
        Q->>T: write(initialize request with hooks config)
        T->>CLI: stdin: {"hooks": {"PreToolUse": [{"matcher": {...},<br/>"hookCallbackIds": ["hook_0"]}]}}
        CLI-->>T: stdout: initialize response
        T-->>Q: initialized
    end

    rect rgb(255, 245, 235)
        Note over CLI,Fn: Hook Trigger Phase
        Note over CLI: CLI encounters hook point<br/>(e.g., PreToolUse for Bash tool)
        CLI->>T: stdout: {"type":"control_request",<br/>"request_id":"req_1",<br/>"request":{"subtype":"hook_callback",<br/>"callback_id":"hook_0",<br/>"input":{tool_name, tool_input},<br/>"tool_use_id":"toolu_xxx"}}
        T->>Q: _read_messages() receives control_request

        Q->>Q: identifies type == "control_request"
        Q->>Q: spawns _handle_control_request() in TaskGroup

        Q->>Q: subtype == "hook_callback"
        Q->>Q: lookup hook_callbacks["hook_0"]

        Q->>Fn: await callback(input, tool_use_id, context)
        activate Fn
        Note over Fn: User async function executes<br/>(approve, reject, or modify)
        Fn-->>Q: return {"decision": "approve"}<br/>or {"decision": "block", "reason": "..."}
        deactivate Fn

        Q->>Q: _convert_hook_output_for_cli()<br/>async_ → async, continue_ → continue

        Q->>T: write(control_response)
        T->>CLI: stdin: {"type":"control_response",<br/>"response":{"subtype":"success",<br/>"request_id":"req_1",<br/>"response":{"decision":"approve"}}}

        alt Decision: approve
            Note over CLI: CLI continues tool execution
        else Decision: block
            Note over CLI: CLI blocks tool use,<br/>reports reason to model
        end
    end
```

**Key points from source code:**
- Hook registration happens in `Query.initialize()` (query.py:119-163): each hook gets a unique `callback_id` mapped to the Python function
- CLI sends hook callbacks as `control_request` with `subtype: "hook_callback"` (query.py:288)
- `_handle_control_request()` at query.py:236 dispatches based on subtype
- Python field names `async_` and `continue_` are converted to `async`/`continue` for the wire format by `_convert_hook_output_for_cli()` (query.py:34-50)
- Response matching uses `request_id` to correlate requests and responses

---

## 4. SDK MCP In-Process Tool Call

Shows how SDK MCP servers (defined via `@tool` decorator and `create_sdk_mcp_server()`) handle tool calls entirely in-process. Unlike external MCP servers that run as subprocesses, SDK MCP tools execute within the Python process.

```mermaid
sequenceDiagram
    title SDK MCP In-Process Tool Call

    participant CLI as Claude CLI
    participant T as SubprocessCLITransport
    participant Q as Query
    participant MCP as MCPServer<br/>(in-process)
    participant TH as @tool Handler

    Note over Q,MCP: SDK MCP servers extracted from options<br/>during connect/process_query:<br/>sdk_mcp_servers[name] = McpServer instance

    rect rgb(240, 248, 255)
        Note over CLI,TH: MCP Initialization (during SDK init)
        CLI->>T: stdout: control_request (mcp_message:<br/>{"method":"initialize"})
        T->>Q: _read_messages() → control_request
        Q->>Q: _handle_control_request()
        Q->>Q: subtype == "mcp_message"
        Q->>Q: _handle_sdk_mcp_request(server_name, message)
        Q-->>T: write(control_response with MCP init result)
        T-->>CLI: stdin: {capabilities: {tools: {}},<br/>serverInfo: {name, version}}

        CLI->>T: stdout: mcp_message (tools/list)
        T->>Q: route to _handle_sdk_mcp_request
        Q->>MCP: handler = request_handlers[ListToolsRequest]
        MCP-->>Q: list of tool definitions
        Q-->>T: write(JSONRPC response with tools)
        T-->>CLI: stdin: {"result":{"tools":[...]}}
    end

    rect rgb(255, 248, 240)
        Note over CLI,TH: Tool Call Execution
        Note over CLI: Claude decides to call tool "get_weather"
        CLI->>T: stdout: {"type":"control_request",<br/>"request_id":"req_5",<br/>"request":{"subtype":"mcp_message",<br/>"server_name":"my-server",<br/>"message":{"method":"tools/call",<br/>"params":{"name":"get_weather",<br/>"arguments":{"city":"Paris"}}}}}

        T->>Q: _read_messages() → control_request
        Q->>Q: _handle_control_request()
        Q->>Q: subtype == "mcp_message"
        Q->>Q: _handle_sdk_mcp_request("my-server", message)

        Q->>Q: Check: server_name in sdk_mcp_servers?

        alt Server found
            Q->>MCP: handler = request_handlers[CallToolRequest]
            activate MCP
            MCP->>TH: call_tool("get_weather", {"city":"Paris"})
            activate TH
            Note over TH: @tool decorated function<br/>executes (may be async)
            TH-->>MCP: return result content
            deactivate TH
            MCP-->>Q: CallToolResult(content=[TextContent(...)])
            deactivate MCP

            Q->>Q: Convert result to JSONRPC response
            Q->>T: write(control_response)
            T->>CLI: stdin: {"type":"control_response",<br/>"response":{"subtype":"success",<br/>"request_id":"req_5",<br/>"response":{"mcp_response":<br/>{"result":{"content":[{"type":"text",<br/>"text":"Sunny, 22C"}]}}}}}

            Note over CLI: CLI receives tool result,<br/>continues generation
        else Server not found
            Q->>T: write(JSONRPC error: server not found)
            T->>CLI: stdin: error response
        end
    end
```

**Key points from source code:**
- SDK MCP servers are extracted from `options.mcp_servers` where `type == "sdk"` (client.py:143-147)
- `_handle_sdk_mcp_request()` at query.py:394 manually routes JSONRPC methods since Python MCP SDK lacks Transport abstraction
- Supported methods: `initialize`, `tools/list`, `tools/call`, `notifications/initialized` (query.py:431-514)
- Tool calls go through `server.request_handlers[CallToolRequest]` which invokes the `@tool`-decorated handler
- The `instance` field is stripped from SDK server config before passing to CLI (subprocess_cli.py:246-250)
- All communication is in-process — no subprocess IPC for SDK MCP tools
