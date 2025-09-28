"""End-to-end test for stderr callback functionality."""

import pytest

from claude_agent_sdk import ClaudeAgentOptions, query


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_stderr_callback_captures_debug_output():
    """Test that stderr callback receives debug output when enabled."""
    stderr_lines = []

    def capture_stderr(line: str):
        stderr_lines.append(line)

    # Enable debug mode to generate stderr output
    options = ClaudeAgentOptions(
        stderr=capture_stderr,
        extra_args={"debug-to-stderr": None}
    )

    # Run a simple query
    async for _ in query(prompt="What is 1+1?", options=options):
        pass  # Just consume messages

    # Verify we captured debug output
    assert len(stderr_lines) > 0, "Should capture stderr output with debug enabled"
    assert any("[DEBUG]" in line for line in stderr_lines), "Should contain DEBUG messages"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_stderr_callback_without_debug():
    """Test that stderr callback works but receives no output without debug mode."""
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