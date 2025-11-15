# Custom Claude CLI Wrappers

This directory contains custom Claude CLI wrapper scripts that extend the official CLI with additional features via HTTP interception.

## claude-with-structured-outputs

A drop-in replacement for the Claude CLI that adds structured outputs support by injecting the beta header and JSON schema into API requests.

### How It Works

1. Acts as a wrapper around the real `claude` CLI binary
2. Uses Node.js `--require` flag to load the HTTP interceptor before the CLI starts
3. Interceptor monkey-patches `global.fetch` to inject:
   - Beta header: `anthropic-beta: structured-outputs-2025-11-13`
   - Output format with JSON schema in request body

### Usage with SDK

```python
from claude_agent_sdk import query, ClaudeAgentOptions
from pydantic import BaseModel

class MySchema(BaseModel):
    name: str
    value: int

# Point to the custom CLI wrapper
options = ClaudeAgentOptions(
    cli_path="/path/to/claude-with-structured-outputs"
)

# The wrapper automatically enables structured outputs
async for message in query(
    prompt="Extract data...",
    output_format=MySchema,  # Schema gets passed via environment
    options=options
):
    print(message)
```

### Requirements

- **Node.js**: >= 18.0.0 (for `global.fetch` support)
- **Claude CLI**: Installed via `npm install -g @anthropic-ai/claude-code`
- **API Key**: Set `ANTHROPIC_API_KEY` environment variable (OAuth not supported by beta)

### Environment Variables

The wrapper reads these environment variables set by the SDK:

- `ANTHROPIC_SCHEMA_FILE`: Path to JSON schema file
- `ANTHROPIC_SCHEMA`: Inline JSON schema as string
- `INTERCEPT_DEBUG`: Enable verbose debug logging (1 or true)

### What This Proves

This wrapper demonstrates that:

✅ Structured outputs work perfectly at the Anthropic API level
✅ The SDK's schema generation and conversion is correct
✅ Full end-to-end integration works right now
✅ Only blocker is official CLI support for passing schemas

Once the Claude Code CLI adds native schema support (tracked in anthropics/claude-code#9058), you can remove the `cli_path` option and use the SDK's `output_format` parameter directly.

### Example Output

```bash
$ export ANTHROPIC_API_KEY="sk-ant-api03-..."
$ python examples/structured_outputs_with_wrapper.py

Response:
----------------------------------------------------------------------
{
  "name": "Sarah Chen",
  "email": "sarah@company.com",
  "plan_interest": "Professional plan",
  "demo_requested": true
}

✓ Validation Success!
```

### See Also

- `../TESTING.md` - HTTP interception testing documentation
- `../VALIDATION_RESULTS.md` - Detailed validation report
- `../examples/structured_outputs_with_wrapper.py` - Full SDK integration example
- `../intercept-claude.js` - HTTP interceptor implementation
