"""End-to-end test for stderr callback functionality."""

import pytest

from claude_agent_sdk import ClaudeAgentOptions, query


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_stderr_callback_without_debug():
    """Test that stderr callback is wired up and receives no output on a clean run."""
    stderr_lines = []

    def capture_stderr(line: str):
        stderr_lines.append(line)

    # No debug mode enabled
    options = ClaudeAgentOptions(stderr=capture_stderr)

    # Run a simple query
    async for _ in query(prompt="What is 1+1?", options=options):
        pass  # Just consume messages

    # Should work but capture minimal/no output without debug
    assert len(stderr_lines) == 0, "Should not capture stderr output without debug mode"
