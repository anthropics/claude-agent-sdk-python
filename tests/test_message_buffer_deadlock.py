"""Tests for message buffer deadlock fix (issue #558).

The fix changes max_buffer_size from 100 to math.inf so that
_read_messages() never blocks on send(), keeping control message
routing alive even when the consumer (receive_response) has stopped.

See: https://github.com/anthropics/claude-agent-sdk-python/issues/558
"""

import asyncio
import json
import math
from unittest.mock import AsyncMock, Mock

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

    def test_unbounded_buffer_absorbs_all_messages(self):
        """Prove unbounded buffer absorbs all messages without blocking.

        With math.inf buffer, _read_messages() puts all 110 messages in
        the buffer without ever blocking. This means it stays free to
        handle control_responses at any time.
        """

        async def _test():
            mock_transport, written_messages = _make_mock_transport()
            mock_transport.read_messages = lambda: _mock_read_messages_factory(
                written_messages
            )

            q = Query(transport=mock_transport, is_streaming_mode=True)
            await q.start()
            await q.initialize()

            # Wait for all messages to be buffered
            await asyncio.sleep(1.0)

            # All 110 messages should be in the buffer — nothing blocked
            stats = q._message_send.statistics()
            assert stats.current_buffer_used >= 110, (
                f"Expected >=110 messages buffered, got {stats.current_buffer_used}. "
                f"With bounded buffer (100), _read_messages() would have blocked at 101."
            )

            await q.close()

        asyncio.run(_test())

    def test_unbounded_buffer_allows_control_message_routing(self):
        """Prove unbounded buffer keeps control message routing alive.

        110 messages flood the buffer, but _read_messages() never blocks.
        It reads the interrupt control_response and the request succeeds.
        """

        async def _test():
            mock_transport, written_messages = _make_mock_transport()
            mock_transport.read_messages = lambda: _mock_read_messages_factory(
                written_messages
            )

            q = Query(transport=mock_transport, is_streaming_mode=True)
            await q.start()
            await q.initialize()

            # Don't consume messages — buffer absorbs all 110
            await asyncio.sleep(0.5)

            # With unbounded buffer, _read_messages() is NOT blocked,
            # so it reads the control_response. This should succeed.
            await q._send_control_request({"subtype": "interrupt"}, timeout=5.0)

            await q.close()

        asyncio.run(_test())

    def test_query_class_uses_unbounded_buffer(self):
        """Verify the Query class is configured with math.inf buffer size."""
        transport = AsyncMock()
        q = Query(transport=transport, is_streaming_mode=True)
        stats = q._message_send.statistics()
        assert stats.max_buffer_size == math.inf, (
            f"Expected unbounded buffer (math.inf), got {stats.max_buffer_size}"
        )
