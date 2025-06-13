"""Tests for subprocess transport buffering edge cases."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_code_sdk._errors import CLIJSONDecodeError
from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_code_sdk.types import ClaudeCodeOptions


class MockTextReceiveStream:
    """Mock TextReceiveStream for testing."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.index = 0

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self.index >= len(self.lines):
            raise StopAsyncIteration
        line = self.lines[self.index]
        self.index += 1
        return line


class TestSubprocessBuffering:
    """Test subprocess transport handling of buffered output."""

    @pytest.mark.asyncio
    async def test_multiple_json_objects_on_single_line(self) -> None:
        """Test parsing when multiple JSON objects are concatenated on a single line.

        In some environments, stdout buffering can cause multiple distinct JSON
        objects to be delivered as a single line with embedded newlines.
        """
        # Two valid JSON objects separated by a newline character
        json_obj1 = {"type": "message", "id": "msg1", "content": "First message"}
        json_obj2 = {"type": "result", "id": "res1", "status": "completed"}

        # Simulate buffered output where both objects appear on one line
        buffered_line = json.dumps(json_obj1) + '\n' + json.dumps(json_obj2)

        # Create transport
        transport = SubprocessCLITransport(
            prompt="test",
            options=ClaudeCodeOptions(),
            cli_path="/usr/bin/claude"
        )

        # Mock the process and streams
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=None)
        transport._process = mock_process

        # Create mock stream that returns the buffered line
        transport._stdout_stream = MockTextReceiveStream([buffered_line])  # type: ignore[assignment]
        transport._stderr_stream = MockTextReceiveStream([])  # type: ignore[assignment]

        # Collect all messages
        messages: list[Any] = []
        async for msg in transport.receive_messages():
            messages.append(msg)

        # Verify both JSON objects were successfully parsed
        assert len(messages) == 2
        assert messages[0]["type"] == "message"
        assert messages[0]["id"] == "msg1"
        assert messages[0]["content"] == "First message"
        assert messages[1]["type"] == "result"
        assert messages[1]["id"] == "res1"
        assert messages[1]["status"] == "completed"
