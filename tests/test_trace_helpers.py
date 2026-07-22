"""Tests for trace context propagation in messages.

Verifies that the ambient OTel trace context is injected into every user
message (instead of only at connect() time), so outbound MCP/tool spans
attribute to the caller's current turn trace in long-lived sessions.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import anyio

from claude_agent_sdk._internal._trace_helpers import inject_trace_into_message


class TestInjectTraceIntoMessage:
    """Direct unit tests for the inject_trace_into_message() helper."""

    def test_injects_traceparent_and_tracestate(self):
        message = {"type": "user", "content": "hello"}

        fake_propagate = MagicMock()

        def fake_inject(carrier):
            carrier["traceparent"] = (
                "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
            )
            carrier["tracestate"] = "vendor=value"

        fake_propagate.inject = fake_inject

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(propagate=fake_propagate),
                "opentelemetry.propagate": fake_propagate,
            },
        ):
            inject_trace_into_message(message)

        assert message["traceparent"] == (
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        )
        assert message["tracestate"] == "vendor=value"

    def test_noop_without_opentelemetry(self):
        message = {"type": "user"}

        with patch.dict("sys.modules", {"opentelemetry": None}):
            inject_trace_into_message(message)

        assert "traceparent" not in message
        assert "tracestate" not in message

    def test_noop_without_active_span(self):
        message = {"type": "user"}

        fake_propagate = MagicMock()
        fake_propagate.inject = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(propagate=fake_propagate),
                "opentelemetry.propagate": fake_propagate,
            },
        ):
            inject_trace_into_message(message)

        assert "traceparent" not in message
        assert "tracestate" not in message

    def test_injects_traceparent_only_when_tracestate_missing(self):
        message = {"type": "user"}

        fake_propagate = MagicMock()

        def fake_inject(carrier):
            carrier["traceparent"] = "00-aaaa-bbbb-01"

        fake_propagate.inject = fake_inject

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(propagate=fake_propagate),
                "opentelemetry.propagate": fake_propagate,
            },
        ):
            inject_trace_into_message(message)

        assert message["traceparent"] == "00-aaaa-bbbb-01"
        assert "tracestate" not in message

    def test_propagator_error_does_not_raise(self):
        message = {"type": "user"}

        fake_propagate = MagicMock()
        fake_propagate.inject = MagicMock(
            side_effect=RuntimeError("inject failed")
        )

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(propagate=fake_propagate),
                "opentelemetry.propagate": fake_propagate,
            },
        ):
            inject_trace_into_message(message)

        assert "traceparent" not in message

    def test_existing_message_fields_are_preserved(self):
        message = {"type": "user", "content": "hello", "session_id": "s1"}

        fake_propagate = MagicMock()

        def fake_inject(carrier):
            carrier["traceparent"] = "00-aaaa-bbbb-01"

        fake_propagate.inject = fake_inject

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(propagate=fake_propagate),
                "opentelemetry.propagate": fake_propagate,
            },
        ):
            inject_trace_into_message(message)

        assert message["type"] == "user"
        assert message["content"] == "hello"
        assert message["session_id"] == "s1"
        assert message["traceparent"] == "00-aaaa-bbbb-01"


class TestClaudeSDKClientQueryTraceInjection:
    """Trace context is injected in ClaudeSDKClient.query() for both
    string and AsyncIterable prompts."""

    def test_string_prompt_gets_trace_context(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            writes = []

            async def capture_write(data):
                writes.append(json.loads(data))

            mock_transport.write = capture_write
            mock_transport.end_input = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.connect = AsyncMock()

            fake_propagate = MagicMock()

            def fake_inject(carrier):
                carrier["traceparent"] = "00-abc-123-01"

            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "claude_agent_sdk.client.ClaudeSDKClient.connect",
                    new_callable=AsyncMock,
                ),
            ):
                from claude_agent_sdk import ClaudeSDKClient

                client = ClaudeSDKClient(transport=mock_transport)
                client._transport = mock_transport
                client._query = AsyncMock()
                client._query.closed = False

                await client.query(prompt="Say hello", session_id="s1")

            assert len(writes) >= 1
            msg = writes[0]
            assert msg["type"] == "user"
            assert msg["message"]["content"] == "Say hello"
            assert msg["traceparent"] == "00-abc-123-01"

        anyio.run(_test)

    def test_async_iterable_prompt_each_message_gets_trace_context(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            writes = []

            async def capture_write(data):
                writes.append(json.loads(data))

            mock_transport.write = capture_write
            mock_transport.end_input = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.connect = AsyncMock()

            fake_propagate = MagicMock()

            def fake_inject(carrier):
                carrier["traceparent"] = "00-abc-123-01"

            fake_propagate.inject = fake_inject

            async def prompt_stream():
                yield {"type": "user", "message": {"role": "user", "content": "First"}}
                yield {"type": "user", "message": {"role": "user", "content": "Second"}}

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "claude_agent_sdk.client.ClaudeSDKClient.connect",
                    new_callable=AsyncMock,
                ),
            ):
                from claude_agent_sdk import ClaudeSDKClient

                client = ClaudeSDKClient(transport=mock_transport)
                client._transport = mock_transport
                client._query = AsyncMock()
                client._query.closed = False

                await client.query(prompt=prompt_stream(), session_id="s1")

            assert len(writes) >= 2
            for msg in writes:
                assert msg["traceparent"] == "00-abc-123-01"
            assert writes[0]["message"]["content"] == "First"
            assert writes[1]["message"]["content"] == "Second"

        anyio.run(_test)

    def test_string_prompt_does_not_inject_trace_without_opentelemetry(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            writes = []

            async def capture_write(data):
                writes.append(json.loads(data))

            mock_transport.write = capture_write
            mock_transport.end_input = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.connect = AsyncMock()

            with (
                patch.dict("sys.modules", {"opentelemetry": None}),
                patch(
                    "claude_agent_sdk.client.ClaudeSDKClient.connect",
                    new_callable=AsyncMock,
                ),
            ):
                from claude_agent_sdk import ClaudeSDKClient

                client = ClaudeSDKClient(transport=mock_transport)
                client._transport = mock_transport
                client._query = AsyncMock()
                client._query.closed = False

                await client.query(prompt="Say hello", session_id="s1")

            assert len(writes) >= 1
            msg = writes[0]
            assert "traceparent" not in msg

        anyio.run(_test)


class TestInternalClientProcessQueryTraceInjection:
    """Trace context is injected in InternalClient._process_query_inner()
    for both string and AsyncIterable prompts."""

    def test_string_prompt_injects_trace_context(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            writes = []

            async def capture_write(data):
                writes.append(json.loads(data))

            mock_transport.write = capture_write
            mock_transport.end_input = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.connect = AsyncMock()

            async def mock_receive():
                yield {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 100,
                    "duration_api_ms": 80,
                    "is_error": False,
                    "num_turns": 1,
                    "session_id": "test",
                }

            mock_transport.read_messages = mock_receive

            fake_propagate = MagicMock()

            def fake_inject(carrier):
                carrier["traceparent"] = "00-abc-123-01"

            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport",
                    return_value=mock_transport,
                ),
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                from claude_agent_sdk import query as query_fn

                async for _ in query_fn(prompt="Hello"):
                    pass

            assert len(writes) >= 1
            msg = writes[0]
            assert msg["type"] == "user"
            assert msg["message"]["content"] == "Hello"
            assert msg["traceparent"] == "00-abc-123-01"

        anyio.run(_test)

    def test_async_iterable_prompt_injects_trace_context(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            writes = []

            async def capture_write(data):
                writes.append(json.loads(data))

            mock_transport.write = capture_write
            mock_transport.end_input = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.connect = AsyncMock()

            async def mock_receive():
                await anyio.sleep(0.05)
                yield {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 100,
                    "duration_api_ms": 80,
                    "is_error": False,
                    "num_turns": 1,
                    "session_id": "test",
                }

            mock_transport.read_messages = mock_receive

            async def prompt_stream():
                yield {"type": "user", "message": {"role": "user", "content": "A"}}
                yield {"type": "user", "message": {"role": "user", "content": "B"}}

            fake_propagate = MagicMock()

            def fake_inject(carrier):
                carrier["traceparent"] = "00-abc-123-01"

            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport",
                    return_value=mock_transport,
                ),
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                from claude_agent_sdk import query as query_fn

                async for _ in query_fn(prompt=prompt_stream()):
                    pass

            assert len(writes) >= 2
            for msg in writes:
                assert msg["traceparent"] == "00-abc-123-01"
            assert writes[0]["message"]["content"] == "A"
            assert writes[1]["message"]["content"] == "B"

        anyio.run(_test)


class TestQueryStreamInputTraceInjection:
    """Each message streamed via Query.stream_input() gets trace context."""

    def test_stream_input_injects_trace_context(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            writes = []

            async def capture_write(data):
                writes.append(json.loads(data))

            mock_transport.write = capture_write
            mock_transport.end_input = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.connect = AsyncMock()

            streamed = [
                {"type": "user", "message": {"role": "user", "content": "First"}},
                {"type": "user", "message": {"role": "user", "content": "Second"}},
                {"type": "user", "message": {"role": "user", "content": "Third"}},
            ]

            async def prompt_stream():
                for msg in streamed:
                    yield msg

            fake_propagate = MagicMock()

            def fake_inject(carrier):
                carrier["traceparent"] = "00-xyz-789-01"

            fake_propagate.inject = fake_inject

            with patch.dict(
                "sys.modules",
                {
                    "opentelemetry": MagicMock(propagate=fake_propagate),
                    "opentelemetry.propagate": fake_propagate,
                },
            ):
                from claude_agent_sdk._internal.query import Query

                q = Query(transport=mock_transport, is_streaming_mode=True)
                q._first_result_event = anyio.Event()
                q._first_result_event.set()

                await q.stream_input(prompt_stream())

            assert len(writes) == 3
            for i, msg in enumerate(writes):
                assert msg["traceparent"] == "00-xyz-789-01"
                assert msg["message"]["content"] == streamed[i]["message"]["content"]

        anyio.run(_test)

    def test_stream_input_does_not_inject_without_opentelemetry(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            writes = []

            async def capture_write(data):
                writes.append(json.loads(data))

            mock_transport.write = capture_write
            mock_transport.end_input = AsyncMock()

            async def prompt_stream():
                yield {"type": "user", "message": {"role": "user", "content": "Hi"}}

            with patch.dict("sys.modules", {"opentelemetry": None}):
                from claude_agent_sdk._internal.query import Query

                q = Query(transport=mock_transport, is_streaming_mode=True)
                q._first_result_event = anyio.Event()
                q._first_result_event.set()

                await q.stream_input(prompt_stream())

            assert len(writes) == 1
            assert "traceparent" not in writes[0]

        anyio.run(_test)


class TestQuerySetTraceContext:
    """Query.set_trace_context() sends a control request with trace context."""

    def test_set_trace_context_sends_control_request(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            control_requests = []

            async def fake_send_control(request):
                control_requests.append(request)

            fake_propagate = MagicMock()

            def fake_inject(carrier):
                carrier["traceparent"] = "00-aaa-bbb-01"
                carrier["tracestate"] = "rojo=1"

            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch.object(
                    mock_transport, "write", new_callable=AsyncMock
                ),
            ):
                from claude_agent_sdk._internal.query import Query

                q = Query(transport=mock_transport, is_streaming_mode=True)
                q._send_control_request = fake_send_control

                await q.set_trace_context()

            assert len(control_requests) == 1
            req = control_requests[0]
            assert req["subtype"] == "set_trace_context"
            assert req["traceparent"] == "00-aaa-bbb-01"
            assert req["tracestate"] == "rojo=1"

        anyio.run(_test)

    def test_set_trace_context_noop_without_active_span(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            control_requests = []

            async def fake_send_control(request):
                control_requests.append(request)

            fake_propagate = MagicMock()
            fake_propagate.inject = MagicMock()

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch.object(
                    mock_transport, "write", new_callable=AsyncMock
                ),
            ):
                from claude_agent_sdk._internal.query import Query

                q = Query(transport=mock_transport, is_streaming_mode=True)
                q._send_control_request = fake_send_control

                await q.set_trace_context()

            assert len(control_requests) == 0

        anyio.run(_test)

    def test_set_trace_context_error_does_not_raise(self):
        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            fake_propagate = MagicMock()
            fake_propagate.inject = MagicMock(
                side_effect=RuntimeError("inject failed")
            )

            with patch.dict(
                "sys.modules",
                {
                    "opentelemetry": MagicMock(propagate=fake_propagate),
                    "opentelemetry.propagate": fake_propagate,
                },
            ):
                from claude_agent_sdk._internal.query import Query

                q = Query(transport=mock_transport, is_streaming_mode=True)

                await q.set_trace_context()

        anyio.run(_test)
