"""Test FastAPI streaming compatibility (issue #4 fix)."""

import inspect

from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport


def test_no_task_groups_in_receive_messages():
    """Verify receive_messages doesn't use task groups (fixes FastAPI issue #4)."""
    # Get the source code of receive_messages
    source = inspect.getsource(SubprocessCLITransport.receive_messages)

    # The fix: ensure no task group or task creation
    assert "create_task_group" not in source, (
        "receive_messages must not use create_task_group to avoid "
        "RuntimeError with FastAPI streaming"
    )
    assert "asyncio.create_task" not in source, (
        "receive_messages must not create tasks to maintain "
        "compatibility with FastAPI's generator handling"
    )

    # Verify stderr is still being read (sequential approach)
    assert "_stderr_stream" in source, "Should still read stderr"
    assert "stderr_lines" in source, "Should collect stderr output"
