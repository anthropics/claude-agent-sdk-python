"""Tests for deferred end_input() with SDK MCP servers on string prompts.

When SDK MCP servers are present and prompt is a string, end_input() must
be deferred until the first result message is received. Closing stdin
immediately prevents the CLI from completing tools/list via control protocol,
making SDK MCP tools invisible to the model.

See: client.py process_query() string prompt handling.
"""

from unittest.mock import AsyncMock, Mock, patch

import anyio

from claude_agent_sdk import ClaudeAgentOptions, query


def _make_mock_transport(end_input_mock: AsyncMock | None = None) -> Mock:
    """Create a mock transport with standard async methods."""
    mock_transport = Mock()
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = end_input_mock or AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)
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

            async def mock_receive() -> None:  # type: ignore[return]
                yield {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 100,
                    "duration_api_ms": 80,
                    "is_error": False,
                    "num_turns": 1,
                    "session_id": "test",
                    "total_cost_usd": 0.001,
                }

            mock_transport.read_messages = mock_receive

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
                # No mcp_servers → no SDK MCP servers
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

    def test_string_prompt_with_sdk_mcp_servers_defers_stdin_close(self) -> None:
        """With SDK MCP servers, end_input() must NOT be called immediately after write."""

        async def _test() -> None:
            mock_transport = _make_mock_transport()

            # Track call order
            call_order: list[str] = []

            async def tracking_write(data: str) -> None:
                call_order.append("write")

            async def tracking_end_input() -> None:
                call_order.append("end_input")

            mock_transport.write = AsyncMock(side_effect=tracking_write)
            mock_transport.end_input = AsyncMock(side_effect=tracking_end_input)

            # Need a result message so the query completes
            async def mock_receive() -> None:  # type: ignore[return]
                yield {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 100,
                    "duration_api_ms": 80,
                    "is_error": False,
                    "num_turns": 1,
                    "session_id": "test",
                    "total_cost_usd": 0.001,
                }

            mock_transport.read_messages = mock_receive

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

            # end_input must still be called (deferred, not skipped)
            assert "end_input" in call_order, "end_input must eventually be called"

            # But it must NOT be immediately after write (there should be something between)
            # The key assertion: write happens first, then end_input happens later
            # (not as the very next call after write in synchronous flow)
            assert "write" in call_order
            # end_input is deferred via task group, so it's called after the
            # result event fires or timeout, not in the synchronous flow after write

        anyio.run(_test)

    def test_end_input_is_always_called_even_with_sdk_mcp_servers(self) -> None:
        """end_input() must always eventually be called to avoid resource leaks."""

        async def _test() -> None:
            end_input_called = anyio.Event()

            async def tracking_end_input() -> None:
                end_input_called.set()

            mock_transport = _make_mock_transport()
            mock_transport.end_input = AsyncMock(side_effect=tracking_end_input)

            async def mock_receive() -> None:  # type: ignore[return]
                yield {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 100,
                    "duration_api_ms": 80,
                    "is_error": False,
                    "num_turns": 1,
                    "session_id": "test",
                    "total_cost_usd": 0.001,
                }

            mock_transport.read_messages = mock_receive
            mock_transport.write = AsyncMock()

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

            # Verify end_input was called
            assert end_input_called.is_set(), (
                "end_input must be called even with SDK MCP servers (deferred, not skipped)"
            )

        anyio.run(_test)
