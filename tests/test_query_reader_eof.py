"""Regression tests for control requests during Query reader shutdown."""

from unittest.mock import AsyncMock, Mock

import anyio
import pytest

from claude_agent_sdk._errors import CLIConnectionError
from claude_agent_sdk._internal.query import Query


def _transport(read_messages, write=None):
    transport = AsyncMock()
    transport.read_messages = read_messages
    transport.connect = AsyncMock()
    transport.close = AsyncMock()
    transport.end_input = AsyncMock()
    transport.write = write if write is not None else AsyncMock()
    transport.is_ready = Mock(return_value=True)
    return transport


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_new_control_request_after_clean_reader_eof_fails_fast(backend):
    async def scenario():
        async def immediate_eof():
            return
            yield {}  # async-generator shape

        transport = _transport(immediate_eof)
        q = Query(transport=transport, is_streaming_mode=True)
        await q.start()
        try:
            with anyio.fail_after(2.0):
                assert q._read_task is not None
                await q._read_task.wait()
            prior_writes = transport.write.await_count
            with anyio.fail_after(0.7):
                with pytest.raises(CLIConnectionError, match="reader"):
                    await q.interrupt()
            assert transport.write.await_count == prior_writes
            assert q.pending_control_responses == {}
        finally:
            await q.close()

    anyio.run(scenario, backend=backend)


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_reader_eof_wakes_pending_before_blocked_user_flush(backend):
    async def scenario():
        release_reader = anyio.Event()
        flush_entered = anyio.Event()
        release_flush = anyio.Event()
        control_done = anyio.Event()
        wrote = anyio.Event()
        errors = []
        writes = []

        class SlowBatcher:
            async def flush(self):
                flush_entered.set()
                await release_flush.wait()

            async def close(self):
                release_flush.set()

        async def read_messages():
            await release_reader.wait()
            return
            yield {}  # async-generator shape

        async def write(data):
            writes.append(data)
            wrote.set()

        transport = _transport(read_messages, write)
        q = Query(transport=transport, is_streaming_mode=True)
        q._transcript_mirror_batcher = SlowBatcher()

        async def pending_interrupt():
            try:
                await q.interrupt()
            except Exception as exc:
                errors.append(exc)
            finally:
                control_done.set()

        await q.start()
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(pending_interrupt)
                with anyio.fail_after(2.0):
                    await wrote.wait()
                assert q.pending_control_responses
                release_reader.set()
                with anyio.fail_after(2.0):
                    await flush_entered.wait()

                # User-supplied SessionStore code is still blocked here.
                with anyio.fail_after(0.7):
                    await control_done.wait()
                assert len(errors) == 1
                assert isinstance(errors[0], CLIConnectionError)
                assert q.pending_control_responses == {}
                assert not release_flush.is_set()

                # A *new* request must also be rejected while flush is blocked.
                n_writes = len(writes)
                with anyio.fail_after(0.7):
                    with pytest.raises(CLIConnectionError, match="reader"):
                        await q.interrupt()
                assert len(writes) == n_writes
        finally:
            release_flush.set()
            with anyio.fail_after(2.0):
                await q.close()

    anyio.run(scenario, backend=backend)
