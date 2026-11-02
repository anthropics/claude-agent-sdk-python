"""Pytest configuration for e2e tests."""

import os

import pytest

# The CLI's default model, used by tests that don't set `model=` and by
# `set_model(None)`. Left to the CLI, it changes between CLI releases: 2.1.280
# moved it from claude-opus-5 to claude-opus-5-5, and for the organization CI
# runs under, the API rejects claude-opus-5-5 requests from the public CLI with
# a 400 ("This model requires Claude Code to attest its permission mode in
# metadata.user_id"). Pin the model the suite last passed on. ANTHROPIC_MODEL
# would not cover `set_model(None)`, which goes back to the CLI's default, not
# to ANTHROPIC_MODEL. A test's own `model=` still wins, and a value already
# set in the environment is kept.
os.environ.setdefault("ANTHROPIC_DEFAULT_MODEL", "claude-opus-5")


@pytest.fixture(scope="session")
def api_key():
    """Ensure ANTHROPIC_API_KEY is set for e2e tests."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.fail(
            "ANTHROPIC_API_KEY environment variable is required for e2e tests. "
            "Set it before running: export ANTHROPIC_API_KEY=your-key-here"
        )
    return key


@pytest.fixture
def anyio_backend() -> str:
    """Pin e2e tests to the asyncio backend.

    Unit tests run under both asyncio and trio (see tests/conftest.py), but
    e2e tests make real API calls, so running them under both backends would
    double cost and runtime without exercising any additional SDK code.
    """
    return "asyncio"


def pytest_configure(config):
    """Add e2e marker."""
    config.addinivalue_line(
        "markers", "e2e: marks tests as e2e tests requiring API key"
    )
