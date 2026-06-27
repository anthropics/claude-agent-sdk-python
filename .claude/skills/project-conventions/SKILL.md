---
name: project-conventions
description: Enforces Claude Agent SDK Python project conventions including type safety, async patterns, PEP 8 compliance, and SDK-specific best practices. Use when writing or reviewing code.
allowed-tools: Read, Grep, Glob
---

# Claude Agent SDK Project Conventions

This skill ensures all code follows the strict conventions of the Claude Agent SDK Python project.

## Quick Reference

**Language**: Python 3.10+
**Style**: PEP 8, enforced by Ruff
**Type Checking**: Strict mypy
**Testing**: pytest with pytest-asyncio
**Line Length**: 88 characters

## Type Hints (MANDATORY)

### All Functions Must Have Complete Type Hints

```python
# ✅ CORRECT
async def query(
    prompt: str,
    options: Optional[ClaudeAgentOptions] = None
) -> AsyncIterator[Message]:
    ...

# ❌ WRONG - Missing return type
async def query(prompt: str, options: Optional[ClaudeAgentOptions] = None):
    ...

# ❌ WRONG - Missing parameter types
async def query(prompt, options=None) -> AsyncIterator[Message]:
    ...
```

### Generic Types Must Be Parameterized

```python
# ✅ CORRECT
from typing import List, Dict, Optional

def get_messages() -> List[Message]:
    ...

def get_config() -> Dict[str, Any]:
    ...

# ❌ WRONG - Unparameterized generics
def get_messages() -> list:  # Should be List[Message]
    ...

def get_config() -> dict:  # Should be Dict[str, Any]
    ...
```

### No Implicit Optional

```python
# ✅ CORRECT
from typing import Optional

def process(value: Optional[str] = None) -> Optional[str]:
    if value is None:
        return None
    return value.upper()

# ❌ WRONG - Implicit Optional
def process(value: str = None) -> str:  # mypy will complain
    ...
```

### Use typing_extensions for Python 3.10

```python
# ✅ CORRECT - Compatible with Python 3.10
from typing import List, Dict, Optional
from typing_extensions import TypeAlias

MessageList: TypeAlias = List[Message]

# ✅ ALSO CORRECT - Python 3.10+ syntax
def process() -> list[str]:  # Built-in generics work in 3.10+
    ...

# But prefer typing.List for consistency
```

## Async Patterns (CRITICAL)

### Always Await Async Functions

```python
# ✅ CORRECT
async def caller():
    result = await async_function()
    return result

# ❌ WRONG - Returns coroutine, not result
async def caller():
    result = async_function()  # Forgot await!
    return result
```

### Use Async Context Managers

```python
# ✅ CORRECT
async with ClaudeSDKClient(options=options) as client:
    await client.query("test")
    # Automatically cleaned up

# ❌ WRONG - Not using context manager
client = ClaudeSDKClient(options=options)
await client.query("test")
# client not properly closed!
```

### No Blocking Calls in Async Functions

```python
# ✅ CORRECT - Using async I/O
async def read_file(path: Path) -> str:
    async with await anyio.open_file(path) as f:
        return await f.read()

# ❌ WRONG - Blocking call blocks event loop
async def read_file(path: Path) -> str:
    with open(path) as f:  # BLOCKING!
        return f.read()
```

### Properly Handle AsyncIterator

```python
# ✅ CORRECT - Consuming async iterator
async def process_messages():
    async for message in query("test"):
        handle_message(message)

# ✅ ALSO CORRECT - Collecting results
messages = []
async for message in query("test"):
    messages.append(message)

# ❌ WRONG - Not consuming iterator
iterator = query("test")  # Never consumed!
```

## Import Organization

### Order: stdlib, third-party, first-party

```python
# ✅ CORRECT
# Standard library
import json
import logging
from pathlib import Path
from typing import List, Optional

# Third-party
import anyio
from mcp import Server

# First-party
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk._errors import CLINotFoundError
from claude_agent_sdk.types import Message

# ❌ WRONG - Mixed order
from claude_agent_sdk import ClaudeSDKClient
import json
from mcp import Server
import logging
```

### Use Absolute Imports

```python
# ✅ CORRECT
from claude_agent_sdk.types import Message
from claude_agent_sdk._errors import ClaudeSDKError

# ❌ WRONG - Relative imports from src
from .types import Message
from ._errors import ClaudeSDKError
```

## Naming Conventions

### Follow Python Standards

```python
# ✅ CORRECT
class ClaudeSDKClient:  # PascalCase for classes
    pass

async def query_claude() -> None:  # snake_case for functions
    pass

MAX_RETRIES = 3  # UPPER_CASE for constants

_internal_function()  # Leading underscore for private

# ❌ WRONG
class claude_sdk_client:  # Should be PascalCase
    pass

def QueryClaude():  # Should be snake_case
    pass

max_retries = 3  # Constant should be UPPER_CASE
```

## File Operations

### Use pathlib, Not os.path

```python
# ✅ CORRECT
from pathlib import Path

config_file = Path.home() / ".config" / "claude" / "config.json"
if config_file.exists():
    content = config_file.read_text()

# ❌ WRONG - Using os.path
import os

config_file = os.path.join(os.path.expanduser("~"), ".config", "claude", "config.json")
if os.path.exists(config_file):
    with open(config_file) as f:
        content = f.read()
```

## String Formatting

### Use f-strings

```python
# ✅ CORRECT
name = "Claude"
message = f"Hello, {name}!"

# ✅ CORRECT - Multi-line
message = (
    f"Hello, {name}! "
    f"Welcome to {project}."
)

# ❌ WRONG - Old-style formatting
message = "Hello, %s!" % name
message = "Hello, {}!".format(name)

# ❌ WRONG - String concatenation
message = "Hello, " + name + "!"
```

## Error Handling

### Use Specific Exceptions

```python
# ✅ CORRECT
from claude_agent_sdk._errors import CLINotFoundError, ProcessError

try:
    result = await query("test")
except CLINotFoundError:
    print("Please install Claude Code")
except ProcessError as e:
    print(f"Process failed: {e.exit_code}")

# ❌ WRONG - Catching too broad
try:
    result = await query("test")
except Exception:  # Too broad!
    print("Something went wrong")
```

### Create Custom Exceptions

```python
# ✅ CORRECT - Inherit from ClaudeSDKError
class InvalidToolError(ClaudeSDKError):
    """Raised when tool configuration is invalid."""
    pass

# ❌ WRONG - Not inheriting from SDK base
class InvalidToolError(Exception):
    pass
```

## Code Style

### Keep Functions Focused

```python
# ✅ CORRECT - Single responsibility
async def send_message(message: str) -> None:
    await _validate_message(message)
    await _format_message(message)
    await _transmit_message(message)

# ❌ WRONG - Too many responsibilities
async def send_message(message: str) -> None:
    # 100 lines of validation, formatting, transmission, logging, error handling...
```

### No Code Duplication

```python
# ✅ CORRECT - Extract common logic
def _parse_timestamp(data: dict) -> datetime:
    return datetime.fromisoformat(data["timestamp"])

def parse_message(data: dict) -> Message:
    timestamp = _parse_timestamp(data)
    ...

def parse_event(data: dict) -> Event:
    timestamp = _parse_timestamp(data)
    ...

# ❌ WRONG - Duplicated parsing
def parse_message(data: dict) -> Message:
    timestamp = datetime.fromisoformat(data["timestamp"])
    ...

def parse_event(data: dict) -> Event:
    timestamp = datetime.fromisoformat(data["timestamp"])  # Duplicate!
    ...
```

### Use Comprehensions

```python
# ✅ CORRECT
messages = [msg for msg in all_messages if msg.type == "assistant"]
ids = {msg.id for msg in messages}

# ❌ WRONG - Verbose loop
messages = []
for msg in all_messages:
    if msg.type == "assistant":
        messages.append(msg)
```

## Documentation

### Docstrings for Public APIs

```python
# ✅ CORRECT
async def query(
    prompt: str,
    options: Optional[ClaudeAgentOptions] = None
) -> AsyncIterator[Message]:
    """Query Claude with a prompt and receive streaming responses.

    Args:
        prompt: The user's prompt to send to Claude
        options: Optional configuration for the query

    Yields:
        Message objects from Claude's response stream

    Raises:
        CLINotFoundError: If Claude Code CLI is not installed
        ProcessError: If the CLI process fails

    Example:
        >>> async for message in query("Hello"):
        ...     print(message)
    """
    ...
```

### Comments Explain "Why", Not "What"

```python
# ✅ CORRECT
# Use exponential backoff to avoid overwhelming the API during outages
await anyio.sleep(2 ** retry_count)

# ❌ WRONG - Obvious from code
# Sleep for 2 seconds
await anyio.sleep(2)
```

## Testing Requirements

### All New Code Needs Tests

```python
# For every new function in src/claude_agent_sdk/foo.py
# Create tests in tests/test_foo.py

@pytest.mark.asyncio
async def test_new_feature():
    """Test the new feature works correctly."""
    result = await new_feature()
    assert result == expected
```

### Use pytest-asyncio for Async Tests

```python
# ✅ CORRECT
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result

# ❌ WRONG - Missing decorator
async def test_async_function():  # Won't run as async!
    result = await async_function()
    assert result
```

## Security

### Never Commit Secrets

```python
# ✅ CORRECT
import os
api_key = os.environ.get("ANTHROPIC_API_KEY")

# ❌ WRONG
api_key = "sk-ant-1234567890"  # NEVER COMMIT!
```

### Validate User Input

```python
# ✅ CORRECT
def set_max_turns(value: int) -> None:
    if value < 1:
        raise ValueError("max_turns must be at least 1")
    if value > 100:
        raise ValueError("max_turns must not exceed 100")
    self.max_turns = value

# ❌ WRONG - No validation
def set_max_turns(value: int) -> None:
    self.max_turns = value  # What if negative or huge?
```

## CLI Command Examples

### Running Code Quality Checks

```bash
# Format code
python -m ruff format src/ tests/

# Lint and auto-fix
python -m ruff check src/ tests/ --fix

# Type check
python -m mypy src/

# Run tests
python -m pytest tests/

# Run tests with coverage
python -m pytest tests/ --cov=claude_agent_sdk --cov-report=term-missing
```

## Common Mistakes to Avoid

### ❌ Mistake: Using `Any` unnecessarily
```python
# Wrong
def process(data: Any) -> Any:
    ...

# Better
def process(data: Dict[str, str]) -> List[Message]:
    ...
```

### ❌ Mistake: Not using context managers
```python
# Wrong
client = ClaudeSDKClient()
result = await client.query("test")

# Correct
async with ClaudeSDKClient() as client:
    result = await client.query("test")
```

### ❌ Mistake: Catching exceptions too broadly
```python
# Wrong
try:
    result = await query("test")
except:  # Catches everything, even KeyboardInterrupt!
    pass

# Correct
try:
    result = await query("test")
except CLINotFoundError:
    handle_cli_not_found()
except ProcessError as e:
    handle_process_error(e)
```

### ❌ Mistake: Not awaiting coroutines
```python
# Wrong
async def caller():
    result = async_function()  # Returns coroutine!
    return result

# Correct
async def caller():
    result = await async_function()
    return result
```

## Pre-commit Checklist

Before committing code:
- [ ] All type hints present
- [ ] No mypy errors: `python -m mypy src/`
- [ ] No ruff errors: `python -m ruff check src/ tests/`
- [ ] Code formatted: `python -m ruff format src/ tests/`
- [ ] Tests pass: `python -m pytest tests/`
- [ ] New code has tests
- [ ] Docstrings for public APIs
- [ ] No secrets in code

## Quick Fix Commands

```bash
# Fix most issues automatically
python -m ruff check src/ tests/ --fix
python -m ruff format src/ tests/

# Check types
python -m mypy src/

# Run tests
python -m pytest tests/ -v
```

## Remember

- **Type safety is non-negotiable** - strict mypy must pass
- **Async patterns are critical** - always await, no blocking calls
- **Tests are required** - no untested code
- **PEP 8 compliance** - ruff enforces this
- **Security first** - validate input, no secrets

When in doubt, look at existing code in the project for examples!
