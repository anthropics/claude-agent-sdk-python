# Fallback Model Implementation Summary

This document summarizes the implementation of the `fallback_model` feature in the Python SDK, which allows automatic fallback to a different model when the primary model is overloaded.

## Changes Made

### 1. Type Definition (`src/claude_agent_sdk/types.py`)

Added `fallback_model` parameter to `ClaudeAgentOptions`:

```python
@dataclass
class ClaudeAgentOptions:
    # ... existing fields ...
    model: str | None = None
    fallback_model: str | None = None  # NEW
    permission_prompt_tool_name: str | None = None
    # ... rest of fields ...
```

**Location:** Line 524

### 2. CLI Transport (`src/claude_agent_sdk/_internal/transport/subprocess_cli.py`)

Added command-line argument building for `--fallback-model`:

```python
def _build_command(self) -> list[str]:
    # ... existing code ...

    if self._options.model:
        cmd.extend(["--model", self._options.model])

    if self._options.fallback_model:
        cmd.extend(["--fallback-model", self._options.fallback_model])  # NEW

    # ... rest of command building ...
```

**Location:** Lines 128-129

### 3. Test Coverage (`tests/test_transport.py`)

Added test to verify fallback model is correctly passed to CLI:

```python
def test_build_command_with_fallback_model(self):
    """Test building CLI command with fallback_model option."""
    transport = SubprocessCLITransport(
        prompt="test",
        options=make_options(
            model="claude-opus-4-5",
            fallback_model="claude-sonnet-4-5",
        ),
    )

    cmd = transport._build_command()
    assert "--model" in cmd
    assert "claude-opus-4-5" in cmd
    assert "--fallback-model" in cmd
    assert "claude-sonnet-4-5" in cmd
```

**Location:** Lines 134-148

### 4. Example Usage (`examples/streaming_mode.py`)

Added example demonstrating fallback model usage:

```python
async def example_fallback_model():
    """Demonstrate fallback model configuration."""
    print("=== Fallback Model Example ===")
    print("Configure automatic fallback to a different model when primary is overloaded\n")

    # Configure with fallback model
    options = ClaudeAgentOptions(
        model="claude-opus-4-5",
        fallback_model="claude-sonnet-4-5",
        system_prompt="You are a helpful assistant.",
    )

    async with ClaudeSDKClient(options=options) as client:
        print("User: What is the capital of France?")
        print("(Will use Opus if available, automatically fall back to Sonnet if overloaded)")
        await client.query("What is the capital of France?")

        # Process response
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage):
                # Look for fallback messages
                if msg.subtype == "info":
                    data = msg.data
                    if "message" in data and "fallback" in data["message"].lower():
                        print(f"\n[System]: {data['message']}")
            else:
                display_message(msg)
```

**Location:** Lines 467-495

The example is also registered in the main examples dictionary at line 511.

## How It Works

1. **SDK Layer**: The Python SDK accepts the `fallback_model` parameter through `ClaudeAgentOptions`
2. **Transport Layer**: The subprocess transport passes `--fallback-model` to the Claude CLI
3. **CLI Layer**: The Claude CLI (TypeScript) handles the actual fallback logic:
   - Validates that fallback model ≠ main model
   - Detects overload/rate limit errors
   - Automatically switches to fallback model
   - Logs analytics events
   - Yields system messages about the fallback

## Usage Example

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

# Configure with fallback
options = ClaudeAgentOptions(
    model="claude-opus-4-5",           # Primary model
    fallback_model="claude-sonnet-4-5"  # Fallback if Opus is overloaded
)

async with ClaudeSDKClient(options=options) as client:
    await client.query("Your prompt here")
    async for message in client.receive_response():
        print(message)
```

## Testing

Run the new test:
```bash
python -m pytest tests/test_transport.py::TestSubprocessCLITransport::test_build_command_with_fallback_model -v
```

Run the example:
```bash
python examples/streaming_mode.py fallback_model
```

## Implementation Notes

- **No validation in SDK**: The Python SDK does **not** validate that fallback_model ≠ model. This validation happens in the Claude CLI (TypeScript layer), keeping the SDK simple.
- **Optional parameter**: `fallback_model` is completely optional. If not provided, no fallback behavior occurs.
- **Pass-through design**: The SDK simply passes the parameter through to the CLI subprocess, maintaining a clean separation of concerns.

## Related Files

- `src/claude_agent_sdk/types.py` - Type definitions
- `src/claude_agent_sdk/_internal/transport/subprocess_cli.py` - CLI command building
- `tests/test_transport.py` - Unit tests
- `examples/streaming_mode.py` - Usage examples

## TypeScript SDK Reference

This implementation mirrors the TypeScript SDK's approach:
- Parameter passing: Similar to `agentSdkTypes.ts` (Options interface)
- CLI integration: Similar to `main.tsx` (CLI argument parsing)
- Fallback logic: Handled by `query.ts` (not in SDK layer)
