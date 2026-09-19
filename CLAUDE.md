# Workflow

```bash
# Lint and style
# Check for issues and fix automatically
python -m ruff check src/ tests/ scripts/ --fix
python -m ruff format src/ tests/ scripts/

# Typecheck
python -m mypy src/ scripts/

# Run all tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_client.py
```

# Codebase Structure

- `src/claude_agent_sdk/` - Main package
  - `client.py` - ClaudeSDKClient for interactive sessions
  - `query.py` - One-shot query function
  - `types.py` - Type definitions
  - `_internal/` - Internal implementation details
    - `transport/subprocess_cli.py` - CLI subprocess management
    - `message_parser.py` - Message parsing logic
    - `executable.py` - Safe executable resolution (`find_executable` / `run`); the only way SDK code may locate or spawn a helper program

# Spawning External Programs

Never pass a bare program name (`"git"`, `"claude"`) to `subprocess`, `anyio.open_process` or any other process API, and never use `shutil.which`: on Windows both search the current working directory, so a binary planted in the directory the application runs from would be executed (CWE-427). Resolve the program with `claude_agent_sdk._internal.executable.find_executable` / `require_executable`, or run it with `executable.run([...])`, and hand the OS the absolute path. See that module's docstring for the guarantees (G1-G5, D1). CI enforces this: ruff bans `shutil.which` (`TID251` via the `banned-api` table in `pyproject.toml`) and `tests/test_executable_invariant.py` fails on any spawn call under `src/` that names its program by a bare or relative literal.
