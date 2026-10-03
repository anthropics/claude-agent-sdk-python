"""Regression tests for #1340: a hook that completes after Query.close().

``Query._close_impl`` cancels the control-request handler tasks but does not
wait for them, so a hook callback that swallows the cancellation finishes
after ``close()`` has closed the transport. Its success write then hit the
closed transport (``CLIConnectionError``), the broad ``except Exception``
mistook that for a handler failure and wrote a *second* ("error")
control_response to the same closed transport, and that second
``CLIConnectionError`` was never retrieved: on asyncio it landed in the loop
exception handler, on trio in the "Unhandled exception in detached trio
task" warning. The late response must instead be dropped and logged.

Every test here runs under both asyncio and trio (``anyio_backend`` in
conftest.py). No CLI or subprocess is involved.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio
import pytest

from claude_agent_sdk._errors import CLIConnectionError
from claude_agent_sdk._internal._task_compat import TaskHandle
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport import Transport

pytestmark = pytest.mark.anyio

_REQUEST_ID = "hook_req_1"

_HOOK_CONTROL_REQUEST: dict[str, Any] = {
    "type": "control_request",
    "request_id": _REQUEST_ID,
    "request": {
        "subtype": "hook_callback",
        "callback_id": "hook_0",
        "input": {"hook_event_name": "PreToolUse"},
        "tool_use_id": "tu_1",
    },
}


class SyntheticTransport(Transport):
    """In-memory transport that refuses writes once closed, like the real one.

    ``read_messages`` yields a single hook_callback control request and then
    parks until the Query's read task is cancelled by ``close()``. A write
    arriving after ``close()`` records the response subtype in ``late_writes``
    and raises the same ``CLIConnectionError`` the subprocess transport does.
    """

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []
        self.late_writes: list[str] = []
        self.closed = False

    async def connect(self) -> None:
        pass

    async def write(self, data: str) -> None:
        frame = json.loads(data)
        if self.closed:
            self.late_writes.append(frame["response"]["subtype"])
            raise CLIConnectionError("ProcessTransport is not ready for writing")
        self.writes.append(frame)

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        return self._read_messages_impl()

    async def _read_messages_impl(self) -> AsyncIterator[dict[str, Any]]:
        yield _HOOK_CONTROL_REQUEST
        await anyio.sleep_forever()

    async def close(self) -> None:
        self.closed = True

    def is_ready(self) -> bool:
        return not self.closed

    async def end_input(self) -> None:
        pass


async def _wait_for_handler_done(q: Query) -> None:
    """Wait until the control-request handler task has fully finished.

    The done callback pops the request id, so once it is gone the handler
    has returned (including any dropped-response logging) and its task
    exception, if any, is final.
    """
    with anyio.fail_after(5):
        while _REQUEST_ID in q._inflight_requests:
            await anyio.sleep(0.01)


async def test_late_hook_completion_after_close_writes_nothing(
    anyio_backend: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A hook that outlives close() must not answer on the closed transport."""
    caplog.set_level(logging.WARNING)
    loop_exceptions: list[type[BaseException]] = []
    loop = None
    previous_handler = None
    if anyio_backend == "asyncio":
        import asyncio

        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def handler(loop_: Any, context: dict[str, Any]) -> None:
            exc = context.get("exception")
            if exc is not None:
                loop_exceptions.append(type(exc))
            if previous_handler is not None:
                previous_handler(loop_, context)
            else:
                loop_.default_exception_handler(context)

        loop.set_exception_handler(handler)

    try:
        transport = SyntheticTransport()
        hook_started = anyio.Event()
        release = anyio.Event()
        hook_done = anyio.Event()

        async def adversarial_hook(input_data, tool_use_id, context):
            hook_started.set()
            try:
                await anyio.sleep_forever()
            except anyio.get_cancelled_exc_class():
                # A badly behaved hook swallows the cancellation close()
                # delivers and keeps running.
                pass
            # trio cancellation is level-triggered: without the shield every
            # await below would raise Cancelled again.
            with anyio.CancelScope(shield=True):
                await release.wait()
            hook_done.set()
            return {}

        q = Query(transport=transport, is_streaming_mode=True)
        q.hook_callbacks["hook_0"] = adversarial_hook

        await q.start()
        with anyio.fail_after(5):
            await hook_started.wait()

        # close() must stay bounded: it cancels the handler task but does not
        # wait for a hook that ignores the cancellation.
        with anyio.fail_after(5):
            await q.close()

        # Only now may the hook return; its handler then tries to answer on
        # the closed transport.
        release.set()
        with anyio.fail_after(5):
            await hook_done.wait()
            await _wait_for_handler_done(q)

        if anyio_backend == "asyncio":
            import gc

            # An exception left in the handler task only reaches the loop
            # exception handler once the task is garbage-collected.
            gc.collect()
            await anyio.sleep(0)

        # Before the fix this was ['success', 'error']: the success write hit
        # the closed transport and the CLIConnectionError fell through into
        # the error write, which hit it again.
        assert transport.late_writes != ["success", "error"]
        assert transport.late_writes == [], (
            f"no response may be written after close, got: {transport.late_writes}"
        )
        assert CLIConnectionError not in loop_exceptions
        assert not any(
            "Unhandled exception in detached trio task" in r.getMessage()
            for r in caplog.records
        ), "the dropped late response surfaced as an unretrieved task exception"

        # The dropped response is still diagnosed in the log.
        assert any(
            "Dropping" in r.getMessage() and _REQUEST_ID in r.getMessage()
            for r in caplog.records
        ), "the dropped late response must be logged"

        q.close_receive_stream()
    finally:
        if loop is not None:
            loop.set_exception_handler(previous_handler)


async def test_hook_business_error_still_writes_error_response() -> None:
    """While the transport is open, a hook that raises must still be answered
    with exactly one "error" control_response — the #1340 fix must not
    swallow business errors — and the exception must not escape the task."""
    transport = SyntheticTransport()
    hook_started = anyio.Event()

    async def failing_hook(input_data, tool_use_id, context):
        hook_started.set()
        raise ValueError("boom")

    q = Query(transport=transport, is_streaming_mode=True)
    q.hook_callbacks["hook_0"] = failing_hook

    # Capture the handler task handle at spawn time (the done callback pops
    # it from _inflight_requests, which can happen before we could look).
    handler_handles: list[TaskHandle] = []
    spawn_task = q.spawn_task

    def recording_spawn_task(coro: Any) -> TaskHandle:
        handle = spawn_task(coro)
        handler_handles.append(handle)
        return handle

    q.spawn_task = recording_spawn_task  # type: ignore[method-assign]

    await q.start()
    with anyio.fail_after(5):
        await hook_started.wait()
        while not handler_handles:
            await anyio.sleep(0.01)
        # Re-raises whatever the handler task raised; must return cleanly.
        await handler_handles[0].wait()
        await _wait_for_handler_done(q)

    responses = [f for f in transport.writes if f.get("type") == "control_response"]
    assert len(responses) == 1
    assert responses[0]["response"]["subtype"] == "error"
    assert responses[0]["response"]["request_id"] == _REQUEST_ID
    assert "boom" in responses[0]["response"]["error"]
    assert transport.late_writes == []

    await q.close()
    q.close_receive_stream()
