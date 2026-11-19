# Contributing to claude-agent-sdk

## Development Setup

1. Clone the repo and install with dev dependencies:

```bash
pip install -e ".[dev]"
```

2. Install the pre-push git hook (runs lint checks before push, matching CI):

```bash
./scripts/initial-setup.sh
```

To skip the hook temporarily: `git push --no-verify`.

## Lint & Format

```bash
python -m ruff check src/ tests/ --fix
python -m ruff format src/ tests/
```

## Type Checking

```bash
python -m mypy src/
```

Type checking is only enforced for `src/` (not `tests/`).

## Tests

```bash
# All tests
python -m pytest tests/

# Single test file
python -m pytest tests/test_client.py
```

## PR Guidelines

- Use [Conventional Commits](https://www.conventionalcommits.org/) for PR titles (e.g., `fix:`, `feat:`, `docs:`, `chore:`).
- Keep PRs focused on a single change.
- Make sure lint, typecheck, and tests pass before opening the PR.
