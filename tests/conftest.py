"""Pytest configuration: run every ``@pytest.mark.anyio`` test under both
asyncio and trio, so CI catches backend-specific regressions in either."""

import pytest


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return request.param
