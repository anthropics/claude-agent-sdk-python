"""Tests for the backend-agnostic detached task spawner."""

import anyio
import pytest

from claude_agent_sdk._internal._task_compat import spawn_detached


class TestSpawnAndWait:
    def test_spawn_and_wait_asyncio(self):
        async def _test():
            flag = {"set": False}

            async def coro():
                flag["set"] = True

            handle = spawn_detached(coro())
            await handle.wait()
            assert flag["set"] is True
            assert handle.done() is True

        anyio.run(_test, backend="asyncio")

    def test_spawn_and_wait_trio(self):
        async def _test():
            flag = {"set": False}

            async def coro():
                flag["set"] = True

            handle = spawn_detached(coro())
            await handle.wait()
            assert flag["set"] is True
            assert handle.done() is True

        anyio.run(_test, backend="trio")


class TestCancel:
    def test_cancel_asyncio(self):
        async def _test():
            async def coro():
                await anyio.sleep(3600)

            handle = spawn_detached(coro())
            await anyio.sleep(0)  # let it start
            handle.cancel()
            await handle.wait()
            assert handle.done() is True

        anyio.run(_test, backend="asyncio")

    def test_cancel_trio(self):
        async def _test():
            async def coro():
                await anyio.sleep(3600)

            handle = spawn_detached(coro())
            await anyio.sleep(0)  # let it start
            handle.cancel()
            await handle.wait()
            assert handle.done() is True

        anyio.run(_test, backend="trio")


class TestDoneCallback:
    def test_done_callback_asyncio(self):
        async def _test():
            fired_with = []

            async def coro():
                pass

            handle = spawn_detached(coro())
            handle.add_done_callback(fired_with.append)
            await handle.wait()
            await anyio.sleep(0)  # let callback fire
            assert fired_with == [handle]

        anyio.run(_test, backend="asyncio")

    def test_done_callback_trio(self):
        async def _test():
            fired_with = []

            async def coro():
                pass

            handle = spawn_detached(coro())
            handle.add_done_callback(fired_with.append)
            await handle.wait()
            assert fired_with == [handle]

        anyio.run(_test, backend="trio")


class TestExceptionPropagation:
    def test_exception_propagates_via_wait_asyncio(self):
        async def _test():
            async def coro():
                raise ValueError("boom")

            handle = spawn_detached(coro())
            with pytest.raises(ValueError, match="boom"):
                await handle.wait()

        anyio.run(_test, backend="asyncio")

    def test_exception_propagates_via_wait_trio(self):
        async def _test():
            async def coro():
                raise ValueError("boom")

            handle = spawn_detached(coro())
            with pytest.raises(ValueError, match="boom"):
                await handle.wait()

        anyio.run(_test, backend="trio")

    def test_unhandled_exception_logged_under_trio(self, caplog):
        """A non-Cancelled exception with no .wait() must still be logged.

        Parity with asyncio's "Task exception was never retrieved": child
        tasks that are only ``.cancel()``ed (never ``.wait()``ed) would
        otherwise drop the exception silently under trio.
        """
        import logging

        async def _test():
            async def coro():
                raise ValueError("boom")

            spawn_detached(coro())  # NB: no .wait()
            await anyio.sleep(0)  # let it run

        with caplog.at_level(
            logging.WARNING, logger="claude_agent_sdk._internal._task_compat"
        ):
            anyio.run(_test, backend="trio")

        assert any(
            "Unhandled exception in detached trio task" in r.message
            for r in caplog.records
        ), f"expected warning log, got: {[r.message for r in caplog.records]}"
        assert any(
            r.exc_info and isinstance(r.exc_info[1], ValueError) for r in caplog.records
        )


class TestContextVarPropagation:
    """Spawned tasks must see the caller's contextvars on both backends.

    asyncio's ``loop.create_task()`` copies the current context implicitly;
    trio's ``spawn_system_task`` does not unless ``context=`` is passed.
    """

    @staticmethod
    def _run(backend: str) -> str:
        import contextvars

        cv: contextvars.ContextVar[str] = contextvars.ContextVar(
            "cv", default="DEFAULT"
        )
        seen: list[str] = []

        async def _test():
            cv.set("PARENT")

            async def coro():
                seen.append(cv.get())

            handle = spawn_detached(coro())
            await handle.wait()

        anyio.run(_test, backend=backend)
        return seen[0]

    def test_contextvar_propagates_asyncio(self):
        assert self._run("asyncio") == "PARENT"

    def test_contextvar_propagates_trio(self):
        assert self._run("trio") == "PARENT"


class TestCrossTaskCancel:
    def test_cancel_from_different_task_trio(self):
        """Cancelling from a different task than the spawner must not raise.

        This is the trio-side equivalent of the cross-task-cancel invariant.
        """

        async def _test():
            async def coro():
                await anyio.sleep(3600)

            handle = spawn_detached(coro())
            await anyio.sleep(0)
            cancel_error = []

            async def cancel_in_other_task():
                try:
                    handle.cancel()
                    await handle.wait()
                except Exception as e:  # pragma: no cover - failure path
                    cancel_error.append(e)

            async with anyio.create_task_group() as tg:
                tg.start_soon(cancel_in_other_task)

            assert cancel_error == [], f"cancel raised: {cancel_error}"
            assert handle.done() is True

        anyio.run(_test, backend="trio")
