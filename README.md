# Claude Code SDK for Python

Python SDK for Claude Code. See the [Claude Code SDK documentation](https://docs.anthropic.com/en/docs/claude-code/sdk) for more information.

## Installation

```bash
pip install claude-code-sdk
```

**Prerequisites:**
- Python 3.10+
- Node.js 
- Claude Code: `npm install -g @anthropic-ai/claude-code`

## Quick Start

```python
import anyio
from claude_code_sdk import query

async def main():
    async for message in query(prompt="What is 2 + 2?"):
        print(message)

anyio.run(main)
```

## Usage

### Basic Query

```python
from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock

# Simple query
async for message in query(prompt="Hello Claude"):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(block.text)

# With options
options = ClaudeCodeOptions(
    system_prompt="You are a helpful assistant",
    max_turns=1
)

async for message in query(prompt="Tell me a joke", options=options):
    print(message)
```

### Using Tools

```python
options = ClaudeCodeOptions(
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

options = ClaudeCodeOptions(
    cwd="/path/to/project"  # or Path("/path/to/project")
)
```

## API Reference

### `query(prompt, options=None)`

Main async function for querying Claude.

**Parameters:**
- `prompt` (str): The prompt to send to Claude
- `options` (ClaudeCodeOptions): Optional configuration

**Returns:** AsyncIterator[Message] - Stream of response messages

### Types

See [src/claude_code_sdk/types.py](src/claude_code_sdk/types.py) for complete type definitions:
- `ClaudeCodeOptions` - Configuration options
- `AssistantMessage`, `UserMessage`, `SystemMessage`, `ResultMessage` - Message types
- `TextBlock`, `ToolUseBlock`, `ToolResultBlock` - Content blocks

## Error Handling

```python
from claude_code_sdk import (
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

See [src/claude_code_sdk/_errors.py](src/claude_code_sdk/_errors.py) for all error types.

## Available Tools

See the [Claude Code documentation](https://docs.anthropic.com/en/docs/claude-code/settings#tools-available-to-claude) for a complete list of available tools.

## Examples

See [examples/quick_start.py](examples/quick_start.py) for a complete working example.

## License

MIT
