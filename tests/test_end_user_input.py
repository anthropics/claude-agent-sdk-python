"""Tests for the ``end_user_input`` declaration and the folded run result (#1190).

``query()`` with hooks, ``can_use_tool`` or SDK MCP servers holds stdin open so
the CLI can ask the SDK questions while it works. The SDK used to close stdin at
the first result with no tracked subagent in flight, which drops the control
responses of a continuation turn that a just-settled subagent wakes ("Stream
closed"). A CLI that honours the ``end_user_input`` control request ends the run
itself instead: stdin stays open until stdout closes, and the per-turn results
are folded into the one ``ResultMessage`` a string prompt has always yielded.

The fake CLI below plays the CLI's side of that contract, including the parts
the SDK must survive: builds that answer the request with an error, and builds
too old to answer it at all.
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    ResultMessage,
    SystemMessage,
    query,
)
from claude_agent_sdk._errors import ProcessError, ResultError
from claude_agent_sdk._internal.query import Query

pytestmark = pytest.mark.anyio


def _assistant(text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "model": "claude-haiku",
        },
    }


def _result(text: str = "done", **overrides: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "duration_ms": 100,
        "duration_api_ms": 80,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s1",
        "result": text,
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    frame.update(overrides)
    return frame


def _notice(subtype: str) -> dict[str, Any]:
    return {"type": "system", "subtype": subtype}


def _permission_request(request_id: str = "perm_1") -> dict[str, Any]:
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "Write",
            "input": {"file_path": "/tmp/x", "content": "hi"},
            "tool_use_id": "toolu_1",
        },
    }


def _is_declaration(frame: dict[str, Any]) -> bool:
    return (
        frame.get("type") == "control_request"
        and frame["request"]["subtype"] == "end_user_input"
    )


def _is_user(frame: dict[str, Any]) -> bool:
    return frame.get("type") == "user"


def _is_response_to(request_id: str) -> Callable[[dict[str, Any]], bool]:
    return lambda f: (
        f.get("type") == "control_response"
        and f["response"]["request_id"] == request_id
    )


def _ack(declaration: dict[str, Any], *, honoured: bool = True) -> dict[str, Any]:
    response: dict[str, Any] = {"request_id": declaration["request_id"]}
    if honoured:
        response.update(subtype="success", response={})
    else:
        response.update(
            subtype="error",
            error="Unsupported control request subtype: end_user_input",
        )
    return {"type": "control_response", "response": response}


class FakeCli:
    """The CLI's side of the wire: records what the SDK writes to stdin and
    plays a script of stdout frames. Writing after ``end_input()`` fails, like a
    closed pipe."""

    def __init__(self, script: Callable[["FakeCli"], AsyncIterator[dict[str, Any]]]):
        self._script = script
        self.frames: list[dict[str, Any]] = []
        self.ended = anyio.Event()
        self.transport = AsyncMock()
        self.transport.connect = AsyncMock()
        self.transport.close = AsyncMock()
        self.transport.is_ready = Mock(return_value=True)
        self.transport.write = self._write
        self.transport.end_input = self._end_input
        self.transport.read_messages = lambda: self._script(self)

    async def _write(self, data: str) -> None:
        if self.ended.is_set():
            raise RuntimeError("stdin closed")
        self.frames.append(json.loads(data))

    async def _end_input(self) -> None:
        self.ended.set()

    async def written(self, predicate: Callable[[dict[str, Any]], bool]) -> dict:
        """Wait until the SDK has written a frame matching ``predicate``."""
        with anyio.fail_after(5):
            while True:
                for frame in self.frames:
                    if predicate(frame):
                        return frame
                await anyio.sleep(0.001)

    async def stdin_closed(self) -> None:
        with anyio.fail_after(5):
            await self.ended.wait()

    @property
    def declarations(self) -> list[dict[str, Any]]:
        return [f for f in self.frames if _is_declaration(f)]


async def _run(
    cli: FakeCli,
    prompt: Any = "go",
    options: ClaudeAgentOptions | None = None,
) -> list[Any]:
    with (
        patch("claude_agent_sdk._internal.client.SubprocessCLITransport") as mock_cls,
        patch(
            "claude_agent_sdk._internal.query.Query.initialize",
            new_callable=AsyncMock,
        ),
    ):
        mock_cls.return_value = cli.transport
        if options is None:
            options = _with_permissions()
        return [m async for m in query(prompt=prompt, options=options)]


def _with_permissions(calls: list[str] | None = None) -> ClaudeAgentOptions:
    """Options with a ``can_use_tool`` callback: stdin must stay open for it."""

    async def allow(tool_name, tool_input, context):
        if calls is not None:
            calls.append(tool_name)
        return PermissionResultAllow()

    return ClaudeAgentOptions(can_use_tool=allow)


async def _one_message(text: str = "go") -> AsyncIterator[dict[str, Any]]:
    yield {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        "session_id": "default",
    }


async def _two_messages() -> AsyncIterator[dict[str, Any]]:
    async for m in _one_message("one"):
        yield m
    async for m in _one_message("two"):
        yield m


async def _no_messages() -> AsyncIterator[dict[str, Any]]:
    return
    yield  # pragma: no cover


class TestStringPrompt:
    async def test_declares_end_of_input_before_the_prompt(self):
        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            await cli.written(_is_user)
            yield _assistant("hi")
            yield _result("hi")

        cli = FakeCli(script)
        messages = await _run(cli)

        assert [f["type"] for f in cli.frames] == ["control_request", "user"]
        declaration = cli.frames[0]
        assert declaration["request"] == {"subtype": "end_user_input"}
        assert declaration["request_id"].startswith("req_")
        assert isinstance(messages[0], AssistantMessage)
        assert isinstance(messages[1], ResultMessage)

    async def test_continuation_turn_is_served_after_a_subagent_settles_before_the_result(
        self,
    ):
        """#1190: the subagent goes terminal just before the turn's result, so
        the in-flight ledger is empty at that result and used to close stdin.
        The continuation turn it wakes then asks for a permission verdict."""
        calls: list[str] = []
        stdin_open_when_answered: list[bool] = []

        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            await cli.written(_is_user)
            yield {"type": "system", "subtype": "task_started", "task_id": "t1",
                   "task_type": "local_agent", "description": "d",
                   "uuid": "u1", "session_id": "s1"}  # fmt: skip
            yield {"type": "system", "subtype": "task_notification",
                   "task_id": "t1", "status": "completed", "output_file": "",
                   "summary": "", "uuid": "u2", "session_id": "s1"}  # fmt: skip
            yield _assistant("delegated")
            yield _result("delegated", total_cost_usd=0.01)
            # The completion wakes the parent for a follow-up turn.
            yield _permission_request()
            await cli.written(_is_response_to("perm_1"))
            stdin_open_when_answered.append(not cli.ended.is_set())
            yield _assistant("all done")
            yield _result(
                "all done",
                total_cost_usd=0.03,
                usage={"input_tokens": 7, "output_tokens": 3},
                num_turns=2,
                duration_ms=50,
            )

        cli = FakeCli(script)
        messages = await _run(cli, options=_with_permissions(calls))

        assert calls == ["Write"]
        assert stdin_open_when_answered == [True]
        results = [m for m in messages if isinstance(m, ResultMessage)]
        assert len(results) == 1
        (final,) = results
        assert final.result == "all done"
        assert final.total_cost_usd == 0.03
        assert final.usage == {"input_tokens": 17, "output_tokens": 8}
        assert final.num_turns == 3
        assert final.duration_ms == 150
        assert final.turn_results is not None
        assert [t.result for t in final.turn_results] == ["delegated", "all done"]
        assert messages[-1] is final

    async def test_single_turn_result_is_yielded_untouched(self):
        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            await cli.written(_is_user)
            yield _result("only")

        messages = await _run(FakeCli(script))

        (result,) = messages
        assert result.result == "only"
        assert result.turn_results is None

    async def test_first_failed_turn_speaks_for_the_run(self):
        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            await cli.written(_is_user)
            yield _result(
                subtype="error_max_turns",
                is_error=True,
                errors=["turn limit"],
                result=None,
            )
            yield _result("recovered")

        messages = await _run(FakeCli(script))

        (final,) = messages
        assert final.subtype == "error_max_turns"
        assert final.is_error is True
        assert final.errors == ["turn limit"]
        assert [t.subtype for t in final.turn_results] == [
            "error_max_turns",
            "success",
        ]

    async def test_notices_are_delivered_live_while_a_result_is_held(self):
        """Progress from a background subagent must not wait for the fold."""

        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            await cli.written(_is_user)
            yield _assistant("first")
            yield _result("first")
            yield _notice("notice_between_turns")
            live_seen.set()
            await release.wait()
            yield _assistant("second")
            yield _result("second")

        live_seen = anyio.Event()
        release = anyio.Event()
        cli = FakeCli(script)
        seen: list[Any] = []
        with (
            patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_cls,
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = cli.transport
            with anyio.fail_after(5):
                async for m in query(prompt="go", options=_with_permissions()):
                    seen.append(m)
                    if (
                        isinstance(m, SystemMessage)
                        and m.subtype == "notice_between_turns"
                    ):
                        # Delivered before the run ended, with the result
                        # still held back.
                        assert not any(isinstance(x, ResultMessage) for x in seen)
                        release.set()

        assert [type(m).__name__ for m in seen] == [
            "AssistantMessage",
            "SystemMessage",
            "AssistantMessage",
            "ResultMessage",
        ]

    async def test_session_state_marker_stays_behind_the_result_it_follows(self):
        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            await cli.written(_is_user)
            yield _assistant("first")
            yield _result("first")
            yield _notice("session_state_changed")
            yield _assistant("second")
            yield _result("second")
            yield _notice("session_state_changed")

        messages = await _run(FakeCli(script))

        shape = [
            m.subtype if isinstance(m, SystemMessage) else type(m).__name__
            for m in messages
        ]
        assert shape == [
            "AssistantMessage",
            "session_state_changed",
            "AssistantMessage",
            "ResultMessage",
            "session_state_changed",
        ]

    async def test_results_reach_the_consumer_ahead_of_the_error_the_exit_becomes(
        self,
    ):
        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            await cli.written(_is_user)
            yield _result("ok")
            yield _result(
                subtype="error_during_execution",
                is_error=True,
                errors=["boom"],
                result=None,
            )
            raise ProcessError("exit 1", exit_code=1)

        cli = FakeCli(script)
        seen: list[Any] = []
        with (
            pytest.raises(ResultError),
            patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_cls,
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = cli.transport
            async for m in query(prompt="go", options=_with_permissions()):
                seen.append(m)

        (final,) = seen
        assert final.subtype == "error_during_execution"
        assert len(final.turn_results) == 2


class TestOlderClis:
    """A CLI that does not honour the declaration keeps the old contract: every
    result is yielded as it arrives and stdin closes at the first result with no
    tracked subagent in flight."""

    async def test_error_reply_closes_stdin_at_the_first_result(self):
        stdin_closed: list[bool] = []

        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _ack(decl, honoured=False)
            await cli.written(_is_user)
            yield _result("first")
            await cli.stdin_closed()
            stdin_closed.append(True)
            yield _result("second")

        messages = await _run(FakeCli(script), options=_with_permissions())

        assert stdin_closed == [True]
        assert [m.result for m in messages] == ["first", "second"]
        assert all(m.turn_results is None for m in messages)

    async def test_unanswered_declaration_does_not_stall_the_run(self):
        """A CLI older than 2.1.40 drops the request silently."""

        async def script(cli):
            await cli.written(_is_declaration)
            await cli.written(_is_user)
            yield _result("first")
            await cli.stdin_closed()
            yield _result("second")

        messages = await _run(FakeCli(script), options=_with_permissions())

        assert [m.result for m in messages] == ["first", "second"]

    async def test_a_result_ahead_of_the_reply_decides_the_run(self):
        """A resumed session's startup notice is a result that precedes the
        reply. Stdin has closed by the old rule by then, so a success reply
        arriving afterwards must not turn the run into one that folds."""

        async def script(cli):
            decl = await cli.written(_is_declaration)
            await cli.written(_is_user)
            yield _result("startup")
            await cli.stdin_closed()
            yield _ack(decl)
            yield _result("prompt")

        messages = await _run(FakeCli(script), options=_with_permissions())

        assert [m.result for m in messages] == ["startup", "prompt"]
        assert all(m.turn_results is None for m in messages)

    async def test_no_bidirectional_needs_declares_nothing_and_changes_nothing(self):
        """With nothing for an open stdin to serve, the run is what it always
        was: no declaration, stdin closed at once, every result as it arrives."""

        async def script(cli):
            await cli.written(_is_user)
            await cli.stdin_closed()
            yield _result("first")
            yield _result("second")

        cli = FakeCli(script)
        messages = await _run(cli, options=ClaudeAgentOptions())

        assert cli.declarations == []
        assert [m.result for m in messages] == ["first", "second"]
        assert all(m.turn_results is None for m in messages)


class TestStreamedPrompt:
    """An async-iterable prompt yields every result unfolded, as before, but a
    single-message stream declares the end of input too, so its continuation
    turns keep a stdin to answer on."""

    async def test_single_message_stream_declares_and_keeps_stdin_open(self):
        stdin_open_when_answered: list[bool] = []

        async def script(cli):
            await cli.written(_is_user)
            decl = await cli.written(_is_declaration)
            yield _ack(decl)
            yield _result("first")
            yield _permission_request()
            await cli.written(_is_response_to("perm_1"))
            stdin_open_when_answered.append(not cli.ended.is_set())
            yield _result("second")

        cli = FakeCli(script)
        messages = await _run(cli, prompt=_one_message(), options=_with_permissions())

        assert [m.result for m in messages] == ["first", "second"]
        assert stdin_open_when_answered == [True]
        assert not cli.ended.is_set()

    async def test_declaration_follows_the_message(self):
        async def script(cli):
            await cli.written(_is_declaration)
            yield _result("done")

        cli = FakeCli(script)
        await _run(cli, prompt=_one_message())

        assert [f["type"] for f in cli.frames] == ["user", "control_request"]

    async def test_error_reply_closes_stdin_at_the_first_result(self):
        async def script(cli):
            await cli.written(_is_user)
            decl = await cli.written(_is_declaration)
            yield _ack(decl, honoured=False)
            yield _result("first")
            await cli.stdin_closed()
            yield _result("second")

        messages = await _run(
            FakeCli(script), prompt=_one_message(), options=_with_permissions()
        )

        assert [m.result for m in messages] == ["first", "second"]

    async def test_unanswered_declaration_stops_waiting_after_the_bound(self):
        async def script(cli):
            decl = await cli.written(_is_declaration)
            # The reply comes after the bound ran out: too late to matter, so
            # the run keeps the old rule and stdin closes at the result.
            await anyio.sleep(0.3)
            yield _ack(decl)
            yield _result("done")
            await cli.stdin_closed()

        with patch.object(Query, "end_user_input_ack_timeout", 0.05):
            messages = await _run(
                FakeCli(script), prompt=_one_message(), options=_with_permissions()
            )

        assert [m.result for m in messages] == ["done"]

    async def test_a_result_ahead_of_the_reply_ends_the_wait(self):
        async def script(cli):
            decl = await cli.written(_is_declaration)
            yield _result("first")
            await cli.stdin_closed()
            yield _ack(decl)
            yield _result("second")

        with patch.object(Query, "end_user_input_ack_timeout", 60):
            messages = await _run(
                FakeCli(script), prompt=_one_message(), options=_with_permissions()
            )

        assert [m.result for m in messages] == ["first", "second"]

    @pytest.mark.parametrize("stream", [_two_messages, _no_messages])
    async def test_streams_that_are_not_a_single_message_never_declare(self, stream):
        async def script(cli):
            if stream is _two_messages:
                await cli.written(lambda f: _is_user(f) and "two" in json.dumps(f))
                yield _result("done")
                await cli.stdin_closed()
            else:
                await cli.stdin_closed()
                yield _result("done")

        cli = FakeCli(script)
        messages = await _run(cli, prompt=stream())

        assert cli.declarations == []
        assert [m.result for m in messages] == ["done"]

    async def test_a_failing_stream_never_declares(self):
        async def failing() -> AsyncIterator[dict[str, Any]]:
            async for m in _one_message():
                yield m
            raise RuntimeError("prompt source broke")

        async def script(cli):
            await cli.written(_is_user)
            yield _result("done")
            await cli.stdin_closed()

        cli = FakeCli(script)
        await _run(cli, prompt=failing())

        assert cli.declarations == []


class TestClaudeSDKClient:
    async def test_never_declares_end_of_input(self):
        """A client session takes further prompts, so nothing may tell the CLI
        the input is over."""

        async def script(cli):
            await cli.written(_is_user)
            yield _assistant("hi")
            yield _result("hi")
            await cli.stdin_closed()

        cli = FakeCli(script)
        with patch(
            "claude_agent_sdk._internal.query.Query.initialize",
            new_callable=AsyncMock,
        ):
            client = ClaudeSDKClient(
                options=_with_permissions(), transport=cli.transport
            )
            await client.connect()
            await client.query("hi")
            got = [m async for m in client.receive_response()]
            await client.disconnect()

        assert cli.declarations == []
        assert isinstance(got[-1], ResultMessage)
        assert got[-1].turn_results is None
