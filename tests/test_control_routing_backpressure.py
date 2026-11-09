"""Regression tests for SDK control routing under stalled message consumption.

A caller can stop consuming messages after a result while the CLI continues
sending messages. This must not prevent later hook/control frames from being
handled, or silently discard messages when consumption resumes.
"""

import json

import anyio
import pytest

from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport import Transport


class ScriptedTransport(Transport):
    def __init__(self):
        self._send, self._receive = anyio.create_memory_object_stream[dict](256)
        self.writes = []

    async def connect(self):
        pass

    async def close(self):
        await self._send.aclose()
        await self._receive.aclose()

    async def write(self, data):
        self.writes.append(json.loads(data))

    async def end_input(self):
        pass

    def is_ready(self):
        return True

    async def read_messages(self):
        async with self._receive:
            async for message in self._receive:
                yield message

    async def inject(self, message):
        await self._send.send(message)


@pytest.mark.anyio
async def test_control_request_is_answered_after_unconsumed_message_burst():
    transport = ScriptedTransport()
    query = Query(
        transport=transport,
        is_streaming_mode=True,
        can_use_tool=None,
        hooks=None,
    )
    seen = anyio.Event()

    async def hook(_input, _tool_use_id, _context):
        seen.set()
        return {}

    query.hook_callbacks["hook-1"] = hook
    await query.start()
    try:
        # Exceed the old 100-message buffer without reading any of it.
        for index in range(150):
            await transport.inject({"type": "system", "subtype": "test_event", "index": index})
        await transport.inject(
            {
                "type": "control_request",
                "request_id": "request-1",
                "request": {
                    "subtype": "hook_callback",
                    "callback_id": "hook-1",
                    "input": {},
                },
            }
        )

        # The hook must be serviced independently of regular message reads.
        with anyio.fail_after(2):
            await seen.wait()
            while not any(
                msg.get("type") == "control_response"
                and msg.get("response", {}).get("request_id") == "request-1"
                for msg in transport.writes
            ):
                await anyio.sleep(0)

        response = next(
            msg
            for msg in transport.writes
            if msg.get("type") == "control_response"
            and msg.get("response", {}).get("request_id") == "request-1"
        )
        assert response["response"]["subtype"] == "success"

        # Processing control traffic must not drop or reorder buffered messages.
        received = []
        with anyio.fail_after(2):
            async for msg in query.receive_messages():
                received.append(msg["index"])
                if len(received) == 150:
                    break
        assert received == list(range(150))
    finally:
        await query.close()


@pytest.mark.anyio
async def test_buffer_does_not_block_after_result_before_follow_up():
    transport = ScriptedTransport()
    query = Query(
        transport=transport, is_streaming_mode=True, can_use_tool=None, hooks=None
    )
    await query.start()
    try:
        await transport.inject(
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 0}
        )
        for index in range(130):
            await transport.inject(
                {"type": "system", "subtype": "test_event", "index": index}
            )
        await transport.inject(
            {
                "type": "control_request",
                "request_id": "request-2",
                "request": {"subtype": "unsupported_test_request"},
            }
        )
        # Even an unknown control request must receive an error response,
        # rather than being trapped behind regular SDK messages.
        with anyio.fail_after(2):
            while not any(
                msg.get("type") == "control_response"
                and msg.get("response", {}).get("request_id") == "request-2"
                for msg in transport.writes
            ):
                await anyio.sleep(0)
        response = next(
            msg for msg in transport.writes
            if msg.get("response", {}).get("request_id") == "request-2"
        )
        assert response["response"]["subtype"] == "error"
    finally:
        await query.close()
