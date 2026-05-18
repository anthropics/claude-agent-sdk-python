# Contributing

Thanks for helping improve the Claude Agent SDK for Python.

## Development setup

Install the package with development dependencies:

```bash
pip install -e ".[dev]"
```

To install the pre-push hook used by this repository, run:

```bash
./scripts/initial-setup.sh
```

## Local checks

Run the checks that match your change before opening a pull request:

```bash
# Lint and automatically fix issues
python -m ruff check src/ tests/ --fix

# Format source and tests
python -m ruff format src/ tests/

# Typecheck the SDK package
python -m mypy src/

# Run the test suite
python -m pytest tests/
```

For a focused test run, pass a specific test file:

```bash
python -m pytest tests/test_client.py
```

## Pull request titles

Use a short, descriptive title. Recent pull requests generally follow
Conventional Commits style, such as:

- `fix(query): surface result error text`
- `docs(types): clarify hooks dispatch behavior`
- `deps: bump mcp lower bound`

