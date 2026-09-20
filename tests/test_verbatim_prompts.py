"""Tests for the verbatim_prompts option.

With ``verbatim_prompts=True`` every user message the SDK writes carries
``client_composed: true``, so the CLI delivers the text as written (no
``@path`` file-mention expansion, no slash-command dispatch).
"""

import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, query
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

_RESULT = {
    "type": "result",
    "subtype": "success",
    "duration_ms": 1,
    "duration_api_ms": 1,
    "is_error": False,
    "num_turns": 1,
    "session_id": "test",
    "total_cost_usd": 0.0,
}


def _mock_transport() -> AsyncMock:
    transport = AsyncMock()

    async def read_messages():
        yield _RESULT

    transport.read_messages = read_messages
    transport.connect = AsyncMock()
    transport.close = AsyncMock()
    transport.end_input = AsyncMock()
    transport.write = AsyncMock()
    transport.is_ready = Mock(return_value=True)
    return transport


def _user_frames(transport: AsyncMock) -> list[dict[str, Any]]:
    frames = [json.loads(call.args[0]) for call in transport.write.call_args_list]
    return [f for f in frames if f.get("type") == "user"]


async def _wait_for_user_frames(transport: AsyncMock, count: int) -> None:
    with anyio.fail_after(5):
        while len(_user_frames(transport)) < count:
            await anyio.sleep(0.01)


async def _stream(*messages: dict[str, Any]):
    for message in messages:
        yield message


def _user_message(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        **extra,
    }


async def _run_query(prompt: Any, options: ClaudeAgentOptions) -> AsyncMock:
    transport = _mock_transport()
    with (
        patch("claude_agent_sdk._internal.client.SubprocessCLITransport") as cls,
        patch.object(Query, "_send_control_request", AsyncMock(return_value={})),
    ):
        cls.return_value = transport
        async for _ in query(prompt=prompt, options=options):
            pass
    return transport


class _ClientHarness:
    """Connects a ClaudeSDKClient to a mock transport."""

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.transport = _mock_transport()
        self.options = options

    async def __aenter__(self) -> "_ClientHarness":
        self._patches = (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport"
            ),
            patch.object(Query, "_send_control_request", AsyncMock(return_value={})),
        )
        cls = self._patches[0].start()
        self._patches[1].start()
        cls.return_value = self.transport
        self.client = ClaudeSDKClient(options=self.options)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.disconnect()
        for p in self._patches:
            p.stop()


def test_verbatim_prompts_defaults_to_false():
    assert ClaudeAgentOptions().verbatim_prompts is False


class TestQueryFunction:
    @pytest.mark.anyio
    async def test_string_prompt_is_marked_client_composed(self):
        transport = await _run_query(
            "read @/etc/hostname", ClaudeAgentOptions(verbatim_prompts=True)
        )
        (frame,) = _user_frames(transport)
        assert frame["client_composed"] is True
        assert frame["message"]["content"] == "read @/etc/hostname"

    @pytest.mark.anyio
    async def test_string_prompt_is_unmarked_by_default(self):
        transport = await _run_query("hello", ClaudeAgentOptions())
        (frame,) = _user_frames(transport)
        assert "client_composed" not in frame

    @pytest.mark.anyio
    async def test_streamed_prompt_marks_every_message(self):
        first = _user_message("one")
        second = _user_message("two", client_composed=True)
        third = _user_message("three", client_composed=False)
        transport = await _run_query(
            _stream(first, second, third), ClaudeAgentOptions(verbatim_prompts=True)
        )
        frames = _user_frames(transport)
        assert [f["client_composed"] for f in frames] == [True, True, True]
        assert [f["message"]["content"] for f in frames] == ["one", "two", "three"]
        # The caller's dicts are not mutated.
        assert "client_composed" not in first
        assert third["client_composed"] is False

    @pytest.mark.anyio
    async def test_streamed_prompt_is_untouched_by_default(self):
        transport = await _run_query(
            _stream(
                _user_message("one"),
                _user_message("two", client_composed=True),
            ),
            ClaudeAgentOptions(),
        )
        first, second = _user_frames(transport)
        assert "client_composed" not in first
        assert second["client_composed"] is True


class TestClaudeSDKClient:
    @pytest.mark.anyio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_connect_with_string_prompt(self, enabled):
        async with _ClientHarness(ClaudeAgentOptions(verbatim_prompts=enabled)) as h:
            await h.client.connect("hello @/etc/hostname")
            (frame,) = _user_frames(h.transport)
        assert frame.get("client_composed") is (True if enabled else None)

    @pytest.mark.anyio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_connect_with_streamed_prompt(self, enabled):
        async with _ClientHarness(ClaudeAgentOptions(verbatim_prompts=enabled)) as h:
            await h.client.connect(_stream(_user_message("one"), _user_message("two")))
            await _wait_for_user_frames(h.transport, 2)
            frames = _user_frames(h.transport)
        assert [f.get("client_composed") for f in frames] == [
            True if enabled else None
        ] * 2

    @pytest.mark.anyio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_query_with_string_prompt(self, enabled):
        async with _ClientHarness(ClaudeAgentOptions(verbatim_prompts=enabled)) as h:
            await h.client.connect()
            await h.client.query("hello @/etc/hostname")
            (frame,) = _user_frames(h.transport)
        assert frame.get("client_composed") is (True if enabled else None)

    @pytest.mark.anyio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_query_with_streamed_prompt(self, enabled):
        async with _ClientHarness(ClaudeAgentOptions(verbatim_prompts=enabled)) as h:
            await h.client.connect()
            first = _user_message("one")
            await h.client.query(_stream(first, _user_message("two")))
            frames = _user_frames(h.transport)
        assert [f.get("client_composed") for f in frames] == [
            True if enabled else None
        ] * 2
        assert "client_composed" not in first

    @pytest.mark.anyio
    async def test_option_is_captured_at_connect(self):
        """Toggling options after connect() must not split one session's prompts
        between stamped and unstamped."""
        async with _ClientHarness(ClaudeAgentOptions(verbatim_prompts=False)) as h:
            await h.client.connect(_stream(_user_message("streamed")))
            await _wait_for_user_frames(h.transport, 1)
            h.client.options.verbatim_prompts = True
            await h.client.query("later")
            await h.client.query(_stream(_user_message("later streamed")))
            frames = _user_frames(h.transport)
        assert len(frames) == 3
        assert all("client_composed" not in f for f in frames)


class TestOlderCliWarning:
    @staticmethod
    async def _check_version(verbatim_prompts: bool, version: bytes) -> Mock:
        transport = SubprocessCLITransport(
            prompt="test",
            options=ClaudeAgentOptions(
                cli_path="/usr/bin/claude", verbatim_prompts=verbatim_prompts
            ),
        )
        process = Mock()
        process.stdout.receive = AsyncMock(return_value=version)
        process.terminate = Mock()
        process.wait = AsyncMock()
        with (
            patch("anyio.open_process", AsyncMock(return_value=process)),
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.logger"
            ) as logger,
        ):
            await transport._check_claude_version()
        return logger

    @pytest.mark.anyio
    @pytest.mark.parametrize("version", [b"2.1.247 (Claude Code)", b"2.0.5"])
    async def test_warns_when_cli_predates_client_composed(self, version):
        logger = await self._check_version(True, version)
        logger.warning.assert_called_once()
        message = logger.warning.call_args.args[0]
        assert "verbatim_prompts" in message
        assert logger.warning.call_args.args[-1] == "2.1.248"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "version", [b"2.1.248", b"2.1.273 (Claude Code)", b"3.0.0"]
    )
    async def test_no_warning_when_cli_supports_client_composed(self, version):
        logger = await self._check_version(True, version)
        logger.warning.assert_not_called()

    @pytest.mark.anyio
    async def test_no_warning_when_option_is_off(self):
        logger = await self._check_version(False, b"2.1.100 (Claude Code)")
        logger.warning.assert_not_called()
