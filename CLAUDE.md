# Workflow

```bash
# Lint and style
# Check for issues and fix automatically
python -m ruff check src/ test/ --fix
python -m ruff format src/ test/

# Typecheck (only done for src/)
python -m mypy src/

# Run all tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_client.py
```

# Codebase Structure

- `src/claude_code_sdk/` - Main package
  - `client.py` - ClaudeSDKClient for interactive sessions
  - `query.py` - One-shot query function
  - `types.py` - Type definitions
  - `_internal/` - Internal implementation details
    - `transport/subprocess_cli.py` - CLI subprocess management
    - `message_parser.py` - Message parsing logic
