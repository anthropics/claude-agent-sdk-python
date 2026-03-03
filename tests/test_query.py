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
from claude_agent_sdk.types import HookMatcher


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
