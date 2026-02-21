"""Tests for task context validation in ClaudeSDKClient."""

import asyncio
from unittest.mock import AsyncMock

import anyio
import pytest

from claude_agent_sdk import TaskContextError
from claude_agent_sdk._internal.query import Query


def create_mock_transport():
    """Create a minimal mock transport for testing."""
    mock_transport = AsyncMock()
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.end_input = AsyncMock()

    async def mock_read():
        # Just yield messages and end
        yield {"type": "end"}

    mock_transport.read_messages = mock_read
    return mock_transport


class TestTaskContextValidation:
    """Test task context detection and error handling."""

    def test_receive_messages_same_task(self):
        """Test that receive_messages works in the same task as start."""

        async def _test():
            mock_transport = create_mock_transport()
            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            await query.start()
            # Should work fine - same task
            count = 0
            async for _ in query.receive_messages():
                count += 1

            await query.close()

        anyio.run(_test)

    def test_receive_messages_different_task_raises_error(self):
        """Test that receive_messages raises TaskContextError from different task."""

        async def _test():
            mock_transport = create_mock_transport()
            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            # Connect in task A (current task)
            await query.start()

            # Try to receive in task B
            async def receive_in_different_task():
                async for _ in query.receive_messages():
                    pass  # Should never reach here

            receive_task = asyncio.create_task(receive_in_different_task())

            # Should raise TaskContextError
            with pytest.raises(TaskContextError) as exc_info:
                await receive_task

            # Verify error attributes
            error = exc_info.value
            assert error.connect_task_id is not None
            assert error.current_task_id is not None
            assert error.connect_task_id != error.current_task_id

            # Verify error message is helpful
            error_msg = str(error)
            assert "different async task" in error_msg
            assert "connect task" in error_msg
            assert "current task" in error_msg

            await query.close()

        anyio.run(_test)

    def test_multiple_query_objects_different_tasks(self):
        """Test that multiple Query objects can coexist in different tasks."""

        async def _test():
            async def use_query_in_task():
                mock_transport = create_mock_transport()
                query = Query(
                    transport=mock_transport,
                    is_streaming_mode=True,
                )
                await query.start()
                async for _ in query.receive_messages():
                    pass  # Should work fine
                await query.close()

            # Create two queries in different tasks
            task1 = asyncio.create_task(use_query_in_task())
            task2 = asyncio.create_task(use_query_in_task())

            # Both should succeed
            await asyncio.gather(task1, task2)

        anyio.run(_test)

    def test_receive_messages_without_start_raises_error(self):
        """Test that receive_messages raises RuntimeError if start() not called."""

        async def _test():
            mock_transport = create_mock_transport()
            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            # Don't call start()
            with pytest.raises(RuntimeError) as exc_info:
                async for _ in query.receive_messages():
                    pass

            assert "Query.start() must be called" in str(exc_info.value)

        anyio.run(_test)

    def test_task_context_error_attributes(self):
        """Test TaskContextError stores task IDs correctly."""

        error = TaskContextError(
            "Test error",
            connect_task_id=123,
            current_task_id=456,
        )

        assert error.connect_task_id == 123
        assert error.current_task_id == 456
        assert "Test error" in str(error)
        assert "123" in str(error)
        assert "456" in str(error)

    def test_task_context_error_without_task_ids(self):
        """Test TaskContextError works without task IDs."""

        error = TaskContextError("Test error")

        assert error.connect_task_id is None
        assert error.current_task_id is None
        assert "Test error" in str(error)

