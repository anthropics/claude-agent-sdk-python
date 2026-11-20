# Explicit memory tools with Loom

This example exposes two SDK-native in-process MCP tools: `remember` and
`recall`. The application binds the Loom namespace and user before creating
the tools. Model arguments cannot select another user or namespace.

The tools call the public `Agent.tools(...).execute(...)` interface from
[`clickhouse-loom`](https://pypi.org/project/clickhouse-loom/). They return its
write receipts and retrieval metadata as text. A write can be refused or
produce a conflict; a completed tool call alone does not prove a fact was
stored. Retrieved facts are data, not instructions.

This is selective shared memory, separate from Claude's conversation
transcripts. It does not implement `SessionStore`, the Claude API built-in
memory tool, or an external MCP endpoint. It does not record prompts or run
memory writes from hooks.

## Setup

This example requires Python 3.11 or newer. Install the SDK and the pinned
Loom client in a virtual environment:

```sh
pip install claude-agent-sdk clickhouse-loom==1.3.0rc1
```

Use a Loom deployment and a token scoped to the namespace and operations this
application needs (`memory.set` and `memory.search`). Its authorization rules
still apply. Configure `LOOM_BASE_URL`, `LOOM_API_KEY`, `LOOM_ORG`,
`LOOM_NAMESPACE` and a named `LOOM_USER_ID`. Use the Claude Agent SDK's normal
Claude authentication separately.

Run from this repository only when you intend to call Claude and Loom:

```sh
python examples/loom_memory.py --live --prompt 'Remember that I prefer concise answers.'
python examples/loom_memory.py --live --prompt 'What answer style do I prefer?'
```

Keep the same application identity to recall its stored facts. `session_id`
is fixed to `-` so these selective facts can be recalled across conversation
sessions. The application owns the Loom agent and closes it after the Claude
client. Built-in Claude tools and filesystem settings sources are disabled.
Strict MCP configuration excludes ambient project, user and plugin MCP servers;
only the two configured tools are pre-approved. The example does not make a
production security or retention guarantee.

## Offline checks

The focused `loom_memory` cases in `tests/test_sdk_mcp_integration.py` drive
real SDK MCP initialization, tool listing and calls with a fake Loom tools
boundary. They require no model credentials, running CLI, network or database:

```sh
python -m pytest tests/test_sdk_mcp_integration.py -k loom_memory
```

These checks cover SDK dispatch and the example's scope/schema/receipt
behavior. They do not demonstrate live Loom persistence or a Claude model
conversation. The optional live commands above must be validated separately.

A separate hosted optional-example check installs the pinned Loom client,
strictly typechecks the example and runs
`examples/tests/test_loom_memory_client.py`. Those tests traverse the real
Loom client and real SDK MCP dispatch with mocked HTTP responses; they do
not call a model, server or database. The default SDK test environment does
not install Loom.
