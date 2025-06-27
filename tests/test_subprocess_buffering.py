"""Tests for subprocess transport buffering edge cases."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio

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

    def test_multiple_json_objects_on_single_line(self) -> None:
        """Test parsing when multiple JSON objects are concatenated on a single line.

        In some environments, stdout buffering can cause multiple distinct JSON
        objects to be delivered as a single line with embedded newlines.
        """
        async def _test() -> None:
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

        anyio.run(_test)

    def test_json_with_embedded_newlines(self) -> None:
        """Test parsing JSON objects that contain newline characters in string values."""
        async def _test() -> None:
            # JSON objects with newlines in string values
            json_obj1 = {"type": "message", "content": "Line 1\nLine 2\nLine 3"}
            json_obj2 = {"type": "result", "data": "Some\nMultiline\nContent"}
            
            buffered_line = json.dumps(json_obj1) + '\n' + json.dumps(json_obj2)
            
            transport = SubprocessCLITransport(
                prompt="test",
                options=ClaudeCodeOptions(),
                cli_path="/usr/bin/claude"
            )
            
            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([buffered_line])
            transport._stderr_stream = MockTextReceiveStream([])
            
            messages: list[Any] = []
            async for msg in transport.receive_messages():
                messages.append(msg)
            
            assert len(messages) == 2
            assert messages[0]["content"] == "Line 1\nLine 2\nLine 3"
            assert messages[1]["data"] == "Some\nMultiline\nContent"

        anyio.run(_test)

    def test_multiple_newlines_between_objects(self) -> None:
        """Test parsing with multiple newlines between JSON objects."""
        async def _test() -> None:
            json_obj1 = {"type": "message", "id": "msg1"}
            json_obj2 = {"type": "result", "id": "res1"}
            
            # Multiple newlines between objects
            buffered_line = json.dumps(json_obj1) + '\n\n\n' + json.dumps(json_obj2)
            
            transport = SubprocessCLITransport(
                prompt="test",
                options=ClaudeCodeOptions(),
                cli_path="/usr/bin/claude"
            )
            
            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([buffered_line])
            transport._stderr_stream = MockTextReceiveStream([])
            
            messages: list[Any] = []
            async for msg in transport.receive_messages():
                messages.append(msg)
            
            assert len(messages) == 2
            assert messages[0]["id"] == "msg1"
            assert messages[1]["id"] == "res1"

        anyio.run(_test)
