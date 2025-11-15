# Claude Agent SDK for Python

Python SDK for Claude Agent. See the [Claude Agent SDK documentation](https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-python) for more information.

## Installation

```bash
pip install claude-agent-sdk
```

**Prerequisites:**
- Python 3.10+
- Node.js
- Claude Code 2.0.0+: `npm install -g @anthropic-ai/claude-code`

## Quick Start

```python
import anyio
from claude_agent_sdk import query

async def main():
    async for message in query(prompt="What is 2 + 2?"):
        print(message)

anyio.run(main)
```

## Basic Usage: query()

`query()` is an async function for querying Claude Code. It returns an `AsyncIterator` of response messages. See [src/claude_agent_sdk/query.py](src/claude_agent_sdk/query.py).

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

# Simple query
async for message in query(prompt="Hello Claude"):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(block.text)

# With options
options = ClaudeAgentOptions(
    system_prompt="You are a helpful assistant",
    max_turns=1
)

async for message in query(prompt="Tell me a joke", options=options):
    print(message)
```

### Using Tools

```python
options = ClaudeAgentOptions(
    allowed_tools=["Read", "Write", "Bash"],
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

### Structured Outputs

> **⚠️ NOT YET FUNCTIONAL**: This feature requires CLI support (see [anthropics/claude-code#9058](https://github.com/anthropics/claude-code/issues/9058)). The API and utilities are ready, but structured outputs won't work until the CLI implements schema passing.

Structured outputs allow you to get responses from Claude in a specific JSON schema format, ensuring type-safe and predictable responses.

#### Using Raw JSON Schema

```python
from claude_agent_sdk import query

# Define your expected output schema
schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "plan_interest": {"type": "string"},
        "demo_requested": {"type": "boolean"}
    },
    "required": ["name", "email", "plan_interest", "demo_requested"]
}

async for message in query(
    prompt="Extract info: John Smith (john@example.com) wants Enterprise plan demo",
    output_format=schema
):
    print(message)
```

#### Using Pydantic Models (Recommended)

```python
from pydantic import BaseModel, Field
from claude_agent_sdk import query

class EmailExtraction(BaseModel):
    """Structured data from an email."""
    name: str = Field(description="Full name")
    email: str = Field(description="Email address")
    plan_interest: str = Field(description="Plan they're interested in")
    demo_requested: bool = Field(description="Whether they requested a demo")

async for message in query(
    prompt="Extract info: Sarah (sarah@company.com) wants Professional plan demo",
    output_format=EmailExtraction  # Pass the Pydantic model class
):
    print(message)
```

For more examples, see [examples/structured_outputs.py](examples/structured_outputs.py).

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

# Use it with Claude
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

### Hooks

A **hook** is a Python function that the Claude Code *application* (*not* Claude) invokes at specific points of the Claude agent loop. Hooks can provide deterministic processing and automated feedback for Claude. Read more in [Claude Code Hooks Reference](https://docs.anthropic.com/en/docs/claude-code/hooks).

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

See [src/claude_agent_sdk/_errors.py](src/claude_agent_sdk/_errors.py) for all error types.

## Available Tools

See the [Claude Code documentation](https://docs.anthropic.com/en/docs/claude-code/settings#tools-available-to-claude) for a complete list of available tools.

## Examples

See [examples/quick_start.py](examples/quick_start.py) for a complete working example.

See [examples/streaming_mode.py](examples/streaming_mode.py) for comprehensive examples involving `ClaudeSDKClient`. You can even run interactive examples in IPython from [examples/streaming_mode_ipython.py](examples/streaming_mode_ipython.py).

See [examples/structured_outputs.py](examples/structured_outputs.py) for structured outputs examples using both Pydantic models and raw JSON schemas.

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

## License

MIT
