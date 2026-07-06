"""Tests for subprocess transport buffering edge cases."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from claude_agent_sdk._errors import CLIJSONDecodeError
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

    def _collect(self, chunks: list[str], **opts: object) -> list[Any]:
        """Drive read_messages() over a canned chunk stream and return messages."""
        messages: list[Any] = []

        async def _run() -> None:
            transport = SubprocessCLITransport(prompt="t", options=make_options(**opts))
            transport._process = MagicMock()
            transport._process.wait = AsyncMock(return_value=0)
            transport._stdout_stream = MockTextReceiveStream(chunks)
            transport._stderr_stream = MockTextReceiveStream([])
            async for m in transport.read_messages():
                messages.append(m)

        anyio.run(_run)
        return messages

    @pytest.mark.parametrize(
        "ws",
        [" ", "  ", "\xa0", "\u2009", "\u3000"],
        ids=["space", "two-spaces", "nbsp", "thin-space", "ideographic-space"],
    )
    def test_whitespace_at_chunk_boundary_preserved(self, ws: str) -> None:
        """Whitespace inside a JSON string value that lands on the boundary
        between two TextReceiveStream chunks must not be stripped.

        The reader used to ``.strip()`` each chunk before accumulating it, so a
        64KiB read boundary falling inside a string value silently ate the
        whitespace there. ``JSON.stringify`` escapes tab/newline/CR but passes
        U+0020 and Unicode spaces through raw, and ``str.strip()`` eats them all.
        """
        content = "A" * 1000 + ws + "B" * 1000
        # ensure_ascii=False so the whitespace goes on the wire raw, and compact
        # separators so the only whitespace on the line is the one under test.
        line = json.dumps(
            {"type": "assistant", "data": content},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        ws_at = line.index(ws)
        # Split the line exactly at the far edge of the whitespace run, the way
        # a 64KiB read boundary would.
        chunks = [line[: ws_at + len(ws)], line[ws_at + len(ws) :] + "\n"]
        assert chunks[0].endswith(ws)

        messages = self._collect(chunks)

        assert len(messages) == 1
        assert messages[0]["data"] == content, (
            f"lost {ws!r} at a chunk boundary — strip() was applied to a chunk, "
            f"not to a complete line"
        )

    def test_whitespace_preserved_across_realistic_64kib_chunking(self) -> None:
        """A long whitespace run spanning several 64KiB reads must survive intact.

        Whether the old code corrupted a payload depended on whether a read
        boundary happened to land on whitespace, so a prose payload only fails
        some of the time. Use a value that is almost entirely spaces: then every
        interior boundary is guaranteed to land inside the run, and the old
        reader stripped whole chunks away (losing ~400k characters).
        """
        content = "S" + " " * 400_000 + "E"
        line = (
            json.dumps({"type": "assistant", "data": content}, separators=(",", ":"))
            + "\n"
        )
        chunks = [line[i : i + 65536] for i in range(0, len(line), 65536)]
        assert len(chunks) > 6

        # Every interior chunk boundary lands inside the whitespace run, so
        # str.strip() on a chunk is guaranteed to eat characters.
        for boundary in range(65536, len(line), 65536):
            assert line[boundary - 1] == " " and line[boundary] == " "

        messages = self._collect(chunks)

        assert len(messages) == 1
        assert messages[0]["data"] == content

    def test_complete_oversized_line_still_raises(self) -> None:
        """The buffer-size guard bounds a whole message, not just the tail left
        over after complete lines are split off — an oversized line that arrives
        with its trailing newline must still be rejected."""
        limit = 512
        line = json.dumps({"type": "x", "data": "y" * (limit + 100)}) + "\n"

        with pytest.raises(CLIJSONDecodeError) as exc_info:
            self._collect([line], max_buffer_size=limit)

        assert f"maximum buffer size of {limit} bytes" in str(exc_info.value)

    def test_malformed_complete_line_raises(self) -> None:
        """A complete line that looks like JSON but doesn't parse is corrupt —
        no later data can complete it — so it surfaces as CLIJSONDecodeError
        rather than vanishing."""
        chunks = ['{"type":"assistant","broken\n{"type":"result"}\n']

        with pytest.raises(CLIJSONDecodeError):
            self._collect(chunks)

    def test_non_json_line_still_skipped(self) -> None:
        """Non-JSON stdout noise (#347) is skipped, not raised on."""
        chunks = [
            "[SandboxDebug] starting up\n"
            + json.dumps({"type": "result"})
            + "\n"
            + "plain text trailer\n"
        ]

        messages = self._collect(chunks)

        assert messages == [{"type": "result"}]

    def test_final_message_without_trailing_newline_is_yielded(self) -> None:
        """A last message with no trailing newline must still be delivered when
        the stream ends, including when it ends by closing the resource."""

        messages: list[Any] = []

        async def _run() -> None:
            class ClosingStream:
                def __init__(self, chunks: list[str]) -> None:
                    self.chunks = chunks
                    self.index = 0

                def __aiter__(self) -> Any:
                    return self

                async def __anext__(self) -> str:
                    if self.index >= len(self.chunks):
                        # The subprocess went away mid-stream rather than the
                        # iterator ending cleanly.
                        raise anyio.ClosedResourceError
                    chunk = self.chunks[self.index]
                    self.index += 1
                    return chunk

            transport = SubprocessCLITransport(prompt="t", options=make_options())
            transport._process = MagicMock()
            transport._process.wait = AsyncMock(return_value=0)
            transport._stdout_stream = ClosingStream(
                [json.dumps({"type": "result", "subtype": "success"})]
            )
            transport._stderr_stream = MockTextReceiveStream([])
            async for m in transport.read_messages():
                messages.append(m)

        anyio.run(_run)

        assert messages == [{"type": "result", "subtype": "success"}]

    def test_truncated_final_line_is_dropped_not_raised(self) -> None:
        """A residual tail cut off mid-write is unrecoverable; drop it quietly
        rather than raising on the way out of a stream that already ended."""
        chunks = [json.dumps({"type": "result"}) + "\n" + '{"type":"assist']

        messages = self._collect(chunks)

        assert messages == [{"type": "result"}]
