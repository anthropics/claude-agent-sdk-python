"""Tests for the owner task pattern used for trio/asyncio compatibility.

The owner task pattern ensures that task groups are properly managed by a single
task, which is required for trio compatibility. These tests verify the pattern
works correctly when connect() and disconnect() are called from the same task.

Note: Cross-task connect/disconnect (calling connect() in one task and
disconnect() in another) is NOT supported due to cancel scope ownership
requirements. The owner task pattern ensures the INNER task group (which does
the actual message reading work) is properly managed.
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import anyio

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk._internal.query import Query


def create_mock_transport(with_init_response: bool = True) -> AsyncMock:
    """Create a properly configured mock transport.

    Args:
        with_init_response: If True, automatically respond to initialization request
    """
    mock_transport = AsyncMock()
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)

    written_messages: list[str] = []

    async def mock_write(data: str) -> None:  # noqa: ASYNC124
        written_messages.append(data)

    mock_transport.write.side_effect = mock_write

    async def control_protocol_generator():
        if with_init_response:
            # Use anyio.sleep for trio compatibility
            await anyio.sleep(0.01)

            for msg_str in written_messages:
                try:
                    msg = json.loads(msg_str.strip())
                    if (
                        msg.get("type") == "control_request"
                        and msg.get("request", {}).get("subtype") == "initialize"
                    ):
                        yield {
                            "type": "control_response",
                            "response": {
                                "request_id": msg.get("request_id"),
                                "subtype": "success",
                                "commands": [],
                                "output_style": "default",
                            },
                        }
                        break
                except (json.JSONDecodeError, KeyError, AttributeError):
                    pass

            # Keep the generator alive briefly
            timeout_counter = 0
            while timeout_counter < 50:
                await anyio.sleep(0.01)
                timeout_counter += 1

    mock_transport.read_messages = control_protocol_generator
    return mock_transport


class TestQueryOwnerTaskPattern:
    """Test Query class owner task pattern lifecycle."""

    def test_query_start_creates_owner_task(self):
        """Verify start() creates owner task and sets events."""

        async def _test():
            mock_transport = create_mock_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            await query.start()

            # Verify owner task infrastructure is set up
            assert query._owner_started_event is not None
            assert query._owner_stop_event is not None
            assert query._outer_tg is not None
            assert query._tg is not None

            # Verify started event is set (owner task is running)
            assert query._owner_started_event.is_set()

            # Clean up
            await query.close()

        anyio.run(_test)

    def test_query_close_signals_stop_event(self):
        """Verify close() signals the owner task to stop."""

        async def _test():
            mock_transport = create_mock_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            await query.start()
            await query.initialize()

            # Store reference to stop event before close
            stop_event = query._owner_stop_event

            await query.close()

            # Verify stop event was set
            assert stop_event is not None
            assert stop_event.is_set()

            # Verify task group is cleaned up
            assert query._tg is None

        anyio.run(_test)

    def test_query_double_close_is_safe(self):
        """Verify calling close() twice doesn't error."""

        async def _test():
            mock_transport = create_mock_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            await query.start()
            await query.initialize()

            # First close
            await query.close()

            # Second close should not raise
            await query.close()

        anyio.run(_test)

    def test_query_close_without_start(self):
        """Verify close() works even if start() was never called."""

        async def _test():
            mock_transport = create_mock_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            # Close without start should not raise
            await query.close()

        anyio.run(_test)


class TestClientOwnerTaskPattern:
    """Test ClaudeSDKClient with owner task pattern."""

    def test_client_context_manager_lifecycle(self):
        """Test that context manager properly manages owner task lifecycle."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    # Verify query's owner task is running
                    assert client._query is not None
                    assert client._query._tg is not None
                    assert client._query._owner_started_event.is_set()

                # After exit, transport should be closed
                mock_transport.close.assert_called()

        anyio.run(_test)

    def test_client_manual_connect_disconnect(self):
        """Test manual connect/disconnect with owner task pattern."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient()

                await client.connect()

                # Verify owner task is running
                assert client._query is not None
                assert client._query._owner_started_event.is_set()

                await client.disconnect()

                # Verify cleanup
                assert client._query is None

        anyio.run(_test)

    def test_client_double_disconnect_is_safe(self):
        """Test that disconnecting twice doesn't error."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient()
                await client.connect()

                await client.disconnect()
                await client.disconnect()  # Should not raise

        anyio.run(_test)


class TestConcurrentOperations:
    """Test concurrent operations within the same async context.

    Note: connect() and disconnect() must be called from the same task due to
    cancel scope ownership requirements. However, query and other operations
    can be performed concurrently while the client is connected.
    """

    def test_query_operations_across_tasks(self):
        """Test that query operations work across different tasks."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient()
                await client.connect()

                query_completed = anyio.Event()

                async def query_in_different_task():
                    await client.query("Hello from another task")
                    query_completed.set()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(query_in_different_task)
                    with anyio.fail_after(5):
                        await query_completed.wait()

                # Verify query was sent
                write_calls = mock_transport.write.call_args_list
                user_msg_found = False
                for call in write_calls:
                    data = call[0][0]
                    try:
                        msg = json.loads(data.strip())
                        if msg.get("type") == "user":
                            assert "Hello from another task" in str(msg)
                            user_msg_found = True
                            break
                    except (json.JSONDecodeError, KeyError):
                        pass
                assert user_msg_found

                await client.disconnect()

        anyio.run(_test)

    def test_context_manager_with_concurrent_operations(self):
        """Test context manager properly handles concurrent operations."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    # Start multiple concurrent queries
                    async def send_query(msg: str):
                        await client.query(msg)

                    async with anyio.create_task_group() as tg:
                        tg.start_soon(send_query, "Query 1")
                        tg.start_soon(send_query, "Query 2")

                # Context manager ensures proper cleanup
                mock_transport.close.assert_called()

        anyio.run(_test)


class TestTrioBackend:
    """Tests that verify the owner task pattern works with trio backend.

    These tests run with trio's stricter cancel scope rules to ensure
    the implementation is compatible with both asyncio and trio.
    """

    def test_client_with_trio_backend(self):
        """Verify client context manager works with trio backend."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    assert client._query is not None
                    await client.query("test")

                mock_transport.close.assert_called()

        anyio.run(_test, backend="trio")

    def test_query_lifecycle_with_trio_backend(self):
        """Verify Query lifecycle works with trio backend."""

        async def _test():
            mock_transport = create_mock_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            await query.start()
            assert query._tg is not None
            assert query._owner_started_event is not None
            assert query._owner_started_event.is_set()

            await query.close()
            assert query._tg is None

        anyio.run(_test, backend="trio")

    def test_manual_connect_disconnect_with_trio_backend(self):
        """Verify manual connect/disconnect works with trio backend."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient()
                await client.connect()

                assert client._query is not None
                await client.query("test message")

                await client.disconnect()
                assert client._query is None

        anyio.run(_test, backend="trio")

    def test_concurrent_queries_with_trio_backend(self):
        """Verify concurrent operations work with trio backend."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = create_mock_transport()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    async def send_query(msg: str):
                        await client.query(msg)

                    async with anyio.create_task_group() as tg:
                        tg.start_soon(send_query, "Query A")
                        tg.start_soon(send_query, "Query B")

        anyio.run(_test, backend="trio")
