"""Tests for subprocess transport buffering edge cases."""

import json
from collections.abc import AsyncIterator
from io import StringIO
from subprocess import PIPE
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from anyio.streams.text import TextReceiveStream

from claude_agent_sdk._errors import CLIJSONDecodeError, ProcessError
from claude_agent_sdk._internal.transport.subprocess_cli import (
    _DEFAULT_MAX_BUFFER_SIZE,
    SubprocessCLITransport,
)
from claude_agent_sdk.types import ClaudeAgentOptions

DEFAULT_CLI_PATH = "/usr/bin/claude"


def make_options(**kwargs: object) -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions with a default CLI path for tests."""

    cli_path = kwargs.pop("cli_path", DEFAULT_CLI_PATH)
    return ClaudeAgentOptions(cli_path=cli_path, **kwargs)


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
            json_obj1 = {"type": "message", "id": "msg1", "content": "First message"}
            json_obj2 = {"type": "result", "id": "res1", "status": "completed"}

            buffered_line = json.dumps(json_obj1) + "\n" + json.dumps(json_obj2)

            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process

            transport._stdout_stream = MockTextReceiveStream([buffered_line])  # type: ignore[assignment]
            transport._stderr_stream = MockTextReceiveStream([])  # type: ignore[assignment]

            messages: list[Any] = []
            async for msg in transport.read_messages():
                messages.append(msg)

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
            json_obj1 = {"type": "message", "content": "Line 1\nLine 2\nLine 3"}
            json_obj2 = {"type": "result", "data": "Some\nMultiline\nContent"}

            buffered_line = json.dumps(json_obj1) + "\n" + json.dumps(json_obj2)

            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([buffered_line])
            transport._stderr_stream = MockTextReceiveStream([])

            messages: list[Any] = []
            async for msg in transport.read_messages():
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

            buffered_line = json.dumps(json_obj1) + "\n\n\n" + json.dumps(json_obj2)

            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([buffered_line])
            transport._stderr_stream = MockTextReceiveStream([])

            messages: list[Any] = []
            async for msg in transport.read_messages():
                messages.append(msg)

            assert len(messages) == 2
            assert messages[0]["id"] == "msg1"
            assert messages[1]["id"] == "res1"

        anyio.run(_test)

    def test_split_json_across_multiple_reads(self) -> None:
        """Test parsing when a single JSON object is split across multiple stream reads."""

        async def _test() -> None:
            json_obj = {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "x" * 1000},
                        {
                            "type": "tool_use",
                            "id": "tool_123",
                            "name": "Read",
                            "input": {"file_path": "/test.txt"},
                        },
                    ]
                },
            }

            complete_json = json.dumps(json_obj)

            part1 = complete_json[:100]
            part2 = complete_json[100:250]
            part3 = complete_json[250:]

            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([part1, part2, part3])
            transport._stderr_stream = MockTextReceiveStream([])

            messages: list[Any] = []
            async for msg in transport.read_messages():
                messages.append(msg)

            assert len(messages) == 1
            assert messages[0]["type"] == "assistant"
            assert len(messages[0]["message"]["content"]) == 2

        anyio.run(_test)

    def test_large_minified_json(self) -> None:
        """Test parsing a large minified JSON (simulating the reported issue)."""

        async def _test() -> None:
            large_data = {"data": [{"id": i, "value": "x" * 100} for i in range(1000)]}
            json_obj = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "tool_use_id": "toolu_016fed1NhiaMLqnEvrj5NUaj",
                            "type": "tool_result",
                            "content": json.dumps(large_data),
                        }
                    ],
                },
            }

            complete_json = json.dumps(json_obj)

            chunk_size = 64 * 1024
            chunks = [
                complete_json[i : i + chunk_size]
                for i in range(0, len(complete_json), chunk_size)
            ]

            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream(chunks)
            transport._stderr_stream = MockTextReceiveStream([])

            messages: list[Any] = []
            async for msg in transport.read_messages():
                messages.append(msg)

            assert len(messages) == 1
            assert messages[0]["type"] == "user"
            assert (
                messages[0]["message"]["content"][0]["tool_use_id"]
                == "toolu_016fed1NhiaMLqnEvrj5NUaj"
            )

        anyio.run(_test)

    def test_buffer_size_exceeded(self) -> None:
        """Test that exceeding buffer size raises an appropriate error."""

        async def _test() -> None:
            huge_incomplete = '{"data": "' + "x" * (_DEFAULT_MAX_BUFFER_SIZE + 1000)

            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([huge_incomplete])
            transport._stderr_stream = MockTextReceiveStream([])

            with pytest.raises(Exception) as exc_info:
                messages: list[Any] = []
                async for msg in transport.read_messages():
                    messages.append(msg)

            assert isinstance(exc_info.value, CLIJSONDecodeError)
            assert "exceeded maximum buffer size" in str(exc_info.value)

        anyio.run(_test)

    def test_buffer_size_option(self) -> None:
        """Test that the configurable buffer size option is respected."""

        async def _test() -> None:
            custom_limit = 512
            huge_incomplete = '{"data": "' + "x" * (custom_limit + 10)

            transport = SubprocessCLITransport(
                prompt="test",
                options=make_options(max_buffer_size=custom_limit),
            )

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([huge_incomplete])
            transport._stderr_stream = MockTextReceiveStream([])

            with pytest.raises(CLIJSONDecodeError) as exc_info:
                async for _ in transport.read_messages():
                    pass

            assert f"maximum buffer size of {custom_limit} bytes" in str(exc_info.value)

        anyio.run(_test)

    def test_mixed_complete_and_split_json(self) -> None:
        """Test handling a mix of complete and split JSON messages."""

        async def _test() -> None:
            msg1 = json.dumps({"type": "system", "subtype": "start"})

            large_msg = {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "y" * 5000}]},
            }
            large_json = json.dumps(large_msg)

            msg3 = json.dumps({"type": "system", "subtype": "end"})

            lines = [
                msg1 + "\n",
                large_json[:1000],
                large_json[1000:3000],
                large_json[3000:] + "\n" + msg3,
            ]

            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=None)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream(lines)
            transport._stderr_stream = MockTextReceiveStream([])

            messages: list[Any] = []
            async for msg in transport.read_messages():
                messages.append(msg)

            assert len(messages) == 3
            assert messages[0]["type"] == "system"
            assert messages[0]["subtype"] == "start"
            assert messages[1]["type"] == "assistant"
            assert len(messages[1]["message"]["content"][0]["text"]) == 5000
            assert messages[2]["type"] == "system"
            assert messages[2]["subtype"] == "end"

        anyio.run(_test)

    def test_non_json_debug_lines_skipped(self) -> None:
        """Non-JSON lines (e.g. [SandboxDebug]) on stdout must not corrupt
        the JSON parser buffer.  Regression test for #347."""

        async def _test() -> None:
            debug = "[SandboxDebug] Seccomp filtering not available"
            msg1 = json.dumps({"type": "system", "subtype": "init"})
            msg2 = json.dumps({"type": "result", "subtype": "success"})

            stream = MockTextReceiveStream([f"{debug}\n{msg1}\n{debug}\n{msg2}\n"])

            transport = SubprocessCLITransport(prompt="test", options=make_options())
            transport._stdout_stream = stream
            transport._process = MagicMock()
            transport._process.wait = AsyncMock(return_value=0)

            messages: list[dict[str, Any]] = []
            async for msg in transport.read_messages():
                messages.append(msg)

            assert len(messages) == 2
            assert messages[0]["type"] == "system"
            assert messages[1]["type"] == "result"

        anyio.run(_test)

    def test_interleaved_non_json_lines_skipped(self) -> None:
        """Debug/warning lines interleaved between valid JSON messages
        must be silently skipped."""

        async def _test() -> None:
            stream = MockTextReceiveStream(
                [
                    "[SandboxDebug] line 1\n",
                    "[SandboxDebug] line 2\n",
                    json.dumps({"type": "system", "subtype": "init"}) + "\n",
                    "WARNING: something\n",
                    json.dumps({"type": "result", "subtype": "success"}) + "\n",
                ]
            )

            transport = SubprocessCLITransport(prompt="test", options=make_options())
            transport._stdout_stream = stream
            transport._process = MagicMock()
            transport._process.wait = AsyncMock(return_value=0)

            messages: list[dict[str, Any]] = []
            async for msg in transport.read_messages():
                messages.append(msg)

            assert len(messages) == 2
            assert messages[0]["type"] == "system"
            assert messages[1]["type"] == "result"

        anyio.run(_test)

    def test_nonzero_exit_includes_captured_stderr(self) -> None:
        """ProcessError should surface the stderr emitted by the CLI."""

        async def _test() -> None:
            transport = SubprocessCLITransport(prompt="test", options=make_options())

            mock_process = MagicMock()
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=1)
            transport._process = mock_process
            transport._stdout_stream = MockTextReceiveStream([])
            transport._stderr_stream = MockTextReceiveStream(
                ["error: invalid --model alias", "hint: run claude --help"]
            )

            with pytest.raises(ProcessError) as exc_info:
                async for _ in transport.read_messages():
                    pass

            assert exc_info.value.exit_code == 1
            assert exc_info.value.stderr == (
                "error: invalid --model alias\nhint: run claude --help"
            )

        anyio.run(_test)

    def test_stderr_is_forwarded_to_sink_while_buffering(self) -> None:
        """Captured stderr should still be forwarded to the configured sink."""

        async def _test() -> None:
            sink = StringIO()
            transport = SubprocessCLITransport(
                prompt="test", options=make_options(debug_stderr=sink)
            )
            transport._stderr_stream = MockTextReceiveStream(["warning: deprecated flag"])

            await transport._handle_stderr()

            assert sink.getvalue() == "warning: deprecated flag\n"

        anyio.run(_test)

    def test_nonzero_exit_waits_for_live_stderr_reader(self) -> None:
        """ProcessError should include stderr captured by the background reader."""

        async def _test() -> None:
            import sys

            process = await anyio.open_process(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.stderr.write('error: invalid --model alias\\n'); "
                        "sys.stderr.write('hint: run claude --help\\n'); "
                        "sys.exit(1)"
                    ),
                ],
                stdout=PIPE,
                stderr=PIPE,
            )

            transport = SubprocessCLITransport(prompt="test", options=make_options())
            transport._process = process
            transport._stdout_stream = TextReceiveStream(process.stdout)
            transport._stderr_process_stream = process.stderr
            transport._stderr_stream = TextReceiveStream(process.stderr)
            transport._stderr_reader_finished = anyio.Event()
            transport._stderr_task_group = anyio.create_task_group()
            await transport._stderr_task_group.__aenter__()
            transport._stderr_task_group.start_soon(transport._handle_stderr)

            with pytest.raises(ProcessError) as exc_info:
                async for _ in transport.read_messages():
                    pass

            assert exc_info.value.exit_code == 1
            assert exc_info.value.stderr == (
                "error: invalid --model alias\nhint: run claude --help"
            )

            await transport.close()

        anyio.run(_test)
