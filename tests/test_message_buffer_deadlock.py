"""Tests for message buffer deadlock fix (issue #558).

Reproduces the deadlock scenario where:
1. _read_messages() handles BOTH control routing AND message buffering
2. When the message buffer fills, _read_messages() blocks on send()
3. This prevents it from reading ANY transport data, including control messages
4. Control requests time out, and the system deadlocks

The fix (math.inf unbounded buffer) prevents _read_messages() from ever
blocking on send(), keeping control message routing alive.
"""

import asyncio
import json
import math
from unittest.mock import AsyncMock, Mock

import anyio
import pytest

from claude_agent_sdk._internal.query import Query


def _make_init_response(request_id: str) -> dict:
    """Build a control_response for an initialize request."""
    return {
        "type": "control_response",
        "response": {
            "request_id": request_id,
            "subtype": "success",
            "commands": [],
            "output_style": "default",
        },
    }


def _make_mock_transport() -> tuple[AsyncMock, list[str]]:
    """Create a mock transport that tracks written messages."""
    mock_transport = AsyncMock()
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)

    written_messages: list[str] = []

    async def track_write(data: str):
        written_messages.append(data)

    mock_transport.write = AsyncMock(side_effect=track_write)
    return mock_transport, written_messages


async def _mock_read_messages_factory(written_messages: list[str]):
    """Async generator that handles init then emits 110 task_notifications,
    then waits for an interrupt control request and responds to it."""
    # Wait for init request
    for _ in range(50):
        await asyncio.sleep(0.01)
        if written_messages:
            break

    # Handle initialization
    for msg_str in written_messages:
        try:
            msg = json.loads(msg_str.strip())
            if (
                msg.get("type") == "control_request"
                and msg.get("request", {}).get("subtype") == "initialize"
            ):
                yield _make_init_response(msg["request_id"])
                break
        except (json.JSONDecodeError, KeyError):
            pass

    # Emit 110 regular messages — more than the old buffer size of 100
    for i in range(110):
        yield {
            "type": "system",
            "subtype": "task_notification",
            "task_id": f"task_{i}",
            "status": "completed",
            "summary": f"Task {i} done",
        }

    # Wait for interrupt control request and respond.
    # With bounded buffer, _read_messages() is blocked on send() above
    # and will never reach this point.
    for _ in range(200):
        await asyncio.sleep(0.01)
        for msg_str in written_messages:
            try:
                msg = json.loads(msg_str.strip())
                if (
                    msg.get("type") == "control_request"
                    and msg.get("request", {}).get("subtype") == "interrupt"
                ):
                    yield {
                        "type": "control_response",
                        "response": {
                            "request_id": msg["request_id"],
                            "subtype": "success",
                        },
                    }
                    return
            except (json.JSONDecodeError, KeyError):
                pass


class TestMessageBufferDeadlock:
    """Test that an unbounded message buffer prevents deadlocks."""

    def test_bounded_buffer_blocks_control_message_routing(self):
        """Prove bounded buffer causes _read_messages() to block, preventing
        control protocol messages from being processed.

        This is the exact deadlock from issue #558:
        1. Transport emits 110 regular messages (more than buffer=100)
        2. Nobody consumes from the buffer (simulates receive_response()
           having stopped after a ResultMessage)
        3. _read_messages() blocks on send() at message 101
        4. A control_response that comes AFTER message 100 is never read
        5. The pending control request that needs that response times out
        """

        async def _test():
            mock_transport, written_messages = _make_mock_transport()
            mock_transport.read_messages = lambda: _mock_read_messages_factory(
                written_messages
            )

            # Patch Query to use BOUNDED buffer (old behavior)
            original_init = Query.__init__

            def patched_init(self, *args, **kwargs):
                original_init(self, *args, **kwargs)
                self._message_send, self._message_receive = (
                    anyio.create_memory_object_stream[dict](max_buffer_size=100)
                )

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(Query, "__init__", patched_init)

                q = Query(transport=mock_transport, is_streaming_mode=True)
                await q.start()
                await q.initialize()

                # Don't consume any messages — simulates receive_response()
                # having stopped after ResultMessage.
                await asyncio.sleep(0.5)

                # _read_messages() is blocked on send(), so it can't read
                # the control_response → this should time out.
                with pytest.raises(Exception, match="Control request timeout"):
                    await q._send_control_request({"subtype": "interrupt"}, timeout=1.0)

                await q.close()

        anyio.run(_test)

    def test_unbounded_buffer_allows_control_message_routing(self):
        """Prove unbounded buffer keeps control message routing alive.

        Same scenario as above, but with math.inf buffer (the fix).
        _read_messages() never blocks, so it reads the control_response
        and the interrupt request succeeds.
        """

        async def _test():
            mock_transport, written_messages = _make_mock_transport()
            mock_transport.read_messages = lambda: _mock_read_messages_factory(
                written_messages
            )

            # Use the real Query (which has math.inf buffer from the fix)
            q = Query(transport=mock_transport, is_streaming_mode=True)
            await q.start()
            await q.initialize()

            # Don't consume messages — buffer absorbs all 110
            await asyncio.sleep(0.5)

            # With unbounded buffer, _read_messages() is NOT blocked,
            # so it reads the control_response. This should succeed.
            await q._send_control_request({"subtype": "interrupt"}, timeout=3.0)

            await q.close()

        anyio.run(_test)

    def test_query_class_uses_unbounded_buffer(self):
        """Verify the Query class is configured with math.inf buffer size."""
        transport = AsyncMock()
        q = Query(transport=transport, is_streaming_mode=True)
        stats = q._message_send.statistics()
        assert stats.max_buffer_size == math.inf, (
            f"Expected unbounded buffer (math.inf), got {stats.max_buffer_size}"
        )
