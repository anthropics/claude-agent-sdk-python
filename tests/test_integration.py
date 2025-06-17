"""Integration tests for Claude SDK.

These tests verify end-to-end functionality with mocked CLI responses.
"""

from unittest.mock import AsyncMock, patch

import anyio
import pytest

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    CLINotFoundError,
    ResultMessage,
    query,
)
from claude_code_sdk.types import ToolUseBlock


class TestIntegration:
    """End-to-end integration tests."""

    def test_simple_query_response(self):
        """Test a simple query with text response."""

        async def _test():
            with patch(
                "claude_code_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "2 + 2 equals 4"}],
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

                mock_transport.receive_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.disconnect = AsyncMock()

                # Run query
                messages = []
                async for msg in query(prompt="What is 2 + 2?"):
                    messages.append(msg)

                # Verify results
                assert len(messages) == 2

                # Check assistant message
                assert isinstance(messages[0], AssistantMessage)
                assert len(messages[0].content) == 1
                assert messages[0].content[0].text == "2 + 2 equals 4"

                # Check result message
                assert isinstance(messages[1], ResultMessage)
                assert messages[1].total_cost_usd == 0.001
                assert messages[1].session_id == "test-session"

        anyio.run(_test)

    def test_query_with_tool_use(self):
        """Test query that uses tools."""

        async def _test():
            with patch(
                "claude_code_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream with tool use
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Let me read that file for you.",
                                },
                                {
                                    "type": "tool_use",
                                    "id": "tool-123",
                                    "name": "Read",
                                    "input": {"file_path": "/test.txt"},
                                },
                            ],
                        },
                    }
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1500,
                        "duration_api_ms": 1200,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test-session-2",
                        "total_cost_usd": 0.002,
                    }

                mock_transport.receive_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.disconnect = AsyncMock()

                # Run query with tools enabled
                messages = []
                async for msg in query(
                    prompt="Read /test.txt",
                    options=ClaudeCodeOptions(allowed_tools=["Read"]),
                ):
                    messages.append(msg)

                # Verify results
                assert len(messages) == 2

                # Check assistant message with tool use
                assert isinstance(messages[0], AssistantMessage)
                assert len(messages[0].content) == 2
                assert messages[0].content[0].text == "Let me read that file for you."
                assert isinstance(messages[0].content[1], ToolUseBlock)
                assert messages[0].content[1].name == "Read"
                assert messages[0].content[1].input["file_path"] == "/test.txt"

        anyio.run(_test)

    def test_cli_not_found(self):
        """Test handling when CLI is not found."""

        async def _test():
            with (
                patch("shutil.which", return_value=None),
                patch("pathlib.Path.exists", return_value=False),
                pytest.raises(CLINotFoundError) as exc_info,
            ):
                async for _ in query(prompt="test"):
                    pass

            assert "Claude Code requires Node.js" in str(exc_info.value)

        anyio.run(_test)

    def test_continuation_option(self):
        """Test query with continue_conversation option."""

        async def _test():
            with patch(
                "claude_code_sdk._internal.client.SubprocessCLITransport"
            ) as mock_transport_class:
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Continuing from previous conversation",
                                }
                            ],
                        },
                    }

                mock_transport.receive_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.disconnect = AsyncMock()

                # Run query with continuation
                messages = []
                async for msg in query(
                    prompt="Continue",
                    options=ClaudeCodeOptions(continue_conversation=True),
                ):
                    messages.append(msg)

                # Verify transport was created with continuation option
                mock_transport_class.assert_called_once()
                call_kwargs = mock_transport_class.call_args.kwargs
                assert call_kwargs["options"].continue_conversation is True

        anyio.run(_test)
