"""Tests for ClaudeSDKClient streaming functionality and query() with async iterables."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    UserMessage,
    query,
)


class TestClaudeSDKClientStreaming:
    """Test ClaudeSDKClient streaming functionality."""

    def test_auto_connect_with_context_manager(self):
        """Test automatic connection when using context manager."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    # Verify connect was called
                    mock_transport.connect.assert_called_once()
                    assert client._transport is mock_transport

                # Verify disconnect was called on exit
                mock_transport.disconnect.assert_called_once()

        anyio.run(_test)

    def test_manual_connect_disconnect(self):
        """Test manual connect and disconnect."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient()
                await client.connect()

                # Verify connect was called
                mock_transport.connect.assert_called_once()
                assert client._transport is mock_transport

                await client.disconnect()
                # Verify disconnect was called
                mock_transport.disconnect.assert_called_once()
                assert client._transport is None

        anyio.run(_test)

    def test_connect_with_string_prompt(self):
        """Test connecting with a string prompt."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient()
                await client.connect("Hello Claude")

                # Verify transport was created with string prompt
                call_kwargs = mock_transport_class.call_args.kwargs
                assert call_kwargs["prompt"] == "Hello Claude"

        anyio.run(_test)

    def test_connect_with_async_iterable(self):
        """Test connecting with an async iterable."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async def message_stream():
                    yield {"type": "user", "message": {"role": "user", "content": "Hi"}}
                    yield {"type": "user", "message": {"role": "user", "content": "Bye"}}

                client = ClaudeSDKClient()
                stream = message_stream()
                await client.connect(stream)

                # Verify transport was created with async iterable
                call_kwargs = mock_transport_class.call_args.kwargs
                # Should be the same async iterator
                assert call_kwargs["prompt"] is stream

        anyio.run(_test)

    def test_send_message(self):
        """Test sending a message."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    await client.send_message("Test message")

                    # Verify send_request was called with correct format
                    mock_transport.send_request.assert_called_once()
                    call_args = mock_transport.send_request.call_args
                    messages, options = call_args[0]
                    assert len(messages) == 1
                    assert messages[0]["type"] == "user"
                    assert messages[0]["message"]["content"] == "Test message"
                    assert options["session_id"] == "default"

        anyio.run(_test)

    def test_send_message_with_session_id(self):
        """Test sending a message with custom session ID."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    await client.send_message("Test", session_id="custom-session")

                    call_args = mock_transport.send_request.call_args
                    messages, options = call_args[0]
                    assert messages[0]["session_id"] == "custom-session"
                    assert options["session_id"] == "custom-session"

        anyio.run(_test)

    def test_send_message_not_connected(self):
        """Test sending message when not connected raises error."""
        async def _test():
            client = ClaudeSDKClient()
            with pytest.raises(CLIConnectionError, match="Not connected"):
                await client.send_message("Test")

        anyio.run(_test)

    def test_receive_messages(self):
        """Test receiving messages."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Hello!"}],
                        },
                    }
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": "Hi there"},
                    }

                mock_transport.receive_messages = mock_receive

                async with ClaudeSDKClient() as client:
                    messages = []
                    async for msg in client.receive_messages():
                        messages.append(msg)
                        if len(messages) == 2:
                            break

                    assert len(messages) == 2
                    assert isinstance(messages[0], AssistantMessage)
                    assert isinstance(messages[0].content[0], TextBlock)
                    assert messages[0].content[0].text == "Hello!"
                    assert isinstance(messages[1], UserMessage)
                    assert messages[1].content == "Hi there"

        anyio.run(_test)

    def test_receive_response(self):
        """Test receive_response stops at ResultMessage."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Answer"}],
                        },
                    }
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1000,
                        "duration_api_ms": 800,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test",
                        "total_cost_usd": 0.001,
                    }
                    # This should not be yielded
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Should not see this"}],
                        },
                    }

                mock_transport.receive_messages = mock_receive

                async with ClaudeSDKClient() as client:
                    messages = []
                    async for msg in client.receive_response():
                        messages.append(msg)

                    # Should only get 2 messages (assistant + result)
                    assert len(messages) == 2
                    assert isinstance(messages[0], AssistantMessage)
                    assert isinstance(messages[1], ResultMessage)

        anyio.run(_test)

    def test_interrupt(self):
        """Test interrupt functionality."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async with ClaudeSDKClient() as client:
                    await client.interrupt()
                    mock_transport.interrupt.assert_called_once()

        anyio.run(_test)

    def test_interrupt_not_connected(self):
        """Test interrupt when not connected raises error."""
        async def _test():
            client = ClaudeSDKClient()
            with pytest.raises(CLIConnectionError, match="Not connected"):
                await client.interrupt()

        anyio.run(_test)

    def test_client_with_options(self):
        """Test client initialization with options."""
        async def _test():
            options = ClaudeCodeOptions(
                cwd="/custom/path",
                allowed_tools=["Read", "Write"],
                system_prompt="Be helpful",
            )

            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient(options=options)
                await client.connect()

                # Verify options were passed to transport
                call_kwargs = mock_transport_class.call_args.kwargs
                assert call_kwargs["options"] is options

        anyio.run(_test)

    def test_concurrent_send_receive(self):
        """Test concurrent sending and receiving messages."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock receive to wait then yield messages
                async def mock_receive():
                    await asyncio.sleep(0.1)
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Response 1"}],
                        },
                    }
                    await asyncio.sleep(0.1)
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1000,
                        "duration_api_ms": 800,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test",
                        "total_cost_usd": 0.001,
                    }

                mock_transport.receive_messages = mock_receive

                async with ClaudeSDKClient() as client:
                    # Helper to get next message
                    async def get_next_message():
                        return await client.receive_response().__anext__()
                    
                    # Start receiving in background
                    receive_task = asyncio.create_task(get_next_message())

                    # Send message while receiving
                    await client.send_message("Question 1")

                    # Wait for first message
                    first_msg = await receive_task
                    assert isinstance(first_msg, AssistantMessage)

        anyio.run(_test)


class TestQueryWithAsyncIterable:
    """Test query() function with async iterable inputs."""

    def test_query_with_async_iterable(self):
        """Test query with async iterable of messages."""
        async def _test():
            async def message_stream():
                yield {"type": "user", "message": {"role": "user", "content": "First"}}
                yield {"type": "user", "message": {"role": "user", "content": "Second"}}

            with patch(
                "claude_code_sdk.query.InternalClient"
            ) as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.return_value = mock_client

                # Mock the async generator response
                async def mock_process():
                    yield AssistantMessage(
                        content=[TextBlock(text="Response to both messages")]
                    )
                    yield ResultMessage(
                        subtype="success",
                        duration_ms=1000,
                        duration_api_ms=800,
                        is_error=False,
                        num_turns=2,
                        session_id="test",
                        total_cost_usd=0.002,
                    )

                mock_client.process_query.return_value = mock_process()

                # Run query with async iterable
                messages = []
                async for msg in query(prompt=message_stream()):
                    messages.append(msg)

                assert len(messages) == 2
                assert isinstance(messages[0], AssistantMessage)
                assert isinstance(messages[1], ResultMessage)

                # Verify process_query was called with async iterable
                call_kwargs = mock_client.process_query.call_args.kwargs
                # The prompt should be an async generator
                assert hasattr(call_kwargs["prompt"], "__aiter__")

        anyio.run(_test)

    def test_query_async_iterable_with_options(self):
        """Test query with async iterable and custom options."""
        async def _test():
            async def complex_stream():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": "Setup"},
                    "parent_tool_use_id": None,
                    "session_id": "session-1",
                }
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": "Execute"},
                    "parent_tool_use_id": None,
                    "session_id": "session-1",
                }

            options = ClaudeCodeOptions(
                cwd="/workspace",
                permission_mode="acceptEdits",
                max_turns=10,
            )

            with patch(
                "claude_code_sdk.query.InternalClient"
            ) as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.return_value = mock_client

                # Mock response
                async def mock_process():
                    yield AssistantMessage(content=[TextBlock(text="Done")])

                mock_client.process_query.return_value = mock_process()

                # Run query
                messages = []
                async for msg in query(prompt=complex_stream(), options=options):
                    messages.append(msg)

                # Verify options were passed
                call_kwargs = mock_client.process_query.call_args.kwargs
                assert call_kwargs["options"] is options

        anyio.run(_test)

    def test_query_empty_async_iterable(self):
        """Test query with empty async iterable."""
        async def _test():
            async def empty_stream():
                # Never yields anything
                if False:
                    yield

            with patch(
                "claude_code_sdk.query.InternalClient"
            ) as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.return_value = mock_client

                # Mock response
                async def mock_process():
                    yield SystemMessage(
                        subtype="info",
                        data={"message": "No input provided"}
                    )

                mock_client.process_query.return_value = mock_process()

                # Run query with empty stream
                messages = []
                async for msg in query(prompt=empty_stream()):
                    messages.append(msg)

                assert len(messages) == 1
                assert isinstance(messages[0], SystemMessage)

        anyio.run(_test)

    def test_query_async_iterable_with_delay(self):
        """Test query with async iterable that has delays between yields."""
        async def _test():
            async def delayed_stream():
                yield {"type": "user", "message": {"role": "user", "content": "Start"}}
                await asyncio.sleep(0.1)
                yield {"type": "user", "message": {"role": "user", "content": "Middle"}}
                await asyncio.sleep(0.1)
                yield {"type": "user", "message": {"role": "user", "content": "End"}}

            with patch(
                "claude_code_sdk.query.InternalClient"
            ) as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.return_value = mock_client

                # Track if the stream was consumed
                stream_consumed = False

                # Mock process_query to consume the input stream
                async def mock_process_query(prompt, options):
                    nonlocal stream_consumed
                    # Consume the async iterable to trigger delays
                    items = []
                    async for item in prompt:
                        items.append(item)
                    stream_consumed = True
                    # Then yield response
                    yield AssistantMessage(
                        content=[TextBlock(text="Processing all messages")]
                    )

                mock_client.process_query = mock_process_query

                # Time the execution
                import time
                start_time = time.time()
                messages = []
                async for msg in query(prompt=delayed_stream()):
                    messages.append(msg)
                elapsed = time.time() - start_time

                # Should have taken at least 0.2 seconds due to delays
                assert elapsed >= 0.2
                assert len(messages) == 1
                assert stream_consumed

        anyio.run(_test)

    def test_query_async_iterable_exception_handling(self):
        """Test query handles exceptions in async iterable."""
        async def _test():
            async def failing_stream():
                yield {"type": "user", "message": {"role": "user", "content": "First"}}
                raise ValueError("Stream error")

            with patch(
                "claude_code_sdk.query.InternalClient"
            ) as mock_client_class:
                mock_client = MagicMock()
                mock_client_class.return_value = mock_client

                # The internal client should receive the failing stream
                # and handle the error appropriately
                async def mock_process():
                    # Simulate processing until error
                    yield AssistantMessage(content=[TextBlock(text="Error occurred")])

                mock_client.process_query.return_value = mock_process()

                # Query should handle the error gracefully
                messages = []
                async for msg in query(prompt=failing_stream()):
                    messages.append(msg)

                assert len(messages) == 1

        anyio.run(_test)


class TestClaudeSDKClientEdgeCases:
    """Test edge cases and error scenarios."""

    def test_receive_messages_not_connected(self):
        """Test receiving messages when not connected."""
        async def _test():
            client = ClaudeSDKClient()
            with pytest.raises(CLIConnectionError, match="Not connected"):
                async for _ in client.receive_messages():
                    pass

        anyio.run(_test)

    def test_receive_response_not_connected(self):
        """Test receive_response when not connected."""
        async def _test():
            client = ClaudeSDKClient()
            with pytest.raises(CLIConnectionError, match="Not connected"):
                async for _ in client.receive_response():
                    pass

        anyio.run(_test)

    def test_double_connect(self):
        """Test connecting twice."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                client = ClaudeSDKClient()
                await client.connect()
                # Second connect should create new transport
                await client.connect()

                # Should have been called twice
                assert mock_transport_class.call_count == 2

        anyio.run(_test)

    def test_disconnect_without_connect(self):
        """Test disconnecting without connecting first."""
        async def _test():
            client = ClaudeSDKClient()
            # Should not raise error
            await client.disconnect()

        anyio.run(_test)

    def test_context_manager_with_exception(self):
        """Test context manager cleans up on exception."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                with pytest.raises(ValueError):
                    async with ClaudeSDKClient():
                        raise ValueError("Test error")

                # Disconnect should still be called
                mock_transport.disconnect.assert_called_once()

        anyio.run(_test)

    def test_receive_response_list_comprehension(self):
        """Test collecting messages with list comprehension as shown in examples."""
        async def _test():
            with patch(
                "claude_code_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Hello"}],
                        },
                    }
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "World"}],
                        },
                    }
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1000,
                        "duration_api_ms": 800,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test",
                        "total_cost_usd": 0.001,
                    }

                mock_transport.receive_messages = mock_receive

                async with ClaudeSDKClient() as client:
                    # Test list comprehension pattern from docstring
                    messages = [msg async for msg in client.receive_response()]

                    assert len(messages) == 3
                    assert all(isinstance(msg, AssistantMessage | ResultMessage) for msg in messages)
                    assert isinstance(messages[-1], ResultMessage)

        anyio.run(_test)
