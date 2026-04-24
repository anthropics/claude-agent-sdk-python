"""Tests for Claude SDK client functionality."""

import os
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, query
from claude_agent_sdk.types import TextBlock


class TestQueryFunction:
    """Test the main query function."""

    def test_query_single_prompt(self):
        """Test query with a single prompt."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.InternalClient.process_query"
            ) as mock_process:
                # Mock the async generator
                async def mock_generator():
                    yield AssistantMessage(
                        content=[TextBlock(text="4")], model="claude-opus-4-1-20250805"
                    )

                mock_process.return_value = mock_generator()

                messages = []
                async for msg in query(prompt="What is 2+2?"):
                    messages.append(msg)

                assert len(messages) == 1
                assert isinstance(messages[0], AssistantMessage)
                assert messages[0].content[0].text == "4"

        anyio.run(_test)

    def test_query_with_options(self):
        """Test query with various options."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.InternalClient.process_query"
            ) as mock_process:

                async def mock_generator():
                    yield AssistantMessage(
                        content=[TextBlock(text="Hello!")],
                        model="claude-opus-4-1-20250805",
                    )

                mock_process.return_value = mock_generator()

                options = ClaudeAgentOptions(
                    allowed_tools=["Read", "Write"],
                    system_prompt="You are helpful",
                    permission_mode="acceptEdits",
                    max_turns=5,
                )

                messages = []
                async for msg in query(prompt="Hi", options=options):
                    messages.append(msg)

                # Verify process_query was called with correct prompt and options
                mock_process.assert_called_once()
                call_args = mock_process.call_args
                assert call_args[1]["prompt"] == "Hi"
                assert call_args[1]["options"] == options

        anyio.run(_test)

    def test_query_with_cwd(self):
        """Test query with custom working directory."""

        async def _test():
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_transport_class,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Done"}],
                            "model": "claude-opus-4-1-20250805",
                        },
                    }
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1000,
                        "duration_api_ms": 800,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test-session",
                        "total_cost_usd": 0.001,
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                options = ClaudeAgentOptions(cwd="/custom/path")
                messages = []
                async for msg in query(prompt="test", options=options):
                    messages.append(msg)

                # Verify transport was created with correct parameters
                mock_transport_class.assert_called_once()
                call_kwargs = mock_transport_class.call_args.kwargs
                assert call_kwargs["prompt"] == "test"
                assert call_kwargs["options"].cwd == "/custom/path"

        anyio.run(_test)

    def _run_query_with_mocked_internals(self, env_patch, expected_timeout):
        """Helper: run query() with mocked transport/Query and verify initialize_timeout."""

        async def _test():
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_transport_class,
                patch("claude_agent_sdk._internal.client.Query") as mock_query_class,
                patch.dict(os.environ, env_patch, clear=False),
            ):
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                mock_query = AsyncMock()
                mock_query_class.return_value = mock_query
                mock_query.start = AsyncMock()
                mock_query.initialize = AsyncMock()
                mock_query.close = AsyncMock()
                mock_query._tg = None

                def _consume_coro(coro):
                    coro.close()
                    return Mock()

                mock_query.spawn_task = Mock(side_effect=_consume_coro)

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

                mock_query.receive_messages = mock_receive

                async for _ in query(prompt="test", options=ClaudeAgentOptions()):
                    pass

                call_kwargs = mock_query_class.call_args.kwargs
                assert call_kwargs["initialize_timeout"] == expected_timeout

        anyio.run(_test)

    def test_query_passes_initialize_timeout_from_env(self):
        """Test that query() reads CLAUDE_CODE_STREAM_CLOSE_TIMEOUT and passes it to Query."""
        self._run_query_with_mocked_internals(
            env_patch={"CLAUDE_CODE_STREAM_CLOSE_TIMEOUT": "120000"},
            expected_timeout=120.0,
        )

    def test_query_uses_default_initialize_timeout(self):
        """Test that query() defaults to 60s initialize timeout when env var is not set."""
        # Ensure env var is absent for this test
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", None)
            self._run_query_with_mocked_internals(
                env_patch={},
                expected_timeout=60.0,
            )

    def test_string_prompt_spawns_wait_for_result_as_task(self):
        """Test that string prompts spawn wait_for_result_and_end_input as a background
        task instead of awaiting it inline, preventing deadlock when the message
        buffer fills up (e.g. >50 tool calls with hooks)."""

        async def _test():
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_transport_class,
                patch("claude_agent_sdk._internal.client.Query") as mock_query_class,
            ):
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                mock_query = AsyncMock()
                mock_query_class.return_value = mock_query
                mock_query.start = AsyncMock()
                mock_query.initialize = AsyncMock()
                mock_query.close = AsyncMock()
                mock_query._tg = None

                def _consume_coro(coro):
                    coro.close()
                    return Mock()

                mock_query.spawn_task = Mock(side_effect=_consume_coro)

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

                mock_query.receive_messages = mock_receive

                async for _ in query(prompt="test", options=ClaudeAgentOptions()):
                    pass

                mock_query.spawn_task.assert_called_once()
                assert not mock_query.wait_for_result_and_end_input.await_args_list, (
                    "wait_for_result_and_end_input should be spawned as a task, "
                    "not awaited directly"
                )

        anyio.run(_test)


@pytest.mark.filterwarnings(
    "ignore:Unclosed <MemoryObjectReceiveStream:ResourceWarning"
)
class TestClaudeSDKClientTrioBackend:
    """Regression test: ClaudeSDKClient must work under trio.

    ``Query.start``/``spawn_task`` must not call ``asyncio.get_running_loop()``
    (raises ``RuntimeError: no running event loop`` under trio). This test
    drives connect()/disconnect() end-to-end on the trio backend with a mock
    transport that uses only anyio primitives.
    """

    def test_client_connect_under_trio(self):
        import json

        from claude_agent_sdk import ClaudeSDKClient

        def _make_trio_safe_transport():
            """Mock transport using anyio.sleep so it runs under trio."""
            mock_transport = AsyncMock()
            mock_transport.connect = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.end_input = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            written: list[str] = []

            async def mock_write(data):
                written.append(data)

            mock_transport.write = AsyncMock(side_effect=mock_write)

            async def read_messages():
                # Respond to the initialize control_request so connect()
                # doesn't block on the 60s timeout.
                for _ in range(200):
                    for msg_str in written:
                        try:
                            msg = json.loads(msg_str.strip())
                        except (json.JSONDecodeError, AttributeError):
                            continue
                        if (
                            msg.get("type") == "control_request"
                            and msg.get("request", {}).get("subtype") == "initialize"
                        ):
                            yield {
                                "type": "control_response",
                                "response": {
                                    "request_id": msg.get("request_id"),
                                    "subtype": "success",
                                    "response": {},
                                },
                            }
                            return
                    await anyio.sleep(0.01)

            mock_transport.read_messages = read_messages
            return mock_transport

        async def _test():
            mock_transport = _make_trio_safe_transport()
            async with ClaudeSDKClient(transport=mock_transport) as client:
                assert client._transport is mock_transport
                mock_transport.connect.assert_called_once()
            mock_transport.close.assert_called_once()

        anyio.run(_test, backend="trio")
