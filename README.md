# Claude Agent SDK for Python

Python SDK for Claude Agent. See the [Claude Agent SDK documentation](https://platform.claude.com/docs/en/agent-sdk/python) for more information.

## Installation

```bash
pip install claude-agent-sdk
```

**Prerequisites:**

- Python 3.10+

### How the SDK finds Claude Code

The SDK is always CLI-backed: your Python code talks to Claude Code by starting a Claude Code CLI process under the hood.

Resolution order:

1. `ClaudeAgentOptions(cli_path="/path/to/claude")`
2. A bundled Claude Code binary, if the installed package includes one
3. A system `claude` binary on your `PATH` or common install locations

Published wheels can bundle the CLI for you. Source checkouts and editable installs usually do not, so install Claude Code separately if needed:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

The SDK warns if the resolved CLI is older than the minimum supported version (`2.0.0`).

## Quick Start Matrix

| If you are... | Use... | Why |
| --- | --- | --- |
| Running a synchronous script entry point | `anyio.run(main)` + `query()` | Keep top-level script code sync while using the async SDK |
| Already inside an async app or service | `async for ... in query(...)` or `ClaudeSDKClient` | Reuse your existing event loop and stream responses directly |
| Pinning a specific Claude Code binary | `ClaudeAgentOptions(cli_path="...")` | Make CLI-backed runs reproducible in CI, Docker, or staged rollouts |

## Quick Start

```python
import anyio
from claude_agent_sdk import AssistantMessage, TextBlock, query

async def main():
    async for message in query(prompt="What is 2 + 2? Reply with just the number."):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")

anyio.run(main)
```

Expected output:

```text
Claude: 4
```

## Basic Usage: query()

`query()` is an async generator for querying Claude Code. It returns an `AsyncIterator` of response messages. See [src/claude_agent_sdk/query.py](src/claude_agent_sdk/query.py).

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

# Simple query
async for message in query(prompt="Reply with exactly: ready"):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")

# With options
options = ClaudeAgentOptions(
    system_prompt="Reply with exactly one lower-case word.",
    max_turns=1
)

async for message in query(prompt="Return only the word: configured", options=options):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
```

Expected output:

```text
Claude: ready
Claude: configured
```

### Using Tools

By default, Claude has access to the full [Claude Code toolset](https://code.claude.com/docs/en/settings#tools-available-to-claude) (Read, Write, Edit, Bash, and others). `allowed_tools` is a permission allowlist: listed tools are auto-approved, and unlisted tools fall through to `permission_mode` and `can_use_tool` for a decision. It does not remove tools from Claude's toolset. To block specific tools, use `disallowed_tools`. See the [permissions guide](https://platform.claude.com/docs/en/agent-sdk/permissions) for the full evaluation order.

```python
options = ClaudeAgentOptions(
    allowed_tools=["Read", "Write", "Bash"],  # auto-approve these tools
    permission_mode='acceptEdits'  # auto-accept file edits
)

async for message in query(
    prompt="Create a hello.py file",
    options=options
):
    # Process tool use and results
    pass
```

### Working Directory

```python
from pathlib import Path

options = ClaudeAgentOptions(
    cwd="/path/to/project"  # or Path("/path/to/project")
)
```

### Pinned CLI-Backed Flow

Use `cli_path` when you want to force the SDK to use a specific Claude Code build instead of the bundled or system binary.

```python
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

options = ClaudeAgentOptions(
    cli_path="/usr/local/bin/claude",
    max_turns=1,
)

async for message in query(
    prompt="Reply with exactly: pinned",
    options=options,
):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
```

## ClaudeSDKClient

`ClaudeSDKClient` supports bidirectional, interactive conversations with Claude
Code. See [src/claude_agent_sdk/client.py](src/claude_agent_sdk/client.py).

Unlike `query()`, `ClaudeSDKClient` additionally enables **custom tools** and **hooks**, both of which can be defined as Python functions.

### Custom Tools (as In-Process SDK MCP Servers)

A **custom tool** is a Python function that you can offer to Claude, for Claude to invoke as needed.

Custom tools are implemented in-process MCP servers that run directly within your Python application, eliminating the need for separate processes that regular MCP servers require.

For an end-to-end example, see [MCP Calculator](examples/mcp_calculator.py).

#### Creating a Simple Tool

```python
from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient

# Define a tool using the @tool decorator
@tool("greet", "Greet a user", {"name": str})
async def greet_user(args):
    return {
        "content": [
            {"type": "text", "text": f"Hello, {args['name']}!"}
        ]
    }

# Create an SDK MCP server
server = create_sdk_mcp_server(
    name="my-tools",
    version="1.0.0",
    tools=[greet_user]
)

# Use it with Claude. allowed_tools pre-approves the tool so it runs
# without a permission prompt; it does not control tool availability.
options = ClaudeAgentOptions(
    mcp_servers={"tools": server},
    allowed_tools=["mcp__tools__greet"]
)

async with ClaudeSDKClient(options=options) as client:
    await client.query("Greet Alice")

    # Extract and print response
    async for msg in client.receive_response():
        print(msg)
```

#### Benefits Over External MCP Servers

- **No subprocess management** - Runs in the same process as your application
- **Better performance** - No IPC overhead for tool calls
- **Simpler deployment** - Single Python process instead of multiple
- **Easier debugging** - All code runs in the same process
- **Type safety** - Direct Python function calls with type hints

#### Migration from External Servers

```python
# BEFORE: External MCP server (separate process)
options = ClaudeAgentOptions(
    mcp_servers={
        "calculator": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "calculator_server"]
        }
    }
)

# AFTER: SDK MCP server (in-process)
from my_tools import add, subtract  # Your tool functions

calculator = create_sdk_mcp_server(
    name="calculator",
    tools=[add, subtract]
)

options = ClaudeAgentOptions(
    mcp_servers={"calculator": calculator}
)
```

#### Mixed Server Support

You can use both SDK and external MCP servers together:

```python
options = ClaudeAgentOptions(
    mcp_servers={
        "internal": sdk_server,      # In-process SDK server
        "external": {                # External subprocess server
            "type": "stdio",
            "command": "external-server"
        }
    }
)
```

#### MCP Integration Examples Index

| Example | What it shows |
| --- | --- |
| [examples/mcp_calculator.py](examples/mcp_calculator.py) | Minimal in-process SDK MCP server with `@tool`, `create_sdk_mcp_server()`, and `allowed_tools` |
| Migration snippet above | How to replace an external stdio MCP server with an in-process SDK server |
| [src/claude_agent_sdk/client.py](src/claude_agent_sdk/client.py) | Runtime MCP controls such as `get_mcp_status()`, `reconnect_mcp_server()`, and `toggle_mcp_server()` |

### Hooks

A **hook** is a Python function that the Claude Code _application_ (_not_ Claude) invokes at specific points of the Claude agent loop. Hooks can provide deterministic processing and automated feedback for Claude. Read more in [Intercept and control agent behavior with hooks](https://platform.claude.com/docs/en/agent-sdk/hooks).

For more examples, see examples/hooks.py.

#### Example

```python
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

async def check_bash_command(input_data, tool_use_id, context):
    tool_name = input_data["tool_name"]
    tool_input = input_data["tool_input"]
    if tool_name != "Bash":
        return {}
    command = tool_input.get("command", "")
    block_patterns = ["foo.sh"]
    for pattern in block_patterns:
        if pattern in command:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Command contains invalid pattern: {pattern}",
                }
            }
    return {}

options = ClaudeAgentOptions(
    allowed_tools=["Bash"],
    hooks={
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[check_bash_command]),
        ],
    }
)

async with ClaudeSDKClient(options=options) as client:
    # Test 1: Command with forbidden pattern (will be blocked)
    await client.query("Run the bash command: ./foo.sh --help")
    async for msg in client.receive_response():
        print(msg)

    print("\n" + "=" * 50 + "\n")

    # Test 2: Safe command that should work
    await client.query("Run the bash command: echo 'Hello from hooks example!'")
    async for msg in client.receive_response():
        print(msg)
```

## Types

See [src/claude_agent_sdk/types.py](src/claude_agent_sdk/types.py) for complete type definitions:

- `ClaudeAgentOptions` - Configuration options
- `AssistantMessage`, `UserMessage`, `SystemMessage`, `ResultMessage` - Message types
- `TextBlock`, `ToolUseBlock`, `ToolResultBlock` - Content blocks

## Error Handling

```python
from claude_agent_sdk import (
    ClaudeSDKError,      # Base error
    CLINotFoundError,    # Claude Code not installed
    CLIConnectionError,  # Connection issues
    ProcessError,        # Process failed
    CLIJSONDecodeError,  # JSON parsing issues
)

try:
    async for message in query(prompt="Hello"):
        pass
except CLINotFoundError:
    print("Please install Claude Code")
except ProcessError as e:
    print(f"Process failed with exit code: {e.exit_code}")
except CLIJSONDecodeError as e:
    print(f"Failed to parse response: {e}")
```

See [src/claude_agent_sdk/\_errors.py](src/claude_agent_sdk/_errors.py) for all error types.

## Troubleshooting

### Auth and CLI startup

- `CLINotFoundError` means the SDK could not resolve a Claude Code binary. Install Claude Code or set `ClaudeAgentOptions(cli_path=...)`.
- The SDK reuses Claude Code authentication. If requests fail before the first model response, run `claude login` for the CLI binary you expect the SDK to use.
- Source installs and editable installs typically rely on your system CLI. Bundled CLI behavior is most relevant for published wheels.

### Tool execution

- `allowed_tools` only auto-approves tools. It does not shrink Claude's toolset. Use `tools=[]` or `disallowed_tools=[...]` when you want stricter control.
- `can_use_tool` requires streaming mode. Use an `AsyncIterable` prompt with `query()` or switch to `ClaudeSDKClient` when you need callback-driven tool permission handling.
- If a tool still prompts unexpectedly, confirm the exact tool name matches what Claude emits (for MCP tools this is usually `mcp__server__tool`).

### MCP servers

- Call `await client.get_mcp_status()` to inspect whether a server is `connected`, `pending`, `failed`, `needs-auth`, or `disabled`.
- For `failed` or `needs-auth` servers, fix the underlying server configuration or login state, then call `await client.reconnect_mcp_server("server-name")`.

## Available Tools

See the [Claude Code documentation](https://code.claude.com/docs/en/settings#tools-available-to-claude) for a complete list of available tools.

## Examples

See [examples/quick_start.py](examples/quick_start.py) for a complete working example.

See [examples/streaming_mode.py](examples/streaming_mode.py) for comprehensive examples involving `ClaudeSDKClient`. You can even run interactive examples in IPython from [examples/streaming_mode_ipython.py](examples/streaming_mode_ipython.py).

See [examples/mcp_calculator.py](examples/mcp_calculator.py) for a minimal MCP integration example and [examples/tool_permission_callback.py](examples/tool_permission_callback.py) for callback-based tool control.

## Migrating from Claude Code SDK

If you're upgrading from the Claude Code SDK (versions < 0.1.0), please see the [CHANGELOG.md](CHANGELOG.md#010) for details on breaking changes and new features, including:

- `ClaudeCodeOptions` → `ClaudeAgentOptions` rename
- Merged system prompt configuration
- Settings isolation and explicit control
- New programmatic subagents and session forking features

## Development

If you're contributing to this project, run the initial setup script to install git hooks:

```bash
./scripts/initial-setup.sh
```

This installs a pre-push hook that runs lint checks before pushing, matching the CI workflow. To skip the hook temporarily, use `git push --no-verify`.

### Building Wheels Locally

To build wheels with the bundled Claude Code CLI:

```bash
# Install build dependencies
pip install build twine

# Build wheel with bundled CLI
python scripts/build_wheel.py

# Build with specific version
python scripts/build_wheel.py --version 0.1.4

# Build with specific CLI version
python scripts/build_wheel.py --cli-version 2.0.0

# Clean bundled CLI after building
python scripts/build_wheel.py --clean

# Skip CLI download (use existing)
python scripts/build_wheel.py --skip-download
```

The build script:

1. Downloads Claude Code CLI for your platform
2. Bundles it in the wheel
3. Builds both wheel and source distribution
4. Checks the package with twine

See `python scripts/build_wheel.py --help` for all options.

### Release Workflow

The package is published to PyPI via the GitHub Actions workflow in `.github/workflows/publish.yml`. To create a new release:

1. **Trigger the workflow** manually from the Actions tab with two inputs:
   - `version`: The package version to publish (e.g., `0.1.5`)
   - `claude_code_version`: The Claude Code CLI version to bundle (e.g., `2.0.0` or `latest`)

2. **The workflow will**:
   - Build platform-specific wheels for macOS, Linux, and Windows
   - Bundle the specified Claude Code CLI version in each wheel
   - Build a source distribution
   - Publish all artifacts to PyPI
   - Create a release branch with version updates
   - Open a PR to main with:
     - Updated `pyproject.toml` version
     - Updated `src/claude_agent_sdk/_version.py`
     - Updated `src/claude_agent_sdk/_cli_version.py` with bundled CLI version
     - Auto-generated `CHANGELOG.md` entry

3. **Review and merge** the release PR to update main with the new version information

The workflow tracks both the package version and the bundled CLI version separately, allowing you to release a new package version with an updated CLI without code changes.

## License and terms

Use of this SDK is governed by Anthropic's [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms), including when you use it to power products and services that you make available to your own customers and end users, except to the extent a specific component or dependency is covered by a different license as indicated in that component's LICENSE file.
