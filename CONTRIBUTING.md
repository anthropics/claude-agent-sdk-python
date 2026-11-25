# Contributing to claude-agent-sdk-python

Thank you for your interest in contributing! This guide covers how to set up
your development environment and the workflow for submitting changes.

## Development Setup

**Requirements:** Python 3.10+

Clone the repository and install the package with its dev dependencies:

```bash
git clone https://github.com/anthropics/claude-agent-sdk-python.git
cd claude-agent-sdk-python
pip install -e ".[dev]"
```

If you prefer [uv](https://github.com/astral-sh/uv):

```bash
uv pip install -e ".[dev]"
```

The `dev` extra installs: `pytest`, `pytest-cov`, `anyio[trio]`, `mypy`, and `ruff`.

## Running Tests

```bash
python -m pytest tests/
```

Run with coverage:

```bash
python -m pytest tests/ --cov=src/claude_agent_sdk
```

## Lint and Style

Check for issues and auto-fix where possible:

```bash
python -m ruff check src/ tests/ --fix
python -m ruff format src/ tests/
```

## Type Checking

```bash
python -m mypy src/
```

Mypy is configured in `pyproject.toml` with strict mode enabled.

## Submitting a Pull Request

1. Fork the repository and create a branch from `main`:
   ```bash
   git checkout main -b fix/your-short-description
   ```
2. Make your changes. Keep each PR focused on a single fix or feature.
3. Ensure tests pass, lint is clean, and mypy reports no errors.
4. Write a clear commit message that describes *what* changed and *why*.
5. Open a pull request against `anthropics/claude-agent-sdk-python:main`.
   Fill in the PR description with context on the problem being solved.

Please keep PRs small and focused — one fix or feature per PR makes review
faster and merges cleaner.
