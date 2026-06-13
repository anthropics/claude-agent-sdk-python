# Claude Agent SDK for Python

Python SDK for building AI agents powered by Claude. This SDK enables developers to programmatically interact with Claude Code and create custom tools, hooks, and conversational agents.

## Tech Stack

- **Language**: Python 3.10+
- **Core Dependencies**:
  - anyio (async runtime)
  - mcp (Model Context Protocol)
  - typing_extensions (Python 3.10 compatibility)
- **Dev Tools**: pytest, mypy, ruff, pytest-asyncio
- **Build System**: hatchling

## Project Structure

```
src/claude_agent_sdk/
├── __init__.py           # Public API exports
├── client.py             # ClaudeSDKClient for interactive conversations
├── query.py              # Simple query() function
├── types.py              # Type definitions and data models
├── _errors.py            # Exception classes
├── _version.py           # Package version
└── _cli_version.py       # Bundled CLI version

tests/                    # Test suite
examples/                 # Usage examples
scripts/                  # Build and development scripts
```

## Development Commands

```bash
# Run tests
python -m pytest tests/

# Run linter and formatter
python -m ruff check src/ tests/ --fix
python -m ruff format src/ tests/

# Type checking
python -m mypy src/

# Build wheel
python scripts/build_wheel.py

# Initial setup (git hooks)
./scripts/initial-setup.sh
```

## Coding Conventions

### Python Style
- Follow PEP 8 standards
- Use type hints for all function signatures
- Line length: 88 characters (Black/Ruff default)
- Use pathlib for file operations (PTH rules)
- Prefer f-strings for string formatting

### Type Checking
- Strict mypy configuration enabled
- All functions must have type annotations
- No implicit Optional types
- Disallow untyped defs and decorators

### Imports
- Group imports: stdlib, third-party, first-party
- Use absolute imports from `claude_agent_sdk`
- Sort imports alphabetically (handled by ruff)

### Testing
- Use pytest with pytest-asyncio
- Async tests supported via `asyncio_mode = "auto"`
- Test files mirror source structure
- Aim for comprehensive test coverage

### Code Quality
- Ruff linting enforces:
  - pycodestyle (E, W)
  - pyflakes (F)
  - isort (I)
  - pep8-naming (N)
  - pyupgrade (UP)
  - flake8-bugbear (B)
  - flake8-comprehensions (C4)
  - flake8-use-pathlib (PTH)
  - flake8-simplify (SIM)

## Key Components

### query() Function
Simple async function for one-off queries to Claude. Returns AsyncIterator of messages.

```python
async for message in query(prompt="Hello"):
    print(message)
```

### ClaudeSDKClient
Advanced client supporting:
- Bidirectional conversations
- Custom tools (in-process MCP servers)
- Hooks for automation
- Session management

### Custom Tools
Define tools as Python functions using the `@tool` decorator. Tools run in-process as SDK MCP servers.

### Hooks
Python functions invoked at specific points:
- SessionStart
- PreToolUse
- PostToolUse
- Stop

## Important Notes

### Security
- Never commit API keys or secrets
- Use environment variables for sensitive data
- SDK MCP servers run in-process (security consideration)

### CLI Bundling
- Claude Code CLI is bundled with the package
- Each platform gets a platform-specific wheel
- Build script handles CLI download and bundling

### Async Programming
- All SDK functions are async
- Compatible with anyio (asyncio and trio backends)
- Use `async with` for client context management

### Error Handling
- Base error: `ClaudeSDKError`
- Specific errors: `CLINotFoundError`, `ProcessError`, etc.
- Always handle process failures gracefully

### Version Management
- Package version in `pyproject.toml` and `_version.py`
- CLI version tracked separately in `_cli_version.py`
- CHANGELOG.md documents all changes

## Git Workflow

- Pre-push hook runs linting checks
- Skip with `git push --no-verify` if needed
- All checks must pass before merging
- Follow conventional commit messages

## Testing Strategy

- Unit tests for core functionality
- Integration tests with actual CLI
- Async test support via pytest-asyncio
- Mock external dependencies when appropriate

## Release Process

1. Trigger GitHub Actions workflow with version numbers
2. Platform-specific wheels built automatically
3. Release PR created with version updates
4. Review and merge to main

## Common Patterns

### Error Recovery
Always catch specific exceptions and provide helpful error messages:

```python
try:
    async for msg in query(prompt="..."):
        pass
except CLINotFoundError:
    print("Install Claude Code first")
except ProcessError as e:
    print(f"Failed: {e.exit_code}")
```

### Resource Management
Use context managers for proper cleanup:

```python
async with ClaudeSDKClient(options=options) as client:
    await client.query("...")
    # Client closed automatically
```

### Tool Definition
Keep tools focused and well-documented:

```python
@tool("tool_name", "Clear description", {"arg": type})
async def my_tool(args):
    # Process args
    return {"content": [...]}
```
