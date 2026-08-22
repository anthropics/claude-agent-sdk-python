"""End-to-end test for stderr callback functionality."""

from pathlib import Path

import pytest

from claude_agent_sdk import ClaudeAgentOptions, query


@pytest.mark.e2e
@pytest.mark.anyio
async def test_stderr_callback_without_debug(tmp_path: Path):
    """Test that stderr callback is wired up and receives no output on a clean run."""
    stderr_lines = []

    def capture_stderr(line: str):
        stderr_lines.append(line)

    # Run from an empty scratch directory rather than the SDK repo itself. This
    # repo commits a .claude/settings.json containing permissions.allow entries,
    # and since CLI 2.1.193 the CLI refuses to honor project-scoped grants from
    # an untrusted workspace and says so on stderr. That warning is intentional
    # (a fresh clone must not be able to grant itself tool permissions), so the
    # run is only genuinely "clean" somewhere without project settings.
    #
    # Note we deliberately do NOT set setting_sources here: leaving it at its
    # default keeps the normal settings-resolution path under test.
    options = ClaudeAgentOptions(stderr=capture_stderr, cwd=str(tmp_path))

    # Run a simple query
    async for _ in query(prompt="What is 1+1?", options=options):
        pass  # Just consume messages

    # Should work but capture minimal/no output without debug
    assert stderr_lines == [], "Should not capture stderr output without debug mode"
