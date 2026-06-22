# Contributing

Thanks for your interest in contributing to the Claude Agent SDK for Python!

## Development Setup

1. **Clone and install with dev dependencies:**

   ```bash
   git clone https://github.com/anthropics/claude-agent-sdk-python.git
   cd claude-agent-sdk-python
   pip install -e ".[dev]"
   ```

2. **Install git hooks (optional but recommended):**

   ```bash
   ./scripts/initial-setup.sh
   ```

   This installs a pre-push hook that runs lint checks before pushing, matching the CI workflow. To skip the hook temporarily, use `git push --no-verify`.

## Linting and Type Checking

```bash
# Lint
python -m ruff check src/ tests/

# Format check (without modifying)
python -m ruff format --check src/ tests/

# Format (auto-fix)
python -m ruff format src/ tests/

# Type check (strict mode)
python -m mypy src/
```

## Running Tests

```bash
# All tests
python -m pytest tests/

# Specific test file
python -m pytest tests/test_client.py

# With verbose output
python -m pytest tests/ -v
```

## CI

The CI workflow (`.github/workflows/lint.yml` and `.github/workflows/test.yml`) runs:
- `ruff check` and `ruff format --check` on `src/` and `tests/`
- `mypy src/` in strict mode
- `pytest tests/` on Python 3.13 across ubuntu, macOS, and Windows

All checks must pass before merging.

## Pull Request Process

1. Fork the repo and create a branch from `main`.
2. Make your changes with clear commit messages.
3. Ensure all lint, typecheck, and test commands pass locally.
4. Open a PR with a clear description of what changed and why.
5. Link any relevant issues with `Fixes #NNN`.

## Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/) (enforced by `ruff`).
- Type annotations are required (`mypy --strict`).
- Line length is 88 characters (enforced by `ruff format`).
