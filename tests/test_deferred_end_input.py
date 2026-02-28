"""Tests for deferred end_input() with SDK MCP servers on string prompts.

When SDK MCP servers or hooks are present and prompt is a string,
end_input() must be deferred until the first result message is received.
Closing stdin immediately prevents the CLI from completing tools/list
via control protocol, making SDK MCP tools invisible to the model.

See: client.py process_query() string prompt handling.
"""

from unittest.mock import AsyncMock, Mock, patch

import anyio

from claude_agent_sdk import ClaudeAgentOptions, query

RESULT_MESSAGE = {
    "type": "result",
    "subtype": "success",
    "duration_ms": 100,
    "duration_api_ms": 80,
    "is_error": False,
    "num_turns": 1,
    "session_id": "test",
    "total_cost_usd": 0.001,
}


def _make_mock_transport() -> Mock:
    """Create a mock transport with standard async methods."""
    mock_transport = Mock()
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)

    async def mock_receive() -> None:  # type: ignore[return]
        yield RESULT_MESSAGE

    mock_transport.read_messages = mock_receive
    return mock_transport


class TestDeferredEndInput:
    """Test that end_input() is deferred for string prompts with SDK MCP servers."""

    def test_string_prompt_without_sdk_mcp_servers_closes_stdin_immediately(
        self,
    ) -> None:
        """Without SDK MCP servers, end_input() should be called right after write."""

        async def _test() -> None:
            mock_transport = _make_mock_transport()

            # Track call order
            call_order: list[str] = []
            original_write = mock_transport.write

            async def tracking_write(data: str) -> None:
                call_order.append("write")
                return await original_write(data)

            async def tracking_end_input() -> None:
                call_order.append("end_input")

            mock_transport.write = AsyncMock(side_effect=tracking_write)
            mock_transport.end_input = AsyncMock(side_effect=tracking_end_input)

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport",
                    return_value=mock_transport,
                ),
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                options = ClaudeAgentOptions()
                async for _ in query(prompt="test prompt", options=options):
                    pass

            # end_input should be called immediately after write
            assert "write" in call_order
            assert "end_input" in call_order
            write_idx = call_order.index("write")
            end_input_idx = call_order.index("end_input")
            assert end_input_idx == write_idx + 1, (
                f"end_input should follow write immediately, got order: {call_order}"
            )

        anyio.run(_test)

    def test_string_prompt_with_sdk_mcp_servers_calls_end_input(self) -> None:
        """With SDK MCP servers, end_input() must be called (deferred via task group)."""

        async def _test() -> None:
            mock_transport = _make_mock_transport()
            mock_mcp_server = Mock()

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport",
                    return_value=mock_transport,
                ),
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                options = ClaudeAgentOptions(
                    mcp_servers={"team": {"type": "sdk", "instance": mock_mcp_server}},  # type: ignore[typeddict-item]
                )
                async for _ in query(prompt="test prompt", options=options):
                    pass

            mock_transport.end_input.assert_called_once()

        anyio.run(_test)

    def test_deferred_end_input_waits_for_result_event(self) -> None:
        """end_input() must not be called before the result event fires.

        Uses a delayed result message to verify that end_input waits
        for _first_result_event rather than closing stdin immediately.
        """

        async def _test() -> None:
            mock_transport = _make_mock_transport()
            end_input_called_before_result = False

            # Override read_messages to delay the result
            result_gate = anyio.Event()

            async def delayed_receive() -> None:  # type: ignore[return]
                await result_gate.wait()
                yield RESULT_MESSAGE

            mock_transport.read_messages = delayed_receive

            original_end_input = mock_transport.end_input

            async def tracking_end_input() -> None:
                nonlocal end_input_called_before_result
                if not result_gate.is_set():
                    end_input_called_before_result = True
                return await original_end_input()

            mock_transport.end_input = AsyncMock(side_effect=tracking_end_input)

            mock_mcp_server = Mock()

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport",
                    return_value=mock_transport,
                ),
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                options = ClaudeAgentOptions(
                    mcp_servers={"team": {"type": "sdk", "instance": mock_mcp_server}},  # type: ignore[typeddict-item]
                )

                async def consume_and_release() -> None:
                    # Give task group time to start _deferred_end_input
                    await anyio.sleep(0.05)
                    # Now release the result — end_input should NOT have been called yet
                    result_gate.set()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(consume_and_release)
                    async for _ in query(prompt="test prompt", options=options):
                        pass

            assert not end_input_called_before_result, (
                "end_input must not be called before the result event fires"
            )
            mock_transport.end_input.assert_called_once()

        anyio.run(_test)

    def test_end_input_called_even_with_sdk_mcp_servers(self) -> None:
        """end_input() must always eventually be called to avoid resource leaks."""

        async def _test() -> None:
            end_input_called = anyio.Event()

            async def tracking_end_input() -> None:
                end_input_called.set()

            mock_transport = _make_mock_transport()
            mock_transport.end_input = AsyncMock(side_effect=tracking_end_input)

            mock_mcp_server = Mock()

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport",
                    return_value=mock_transport,
                ),
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                options = ClaudeAgentOptions(
                    mcp_servers={"team": {"type": "sdk", "instance": mock_mcp_server}},  # type: ignore[typeddict-item]
                )
                async for _ in query(prompt="test prompt", options=options):
                    pass

            assert end_input_called.is_set(), (
                "end_input must be called even with SDK MCP servers (deferred, not skipped)"
            )

        anyio.run(_test)
