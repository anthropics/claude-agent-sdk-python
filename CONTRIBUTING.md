# Contributing

Thanks for contributing to the Claude Agent SDK for Python!

## Setting up your environment

Install the package in editable mode along with the development dependencies (pytest, mypy, ruff, and friends):

```bash
pip install -e ".[dev]"
```

Then install the git hooks:

```bash
./scripts/initial-setup.sh
```

This installs a pre-push hook that runs the same lint checks as CI before each push. To skip the hook temporarily, use `git push --no-verify`.

## Linting and formatting

```bash
# Check for issues and fix automatically
python -m ruff check src/ tests/ --fix

# Format code
python -m ruff format src/ tests/
```

CI (and the pre-push hook) runs `python -m ruff check src/ tests/` and `python -m ruff format --check src/ tests/`, so both must pass cleanly.

## Typechecking

Typechecking is only enforced for `src/`:

```bash
python -m mypy src/
```

## Running tests

```bash
# Run all tests
python -m pytest tests/

# Run a specific test file
python -m pytest tests/test_client.py
```

## Pull requests

PR titles follow the [Conventional Commits](https://www.conventionalcommits.org/) format, e.g. `fix: handle empty errors list in result messages` or `docs: document dev setup`. Common types used in this repo are `feat`, `fix`, `docs`, `chore`, and `ci`.

Before opening a PR, make sure lint, typecheck, and tests all pass locally.
