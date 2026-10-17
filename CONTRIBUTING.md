# Contributing

Thanks for your interest in contributing to the Claude Agent SDK for Python.

## Setup

Install the package in editable mode with development dependencies:

```bash
pip install -e ".[dev]"
```

Optionally install the pre-push git hook (runs the same lint checks as CI):

```bash
./scripts/initial-setup.sh
```

To skip the hook temporarily, use `git push --no-verify`.

## Lint, typecheck, and test

These commands match `.github/workflows/lint.yml` and `.github/workflows/test.yml`:

```bash
# Lint and auto-fix
python -m ruff check src/ tests/ scripts/ --fix
python -m ruff format src/ tests/ scripts/

# Typecheck
python -m mypy src/ scripts/

# Tests
python -m pytest tests/
```

CI also runs `ruff format --check` (without rewriting) and may collect coverage via `python -m pytest tests/ -v --cov=claude_agent_sdk`.

## Pull request titles

Prefer [Conventional Commits](https://www.conventionalcommits.org/) style titles, for example:

- `fix: ...`
- `feat: ...`
- `docs: ...`
- `chore: ...`
