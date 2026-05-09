"""Backend-agnostic detached task spawning.

``Query`` manages background tasks (the read loop, ``stream_input``,
control-request handlers) that must be cancellable from any task context
— including async-generator finalizers, which Python may run in a
different task than the one that called ``start()``. anyio's
``TaskGroup`` cannot be used for this because its cancel scope has task
affinity: exiting it from a different task either raises ``RuntimeError:
Attempted to exit cancel scope in a different task than it was entered
in`` or busy-spins in ``_deliver_cancellation`` on the asyncio backend.

Under asyncio this is solved with plain ``loop.create_task()``, but that
raises ``RuntimeError: no running event loop`` under trio. This module
provides ``spawn_detached()`` which dispatches via sniffio to the
appropriate backend primitive, returning a uniform ``TaskHandle``.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any

import sniffio

logger = logging.getLogger(__name__)


class TaskHandle:
    """Backend-agnostic handle to a detached background task.

    Safe to ``.cancel()`` from any task — no anyio cancel-scope task
    affinity.
    """

    def cancel(self) -> None:
        """Request cancellation of the wrapped task."""
        raise NotImplementedError

    def done(self) -> bool:
        """Return True if the wrapped task has finished."""
        raise NotImplementedError

    def add_done_callback(self, callback: Callable[[TaskHandle], None]) -> None:
        """Register ``callback(self)`` to run when the task finishes."""
        raise NotImplementedError

    async def wait(self) -> None:
        """Wait for the task to finish.

        Suppresses the backend's cancellation exception (the task was
        cancelled by us) but re-raises any other exception the task
        raised.
        """
        raise NotImplementedError


class _AsyncioTaskHandle(TaskHandle):
    """Thin wrapper around ``asyncio.Task``."""

    def __init__(self, task: Any) -> None:
        self._task = task

    def cancel(self) -> None:
        self._task.cancel()

    def done(self) -> bool:
        return bool(self._task.done())

    def add_done_callback(self, callback: Callable[[TaskHandle], None]) -> None:
        self._task.add_done_callback(lambda _t: callback(self))

    async def wait(self) -> None:
        import asyncio

        with suppress(asyncio.CancelledError):
            await self._task


class _TrioTaskHandle(TaskHandle):
    """Wraps a trio system task with its own ``CancelScope``."""

    def __init__(self) -> None:
        import trio

        self._cancel_scope = trio.CancelScope()
        self._done_event = trio.Event()
        self._exception: BaseException | None = None
        self._callbacks: list[Callable[[TaskHandle], None]] = []

    def cancel(self) -> None:
        # CancelScope.cancel() is sync and safe to call from any task.
        self._cancel_scope.cancel()

    def done(self) -> bool:
        return self._done_event.is_set()

    def add_done_callback(self, callback: Callable[[TaskHandle], None]) -> None:
        if self.done():
            callback(self)
        else:
            self._callbacks.append(callback)

    def _mark_done(self, exc: BaseException | None) -> None:
        import trio

        # Parity with asyncio's "Task exception was never retrieved":
        # close() only .cancel()s child tasks (never .wait()s them), so a
        # non-Cancelled exception would otherwise be silently dropped.
        if exc is not None and not isinstance(exc, trio.Cancelled):
            logger.warning("Unhandled exception in detached trio task", exc_info=exc)
        self._exception = exc
        self._done_event.set()
        for cb in self._callbacks:
            # Suppress BaseException so a misbehaving callback can never
            # propagate out of the system-task _runner (which would crash
            # trio with TrioInternalError). The actual callbacks used here
            # are set.discard / dict.pop, so this is purely defensive.
            with suppress(BaseException):
                cb(self)
        self._callbacks.clear()

    async def wait(self) -> None:
        import trio

        await self._done_event.wait()
        if self._exception is not None and not isinstance(
            self._exception, trio.Cancelled
        ):
            raise self._exception


def spawn_detached(coro: Coroutine[Any, Any, Any]) -> TaskHandle:
    """Spawn ``coro`` as a detached background task on the current backend.

    - **asyncio**: ``asyncio.get_running_loop().create_task(coro)``.
    - **trio**: ``trio.lowlevel.spawn_system_task`` wrapping ``coro`` in a
      per-task ``CancelScope`` so the handle supports ``.cancel()``.
    """
    backend = sniffio.current_async_library()
    if backend == "asyncio":
        import asyncio

        loop = asyncio.get_running_loop()
        return _AsyncioTaskHandle(loop.create_task(coro))
    if backend == "trio":
        import trio

        handle = _TrioTaskHandle()

        async def _runner() -> None:
            exc: BaseException | None = None
            try:
                with handle._cancel_scope:
                    await coro
            except BaseException as e:  # noqa: BLE001
                # System tasks must not raise (would crash trio). Store
                # the exception on the handle; ``.wait()`` re-raises it.
                exc = e
            finally:
                handle._mark_done(exc)

        # Pass context= so trio system tasks inherit the caller's
        # contextvars (asyncio's loop.create_task() does this implicitly;
        # spawn_system_task does not).
        trio.lowlevel.spawn_system_task(_runner, context=contextvars.copy_context())
        return handle
    # Unsupported backend: close the coroutine so we don't leak a "coroutine
    # was never awaited" RuntimeWarning on top of the RuntimeError.
    coro.close()
    raise RuntimeError(
        f"Unsupported async backend: {backend!r}. "
        "claude_agent_sdk requires asyncio or trio."
    )
