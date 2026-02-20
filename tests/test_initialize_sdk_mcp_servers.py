"""Tests for Query.initialize() sending sdkMcpServers field.

The Claude Code CLI registers SDK MCP servers from the sdkMcpServers field
in the initialize control request. Without this field, SDK MCP tools are
invisible to the model.

See: TypeScript SDK sends sdkMcpServers in initialize request.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio

from claude_agent_sdk._internal.query import Query


def _create_recording_transport() -> tuple[MagicMock, list[str]]:
    """Create a mock transport that records written data and provides responses."""
    mock_transport = MagicMock()
    written_data: list[str] = []

    async def mock_write(data: str) -> None:
        written_data.append(data)

    mock_transport.write = AsyncMock(side_effect=mock_write)
    return mock_transport, written_data


class TestInitializeSdkMcpServers:
    """Test that Query.initialize() includes sdkMcpServers in the request."""

    def test_initialize_includes_sdk_mcp_server_names(self) -> None:
        """initialize() should include sdkMcpServers field when SDK MCP servers exist."""

        async def _test() -> None:
            mock_transport, written_data = _create_recording_transport()

            # Create mock MCP server instances
            mock_server_a = MagicMock()
            mock_server_b = MagicMock()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                sdk_mcp_servers={"server-a": mock_server_a, "server-b": mock_server_b},
            )

            # Intercept _send_control_request to capture the request dict
            captured_requests: list[dict[str, Any]] = []

            async def capturing_send(
                request: dict[str, Any], timeout: float = 60.0
            ) -> dict[str, Any]:
                captured_requests.append(request.copy())
                return {"supportedCommands": []}

            query._send_control_request = capturing_send  # type: ignore[assignment]

            await query.initialize()

            # Verify sdkMcpServers was included in the request
            assert len(captured_requests) == 1
            request = captured_requests[0]
            assert "sdkMcpServers" in request, (
                "initialize() must include sdkMcpServers field"
            )
            assert sorted(request["sdkMcpServers"]) == ["server-a", "server-b"]

        anyio.run(_test)

    def test_initialize_omits_sdk_mcp_servers_when_empty(self) -> None:
        """initialize() should not include sdkMcpServers when no SDK MCP servers."""

        async def _test() -> None:
            mock_transport, _ = _create_recording_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                sdk_mcp_servers={},
            )

            captured_requests: list[dict[str, Any]] = []

            async def capturing_send(
                request: dict[str, Any], timeout: float = 60.0
            ) -> dict[str, Any]:
                captured_requests.append(request.copy())
                return {"supportedCommands": []}

            query._send_control_request = capturing_send  # type: ignore[assignment]

            await query.initialize()

            assert len(captured_requests) == 1
            request = captured_requests[0]
            assert "sdkMcpServers" not in request

        anyio.run(_test)

    def test_initialize_omits_sdk_mcp_servers_when_none(self) -> None:
        """initialize() should not include sdkMcpServers when sdk_mcp_servers is None."""

        async def _test() -> None:
            mock_transport, _ = _create_recording_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                sdk_mcp_servers=None,
            )

            captured_requests: list[dict[str, Any]] = []

            async def capturing_send(
                request: dict[str, Any], timeout: float = 60.0
            ) -> dict[str, Any]:
                captured_requests.append(request.copy())
                return {"supportedCommands": []}

            query._send_control_request = capturing_send  # type: ignore[assignment]

            await query.initialize()

            assert len(captured_requests) == 1
            request = captured_requests[0]
            assert "sdkMcpServers" not in request

        anyio.run(_test)

    def test_initialize_returns_none_when_not_streaming(self) -> None:
        """initialize() should return None and not send request when not streaming."""

        async def _test() -> None:
            mock_transport, _ = _create_recording_transport()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=False,
                sdk_mcp_servers={"server": MagicMock()},
            )

            result = await query.initialize()

            assert result is None
            mock_transport.write.assert_not_called()

        anyio.run(_test)

    def test_initialize_includes_both_agents_and_sdk_mcp_servers(self) -> None:
        """initialize() should include both agents and sdkMcpServers when both exist."""

        async def _test() -> None:
            mock_transport, _ = _create_recording_transport()

            mock_server = MagicMock()
            agents = {"agent-1": {"name": "Agent One"}}

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                sdk_mcp_servers={"team": mock_server},
                agents=agents,
            )

            captured_requests: list[dict[str, Any]] = []

            async def capturing_send(
                request: dict[str, Any], timeout: float = 60.0
            ) -> dict[str, Any]:
                captured_requests.append(request.copy())
                return {"supportedCommands": []}

            query._send_control_request = capturing_send  # type: ignore[assignment]

            await query.initialize()

            assert len(captured_requests) == 1
            request = captured_requests[0]
            assert request["sdkMcpServers"] == ["team"]
            assert request["agents"] == agents

        anyio.run(_test)
