"""Pytest configuration for e2e tests."""

import os

import pytest


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
