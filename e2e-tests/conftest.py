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


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default event loop policy for all async tests."""
    import asyncio

    return asyncio.get_event_loop_policy()


def pytest_configure(config):
    """Add e2e marker."""
    config.addinivalue_line("markers", "e2e: marks tests as e2e tests requiring API key")