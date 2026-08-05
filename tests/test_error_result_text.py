"""Regression tests for terminal `result` events with `is_error` (issue #1146).

When the agent loop completes but the final turn was an API error, the CLI emits
`{"type": "result", "subtype": "success", "is_error": true, "errors": [],
"result": "API Error: ..."}` and exits non-zero. The prose lives in `result`, so
falling back to `subtype` produced "error result: success", which contradicts
itself and discards the real message.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from claude_agent_sdk import ClaudeSDKError, ResultError
from claude_agent_sdk._errors import ProcessError
from claude_agent_sdk._internal.query import Query


class _StubTransport:
    """Minimal transport that replays a fixed set of CLI frames, then fails."""

    def __init__(self, frames: list[dict[str, Any]], exc: Exception | None):
        self._frames = frames
        self._exc = exc
        self.is_ready = True

    async def read_messages(self):
        for frame in self._frames:
            yield frame
        if self._exc is not None:
            raise self._exc

    async def write(self, data: str) -> None:  # pragma: no cover - unused here
        return None

    async def close(self) -> None:
        return None

    def end_input(self) -> None:
        return None


def _result_frame(**overrides: Any) -> dict[str, Any]:
    frame = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "errors": [],
        "result": "API Error: Stream idle timeout - no chunks received",
    }
    frame.update(overrides)
    return frame


async def _collect(frames: list[dict[str, Any]], exc: Exception | None):
    """Drive a Query over the stub transport and return (messages, raised)."""
    query = Query(transport=_StubTransport(frames, exc), is_streaming_mode=True)
    messages: list[dict[str, Any]] = []
    raised: BaseException | None = None
    async with anyio.create_task_group() as tg:
        tg.start_soon(query._read_messages)
        try:
            async for message in query.receive_messages():
                messages.append(message)
        except BaseException as exc_caught:  # noqa: BLE001 - the point of the test
            raised = exc_caught
        tg.cancel_scope.cancel()
    return messages, raised


def test_error_result_uses_result_text_not_subtype() -> None:
    """The raised error carries the CLI's prose, not the contradictory subtype."""
    _, raised = anyio.run(
        _collect, [_result_frame()], ProcessError("Command failed", exit_code=1)
    )

    assert raised is not None
    text = str(raised)
    assert "Stream idle timeout" in text, text
    # the bug: "Claude Code returned an error result: success"
    assert not text.endswith("success"), text


def test_error_result_raises_typed_error() -> None:
    """Callers can catch this as an SDK error rather than a bare Exception."""
    _, raised = anyio.run(
        _collect, [_result_frame()], ProcessError("Command failed", exit_code=1)
    )

    assert isinstance(raised, ResultError), type(raised)
    assert isinstance(raised, ClaudeSDKError)
    assert raised.subtype == "success"
    assert raised.errors == []
    assert raised.exit_code == 1


def test_populated_errors_list_still_wins() -> None:
    """When the CLI does fill `errors`, that stays the message."""
    frame = _result_frame(errors=["first failure", "second failure"])
    _, raised = anyio.run(
        _collect, [frame], ProcessError("Command failed", exit_code=1)
    )

    assert raised is not None
    assert "first failure; second failure" in str(raised)
    assert isinstance(raised, ResultError)
    assert raised.errors == ["first failure", "second failure"]


def test_error_subtype_used_when_no_prose_available() -> None:
    """With no errors and no result text, a real error subtype is still reported."""
    frame = _result_frame(subtype="error_during_execution", result=None)
    _, raised = anyio.run(
        _collect, [frame], ProcessError("Command failed", exit_code=1)
    )

    assert raised is not None
    assert "error_during_execution" in str(raised)


def test_non_error_subtype_never_becomes_the_message() -> None:
    """A success-ish subtype must not be presented as the error text."""
    frame = _result_frame(subtype="success", result=None)
    _, raised = anyio.run(
        _collect, [frame], ProcessError("Command failed", exit_code=1)
    )

    assert raised is not None
    assert "success" not in str(raised), str(raised)
    assert "unknown error" in str(raised)


def test_successful_result_does_not_raise() -> None:
    """A clean run is unaffected."""
    frame = _result_frame(is_error=False, result="all good")
    messages, raised = anyio.run(_collect, [frame], None)

    assert raised is None
    assert any(m.get("type") == "result" for m in messages)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
