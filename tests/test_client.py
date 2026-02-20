"""Tests for Claude SDK client functionality."""

from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from claude_agent_sdk import (
    APIError,
    AssistantMessage,
    AuthenticationError,
    ClaudeAgentOptions,
    InvalidRequestError,
    RateLimitError,
    ServerError,
    query,
)
from claude_agent_sdk.types import TextBlock


class TestQueryFunction:
    """Test the main query function."""

    def test_query_single_prompt(self):
        """Test query with a single prompt."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.InternalClient.process_query"
            ) as mock_process:
                # Mock the async generator
                async def mock_generator():
                    yield AssistantMessage(
                        content=[TextBlock(text="4")], model="claude-opus-4-1-20250805"
                    )

                mock_process.return_value = mock_generator()

                messages = []
                async for msg in query(prompt="What is 2+2?"):
                    messages.append(msg)

                assert len(messages) == 1
                assert isinstance(messages[0], AssistantMessage)
                assert messages[0].content[0].text == "4"

        anyio.run(_test)

    def test_query_with_options(self):
        """Test query with various options."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.InternalClient.process_query"
            ) as mock_process:

                async def mock_generator():
                    yield AssistantMessage(
                        content=[TextBlock(text="Hello!")],
                        model="claude-opus-4-1-20250805",
                    )

                mock_process.return_value = mock_generator()

                options = ClaudeAgentOptions(
                    allowed_tools=["Read", "Write"],
                    system_prompt="You are helpful",
                    permission_mode="acceptEdits",
                    max_turns=5,
                )

                messages = []
                async for msg in query(prompt="Hi", options=options):
                    messages.append(msg)

                # Verify process_query was called with correct prompt and options
                mock_process.assert_called_once()
                call_args = mock_process.call_args
                assert call_args[1]["prompt"] == "Hi"
                assert call_args[1]["options"] == options

        anyio.run(_test)

    def test_query_with_cwd(self):
        """Test query with custom working directory."""

        async def _test():
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_transport_class,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Done"}],
                            "model": "claude-opus-4-1-20250805",
                        },
                    }
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1000,
                        "duration_api_ms": 800,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test-session",
                        "total_cost_usd": 0.001,
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                options = ClaudeAgentOptions(cwd="/custom/path")
                messages = []
                async for msg in query(prompt="test", options=options):
                    messages.append(msg)

                # Verify transport was created with correct parameters
                mock_transport_class.assert_called_once()
                call_kwargs = mock_transport_class.call_args.kwargs
                assert call_kwargs["prompt"] == "test"
                assert call_kwargs["options"].cwd == "/custom/path"

        anyio.run(_test)


class TestAPIErrorRaising:
    """Test that API errors are raised as exceptions."""

    def test_invalid_request_error_raised(self):
        """Test that invalid_request errors are raised as InvalidRequestError."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock a message with an error
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "API Error: The provided model identifier is invalid.",
                                }
                            ],
                            "model": "claude-invalid-model",
                            "error": "invalid_request",
                        },
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                with pytest.raises(InvalidRequestError) as exc_info:
                    async for _ in query(prompt="test"):
                        pass

                assert exc_info.value.error_type == "invalid_request"
                assert "model identifier" in exc_info.value.error_text.lower()

        anyio.run(_test)

    def test_rate_limit_error_raised(self):
        """Test that rate_limit errors are raised as RateLimitError."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "API Error: Rate limit exceeded"}
                            ],
                            "model": "claude-sonnet-4-5-20250514",
                            "error": "rate_limit",
                        },
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                with pytest.raises(RateLimitError) as exc_info:
                    async for _ in query(prompt="test"):
                        pass

                assert exc_info.value.error_type == "rate_limit"

        anyio.run(_test)

    def test_authentication_error_raised(self):
        """Test that authentication_failed errors are raised as AuthenticationError."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "API Error: Invalid API key"}
                            ],
                            "model": "claude-sonnet-4-5-20250514",
                            "error": "authentication_failed",
                        },
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                with pytest.raises(AuthenticationError) as exc_info:
                    async for _ in query(prompt="test"):
                        pass

                assert exc_info.value.error_type == "authentication_failed"

        anyio.run(_test)

    def test_server_error_raised(self):
        """Test that server_error errors are raised as ServerError (529 Overloaded)."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "API Error: Repeated 529 Overloaded errors",
                                }
                            ],
                            "model": "claude-sonnet-4-5-20250514",
                            "error": "server_error",
                        },
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                with pytest.raises(ServerError) as exc_info:
                    async for _ in query(prompt="test"):
                        pass

                assert exc_info.value.error_type == "server_error"

        anyio.run(_test)

    def test_unknown_error_raised_as_base(self):
        """Test that unknown error types are raised as base APIError."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Unknown error"}],
                            "model": "claude-sonnet-4-5-20250514",
                            "error": "unknown",
                        },
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                with pytest.raises(APIError) as exc_info:
                    async for _ in query(prompt="test"):
                        pass

                assert exc_info.value.error_type == "unknown"

        anyio.run(_test)

    def test_messages_without_error_pass_through(self):
        """Test that normal messages without errors are yielded normally."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Hello!"}],
                            "model": "claude-sonnet-4-5-20250514",
                            # No error field
                        },
                    }
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1000,
                        "duration_api_ms": 800,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test-session",
                        "total_cost_usd": 0.001,
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                messages = []
                async for msg in query(prompt="test"):
                    messages.append(msg)

                # Should receive both messages without any exceptions
                assert len(messages) == 2
                assert isinstance(messages[0], AssistantMessage)
                assert messages[0].content[0].text == "Hello!"

        anyio.run(_test)
