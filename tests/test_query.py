"""Tests for query() stdin lifecycle with SDK MCP servers and hooks.

The SDK communicates with the CLI subprocess over stdin/stdout. When SDK MCP
servers or hooks are configured, the CLI sends control_request messages back
to the SDK *after* the prompt is written. The SDK must keep stdin open long
enough to respond to these requests. These tests verify that both the string
prompt and AsyncIterable prompt paths defer closing stdin until the CLI's
first result arrives.
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import anyio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk.types import HookMatcher


def _capture_initialize_request(**query_kwargs):
    """Run Query.initialize() with a stubbed control channel and return the request dict."""
    captured: dict = {}

    async def _run():
        transport = AsyncMock()
        transport.is_ready = Mock(return_value=True)
        q = Query(transport=transport, is_streaming_mode=True, **query_kwargs)

        async def fake_send(request, timeout):
            captured.update(request)
            return {"commands": []}

        with patch.object(q, "_send_control_request", side_effect=fake_send):
            await q.initialize()

    anyio.run(_run)
    return captured


def test_initialize_sends_exclude_dynamic_sections():
    """Query.initialize() includes excludeDynamicSections in the control request."""
    sent = _capture_initialize_request(exclude_dynamic_sections=True)
    assert sent["subtype"] == "initialize"
    assert sent["excludeDynamicSections"] is True


def test_initialize_omits_exclude_dynamic_sections_when_unset():
    """excludeDynamicSections is absent from initialize when not configured."""
    sent = _capture_initialize_request()
    assert sent["subtype"] == "initialize"
    assert "excludeDynamicSections" not in sent


def test_initialize_sends_skills_list():
    """Query.initialize() includes skills only when it is a list."""
    sent = _capture_initialize_request(skills=["pdf", "docx"])
    assert sent["skills"] == ["pdf", "docx"]

    sent_empty = _capture_initialize_request(skills=[])
    assert sent_empty["skills"] == []


def test_initialize_omits_skills_for_none_and_all():
    """'all' and None both omit skills from initialize (no filter at wire level)."""
    assert "skills" not in _capture_initialize_request()
    assert "skills" not in _capture_initialize_request(skills=None)
    assert "skills" not in _capture_initialize_request(skills="all")


def _make_mock_transport(messages, control_requests=None):
    """Create a mock transport that yields messages and optionally sends control requests.

    Args:
        messages: List of message dicts to yield from read_messages.
        control_requests: Optional list of control request dicts. If provided,
            they are injected before the regular messages to simulate MCP init.
    """
    mock_transport = AsyncMock()

    all_messages = list(control_requests or []) + list(messages)

    async def mock_receive():
        for msg in all_messages:
            yield msg

    mock_transport.read_messages = mock_receive
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)
    return mock_transport


_ASSISTANT_AND_RESULT = [
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-sonnet-4-20250514",
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "duration_ms": 100,
        "duration_api_ms": 80,
        "is_error": False,
        "num_turns": 1,
        "session_id": "test",
        "total_cost_usd": 0.001,
    },
]


_MCP_CONTROL_REQUESTS = [
    {
        "type": "control_request",
        "request_id": "mcp_init_1",
        "request": {
            "subtype": "mcp_message",
            "server_name": "greeter",
            "message": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
        },
    },
    {
        "type": "control_request",
        "request_id": "mcp_init_2",
        "request": {
            "subtype": "mcp_message",
            "server_name": "greeter",
            "message": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        },
    },
]


def _make_greet_server():
    @tool("greet", "Greet a user", {"name": str})
    async def greet_tool(args):
        return {"content": [{"type": "text", "text": f"Hi {args['name']}"}]}

    return create_sdk_mcp_server("greeter", tools=[greet_tool])


class TestStringPromptWithSdkMcpServers:
    """Test that string prompts keep stdin open for SDK MCP servers."""

    def test_string_prompt_waits_for_result_with_sdk_mcp_servers(self):
        """end_input() should not be called until after the first result
        when SDK MCP servers are present."""

        async def _test():
            server = _make_greet_server()
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            call_order = []
            original_write = mock_transport.write

            async def tracking_write(data):
                call_order.append(("write", data))
                return await original_write(data)

            async def tracking_end_input():
                call_order.append(("end_input",))

            mock_transport.write = tracking_write
            mock_transport.end_input = tracking_end_input

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt="Hello",
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)
            assert any(c[0] == "end_input" for c in call_order)

            write_calls = [c for c in call_order if c[0] == "write"]
            assert len(write_calls) >= 1
            written_data = json.loads(write_calls[0][1])
            assert written_data["type"] == "user"
            assert written_data["message"]["content"] == "Hello"

        anyio.run(_test)

    def test_string_prompt_without_mcp_servers_closes_immediately(self):
        """end_input() should be called immediately when no SDK MCP servers
        are present (preserving existing behavior)."""

        async def _test():
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(prompt="Hello"):
                    messages.append(msg)

            assert len(messages) == 2
            mock_transport.end_input.assert_called_once()

        anyio.run(_test)

    def test_string_prompt_mcp_server_control_requests_succeed(self):
        """MCP control requests arriving after the user message should be
        handled successfully because stdin is still open."""

        async def _test():
            server = _make_greet_server()

            mock_transport = AsyncMock()
            writes = []

            async def tracking_write(data):
                writes.append(data)

            mock_transport.write = tracking_write
            mock_transport.connect = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.end_input = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            async def mock_receive():
                for req in _MCP_CONTROL_REQUESTS:
                    yield req
                for msg in _ASSISTANT_AND_RESULT:
                    yield msg

            mock_transport.read_messages = mock_receive

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt="Greet Alice",
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)

            # user message + 2 MCP control responses = at least 3 writes
            assert len(writes) >= 3

            control_responses = [
                json.loads(w.rstrip("\n")) for w in writes if "control_response" in w
            ]
            assert len(control_responses) == 2

        anyio.run(_test)

    def test_string_prompt_with_hooks_waits_for_result(self):
        """end_input() should wait for first result when hooks are configured,
        even without SDK MCP servers."""

        async def _test():
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            call_order = []

            async def tracking_write(data):
                call_order.append(("write", data))

            async def tracking_end_input():
                call_order.append(("end_input",))

            mock_transport.write = tracking_write
            mock_transport.end_input = tracking_end_input

            async def dummy_hook(input_data, tool_use_id, context):
                return {"continue_": True}

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt="Do something",
                    options=ClaudeAgentOptions(
                        hooks={
                            "PreToolUse": [
                                HookMatcher(hooks=[dummy_hook]),
                            ],
                        },
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert any(c[0] == "end_input" for c in call_order)

        anyio.run(_test)


class TestAsyncIterablePromptWithSdkMcpServers:
    """Test that AsyncIterable prompts keep stdin open for SDK MCP servers."""

    def test_async_iterable_with_sdk_mcp_servers(self):
        """AsyncIterable prompt path should wait for first result before
        closing stdin when SDK MCP servers are present."""

        async def _test():
            server = _make_greet_server()
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            call_order = []
            original_write = mock_transport.write

            async def tracking_write(data):
                call_order.append(("write", data))
                return await original_write(data)

            async def tracking_end_input():
                call_order.append(("end_input",))

            mock_transport.write = tracking_write
            mock_transport.end_input = tracking_end_input

            async def prompt_stream():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": "Hello"},
                }

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt=prompt_stream(),
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)
            assert any(c[0] == "end_input" for c in call_order)

            write_calls = [c for c in call_order if c[0] == "write"]
            assert len(write_calls) >= 1
            written_data = json.loads(write_calls[0][1])
            assert written_data["type"] == "user"
            assert written_data["message"]["content"] == "Hello"

        anyio.run(_test)

    def test_async_iterable_mcp_control_requests_succeed(self):
        """MCP control requests should be handled correctly when using
        AsyncIterable prompts with SDK MCP servers."""

        async def _test():
            server = _make_greet_server()

            mock_transport = AsyncMock()
            writes = []

            async def tracking_write(data):
                writes.append(data)

            mock_transport.write = tracking_write
            mock_transport.connect = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.end_input = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            async def mock_receive():
                for req in _MCP_CONTROL_REQUESTS:
                    yield req
                for msg in _ASSISTANT_AND_RESULT:
                    yield msg

            mock_transport.read_messages = mock_receive

            async def prompt_stream():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": "Greet Alice"},
                }

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt=prompt_stream(),
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)

            # user message + 2 MCP control responses = at least 3 writes
            assert len(writes) >= 3

            control_responses = [
                json.loads(w.rstrip("\n")) for w in writes if "control_response" in w
            ]
            assert len(control_responses) == 2

        anyio.run(_test)


class TestNoTimeoutForHooksAndMcpServers:
    """Regression test for #730: stdin must not be closed by a timeout when
    hooks or SDK MCP servers are active."""

    def test_hooks_wait_without_timeout(self):
        """wait_for_result_and_end_input() should wait indefinitely for the
        result event when hooks are configured, not cut off after 60s."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            end_input_called = anyio.Event()

            async def tracking_end_input():
                end_input_called.set()

            mock_transport.end_input = tracking_end_input

            q = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                hooks={"PreToolUse": [{"matcher": "Bash", "hooks": ["hook_0"]}]},
            )

            async with anyio.create_task_group() as tg:

                async def wait_then_check():
                    await anyio.sleep(0.05)
                    assert not end_input_called.is_set()
                    q._first_result_event.set()
                    await anyio.sleep(0.05)
                    assert end_input_called.is_set()

                tg.start_soon(q.wait_for_result_and_end_input)
                tg.start_soon(wait_then_check)

        anyio.run(_test)

    def test_no_hooks_closes_immediately(self):
        """Without hooks or SDK MCP servers, end_input should be called
        immediately without waiting for any event."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])

            q = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            await q.wait_for_result_and_end_input()
            mock_transport.end_input.assert_called_once()

        anyio.run(_test)


class TestQueryCrossTaskCleanup:
    """Tests for cross-task cleanup of Query task groups (issue #454).

    When a user breaks out of an async for loop over process_query(), Python
    finalizes the async generator in a different task than the one that called
    start(). This triggers close() from a different task context, which causes
    anyio to raise RuntimeError because cancel scopes must be exited by the
    same task that entered them. These tests verify that close() handles this
    gracefully.
    """

    def test_close_from_different_task_does_not_raise(self):
        """close() called from a different task than start() must not raise."""
        import asyncio

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            q = Query(transport=mock_transport, is_streaming_mode=True)

            await q.start()

            close_error = None

            async def close_in_other_task():
                nonlocal close_error
                try:
                    await q.close()
                except Exception as e:
                    close_error = e

            task = asyncio.create_task(close_in_other_task())
            await task

            assert close_error is None, f"close() raised: {close_error}"

        asyncio.run(_test())

    def test_close_from_same_task_still_works(self):
        """close() from the same task as start() should still work normally."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            q = Query(transport=mock_transport, is_streaming_mode=True)

            await q.start()
            await q.close()

            assert q._read_task is None
            mock_transport.close.assert_called_once()

        anyio.run(_test)


class TestControlCancelRequest:
    """Tests for control_cancel_request handling (issue #739).

    When the CLI sends a control_cancel_request, the SDK should cancel the
    matching in-flight _handle_control_request task so it stops executing and
    does not write a response for a request the CLI has already abandoned.
    """

    def test_cancel_request_cancels_inflight_hook(self):
        """A control_cancel_request should cancel the matching hook task."""
        import asyncio

        hook_started = asyncio.Event()
        hook_cancelled = asyncio.Event()

        async def slow_hook(input_data, tool_use_id, context):
            hook_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                hook_cancelled.set()
                raise
            return {}

        async def _test():
            mock_transport = AsyncMock()
            emitted: list[dict] = []

            async def mock_receive():
                yield {
                    "type": "control_request",
                    "request_id": "hook_1",
                    "request": {
                        "subtype": "hook_callback",
                        "callback_id": "hook_0",
                    },
                }
                await hook_started.wait()
                yield {
                    "type": "control_cancel_request",
                    "request_id": "hook_1",
                }
                await hook_cancelled.wait()

            async def mock_write(data):
                emitted.append(json.loads(data))

            mock_transport.read_messages = mock_receive
            mock_transport.write = mock_write
            mock_transport.close = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            q = Query(transport=mock_transport, is_streaming_mode=True)
            q.hook_callbacks["hook_0"] = slow_hook

            await q.start()
            await asyncio.wait_for(hook_cancelled.wait(), timeout=5)
            await q.close()

            assert hook_cancelled.is_set()
            assert "hook_1" not in q._inflight_requests
            responses = [m for m in emitted if m.get("type") == "control_response"]
            assert responses == [], (
                f"Cancelled request should not write a response, got: {responses}"
            )

        asyncio.run(_test())

    def test_cancel_request_for_unknown_id_is_noop(self):
        """A control_cancel_request for an unknown request_id should not raise."""
        import asyncio

        async def _test():
            mock_transport = _make_mock_transport(
                messages=[
                    {
                        "type": "control_cancel_request",
                        "request_id": "nonexistent",
                    },
                ]
                + _ASSISTANT_AND_RESULT
            )
            q = Query(transport=mock_transport, is_streaming_mode=True)

            await q.start()
            messages = []
            async for msg in q.receive_messages():
                messages.append(msg)
            await q.close()

            assert any(m.get("type") == "result" for m in messages)

        asyncio.run(_test())

    def test_completed_request_is_removed_from_inflight(self):
        """Once a control_request handler completes, it should be removed from
        _inflight_requests so a late cancel is a no-op."""
        import asyncio

        async def fast_hook(input_data, tool_use_id, context):
            return {}

        async def _test():
            mock_transport = _make_mock_transport(
                messages=_ASSISTANT_AND_RESULT,
                control_requests=[
                    {
                        "type": "control_request",
                        "request_id": "fast_1",
                        "request": {
                            "subtype": "hook_callback",
                            "callback_id": "hook_0",
                        },
                    }
                ],
            )
            q = Query(transport=mock_transport, is_streaming_mode=True)
            q.hook_callbacks["hook_0"] = fast_hook

            await q.start()
            async for msg in q.receive_messages():
                if msg.get("type") == "result":
                    break
            await asyncio.sleep(0)
            await q.close()

            assert "fast_1" not in q._inflight_requests

        asyncio.run(_test())
