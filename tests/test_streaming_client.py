"""Tests for ClaudeSDKClient streaming functionality and query() with async iterables."""

import asyncio
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import anyio
import pytest

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    ResultMessage,
    TextBlock,
    UserMessage,
    query,
)
from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport


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
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": "Bye"},
                    }

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
                            "content": [
                                {"type": "text", "text": "Should not see this"}
                            ],
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

    def _create_test_script(
        self, expected_messages=None, response=None, should_error=False
    ):
        """Create a test script that validates CLI args and stdin messages.

        Args:
            expected_messages: List of expected message content strings, or None to skip validation
            response: Custom response to output, defaults to a success result
            should_error: If True, script will exit with error after reading stdin

        Returns:
            Path to the test script
        """
        if response is None:
            response = '{"type": "result", "subtype": "success", "duration_ms": 100, "duration_api_ms": 50, "is_error": false, "num_turns": 1, "session_id": "test", "total_cost_usd": 0.001}'

        script_content = textwrap.dedent(
            """
            #!/usr/bin/env python3
            import sys
            import json
            import time

            # Check command line args
            args = sys.argv[1:]
            assert "--output-format" in args
            assert "stream-json" in args

            # Read stdin messages
            stdin_messages = []
            stdin_closed = False
            try:
                while True:
                    line = sys.stdin.readline()
                    if not line:
                        stdin_closed = True
                        break
                    stdin_messages.append(line.strip())
            except:
                stdin_closed = True
            """,
        )

        if expected_messages is not None:
            script_content += textwrap.dedent(
                f"""
                # Verify we got the expected messages
                assert len(stdin_messages) == {len(expected_messages)}
                """,
            )
            for i, msg in enumerate(expected_messages):
                script_content += f'''assert '"{msg}"' in stdin_messages[{i}]\n'''

        if should_error:
            script_content += textwrap.dedent(
                """
                sys.exit(1)
                """,
            )
        else:
            script_content += textwrap.dedent(
                f"""
                # Output response
                print('{response}')
                """,
            )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            test_script = f.name
            f.write(script_content)

        Path(test_script).chmod(0o755)
        return test_script

    def test_query_with_async_iterable(self):
        """Test query with async iterable of messages."""

        async def _test():
            async def message_stream():
                yield {"type": "user", "message": {"role": "user", "content": "First"}}
                yield {"type": "user", "message": {"role": "user", "content": "Second"}}

            test_script = self._create_test_script(
                expected_messages=["First", "Second"]
            )

            try:
                # Mock _build_command to return our test script
                with patch.object(
                    SubprocessCLITransport,
                    "_build_command",
                    return_value=[
                        sys.executable,
                        test_script,
                        "--output-format",
                        "stream-json",
                        "--verbose",
                    ],
                ):
                    # Run query with async iterable
                    messages = []
                    async for msg in query(prompt=message_stream()):
                        messages.append(msg)

                    # Should get the result message
                    assert len(messages) == 1
                    assert isinstance(messages[0], ResultMessage)
                    assert messages[0].subtype == "success"
            finally:
                # Clean up
                Path(test_script).unlink()

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
                    assert all(
                        isinstance(msg, AssistantMessage | ResultMessage)
                        for msg in messages
                    )
                    assert isinstance(messages[-1], ResultMessage)

        anyio.run(_test)
