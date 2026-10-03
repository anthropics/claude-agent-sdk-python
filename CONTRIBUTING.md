# Contributing to Claude Agent SDK for Python

Thank you for your interest in contributing to the Claude Agent SDK for Python!

## Development Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/anthropics/claude-agent-sdk-python.git
   cd claude-agent-sdk-python
   ```

2. Create and activate a virtual environment (recommended):

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   # or .venv\Scripts\activate  # Windows
   ```

3. Install the package in editable mode with development dependencies:

   ```bash
   pip install -e ".[dev]"
   ```

## Lint and Format

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting, and [mypy](https://mypy-lang.org/) for static type checking.

```bash
# Run linter
ruff check src/ tests/

# Run linter with auto-fix
ruff check src/ tests/ --fix

# Check formatting
ruff format --check src/ tests/

# Auto-format code
ruff format src/ tests/

# Run type checker
mypy src/
```

## Testing

Tests are written with [pytest](https://docs.pytest.org/) and use `pytest-asyncio` with `asyncio_mode = "auto"`.

```bash
# Run all tests
python -m pytest tests/

# Run tests with coverage
python -m pytest tests/ --cov=claude_agent_sdk --cov-report=xml

# Run a specific test file
python -m pytest tests/test_client.py
```

## Pull Request Guidelines

### Commit Messages and PR Titles

This project follows [Conventional Commits](https://www.conventionalcommits.org/). PR titles and squash-merged commit messages should use one of the following prefixes:

- `feat:` — a new feature
- `fix:` — a bug fix
- `docs:` — documentation changes
- `chore:` — maintenance, dependency bumps, version bumps, etc.
- `refactor:` — code restructuring without feature or fix changes
- `perf:` — performance improvements
- `test:` — adding or updating tests
- `ci:` — CI configuration changes

Example: `docs: add CONTRIBUTING.md with dev setup instructions`

### Before Submitting

1. Ensure all lint checks pass (`ruff check`, `ruff format --check`, `mypy`).
2. Ensure all tests pass (`python -m pytest tests/`).
3. Write a clear PR description explaining the motivation and approach.

## Contributor License Agreement

By submitting a pull request, you agree to the terms of the [Anthropic Contributor License Agreement](https://cla-assistant.io/anthropics/claude-agent-sdk-python). You will be prompted to sign the CLA via the CLA Assistant bot when you open your first pull request.
