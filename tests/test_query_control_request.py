"""Tests for Query._handle_control_request() race condition handling."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from claude_agent_sdk import CLIConnectionError
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk.types import (
    PermissionResultAllow,
    SDKControlRequest,
)


def create_mock_transport_raising_on_write() -> MagicMock:
    """Create a mock transport that raises CLIConnectionError on write."""
    mock_transport = MagicMock()
    mock_transport.write = AsyncMock(side_effect=CLIConnectionError("Transport closed"))
    return mock_transport


def create_mock_transport_working() -> tuple[MagicMock, list[str]]:
    """Create a working mock transport that records written data."""
    mock_transport = MagicMock()
    written_data: list[str] = []

    async def mock_write(data: str) -> None:
        written_data.append(data)

    mock_transport.write = AsyncMock(side_effect=mock_write)
    return mock_transport, written_data


def _make_can_use_tool_request(request_id: str) -> SDKControlRequest:
    """Create a can_use_tool control request for testing."""
    return cast(
        SDKControlRequest,
        {
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "test_tool",
                "input": {"arg": "value"},
            },
        },
    )


def _make_mcp_message_request(request_id: str) -> SDKControlRequest:
    """Create an mcp_message control request for testing (with None server_name)."""
    return cast(
        SDKControlRequest,
        {
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "mcp_message",
                "server_name": None,  # Will trigger error path
                "message": {},
            },
        },
    )


def _make_hook_callback_request(request_id: str) -> SDKControlRequest:
    """Create a hook_callback control request for testing."""
    return cast(
        SDKControlRequest,
        {
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "hook_callback",
                "callback_id": "hook_0",
                "input": {"test": "data"},
                "tool_use_id": "tool-123",
            },
        },
    )


class TestHandleControlRequestRaceCondition:
    """Test race condition handling in _handle_control_request()."""

    def test_success_response_on_closed_transport(self) -> None:
        """Transport closed before success response - should not raise."""

        async def _test() -> None:
            mock_transport = create_mock_transport_raising_on_write()

            # Create a can_use_tool callback that returns Allow
            async def mock_can_use_tool(
                tool_name: str,
                tool_input: dict[str, Any],
                context: object,
            ) -> PermissionResultAllow:
                return PermissionResultAllow()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                can_use_tool=mock_can_use_tool,
            )

            request = _make_can_use_tool_request("test-request-1")

            # Should not raise - CLIConnectionError should be caught
            await query._handle_control_request(request)

            # Verify write was attempted
            mock_transport.write.assert_called_once()

        anyio.run(_test)

    def test_error_response_on_closed_transport(self) -> None:
        """Transport closed before error response - should not raise."""

        async def _test() -> None:
            mock_transport = create_mock_transport_raising_on_write()

            # Create a can_use_tool callback that raises an exception
            async def mock_can_use_tool_error(
                tool_name: str,
                tool_input: dict[str, Any],
                context: object,
            ) -> PermissionResultAllow:
                raise ValueError("Callback error")

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                can_use_tool=mock_can_use_tool_error,
            )

            request = _make_can_use_tool_request("test-request-2")

            # Should not raise - CLIConnectionError in error path should be caught
            await query._handle_control_request(request)

            # Verify write was attempted (for error response)
            mock_transport.write.assert_called_once()

        anyio.run(_test)

    def test_mcp_message_on_closed_transport(self) -> None:
        """MCP message response on closed transport - should not raise."""

        async def _test() -> None:
            mock_transport = create_mock_transport_raising_on_write()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            request = _make_mcp_message_request("test-request-3")

            # Should not raise - error response CLIConnectionError should be caught
            await query._handle_control_request(request)

            # Verify write was attempted
            mock_transport.write.assert_called_once()

        anyio.run(_test)

    def test_normal_operation_unaffected(self) -> None:
        """Normal operation still works correctly."""

        async def _test() -> None:
            mock_transport, written_data = create_mock_transport_working()

            # Create a can_use_tool callback that returns Allow
            async def mock_can_use_tool(
                tool_name: str,
                tool_input: dict[str, Any],
                context: object,
            ) -> PermissionResultAllow:
                return PermissionResultAllow(updated_input={"modified": True})

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                can_use_tool=mock_can_use_tool,
            )

            request = _make_can_use_tool_request("test-request-4")

            await query._handle_control_request(request)

            # Verify response was written correctly
            assert len(written_data) == 1
            response = json.loads(written_data[0].strip())
            assert response["type"] == "control_response"
            assert response["response"]["subtype"] == "success"
            assert response["response"]["request_id"] == "test-request-4"
            assert response["response"]["response"]["behavior"] == "allow"
            assert response["response"]["response"]["updatedInput"] == {
                "modified": True
            }

        anyio.run(_test)

    def test_hook_callback_on_closed_transport(self) -> None:
        """Hook callback response on closed transport - should not raise."""

        async def _test() -> None:
            mock_transport = create_mock_transport_raising_on_write()

            # Create a hook callback
            async def mock_hook(
                input_data: dict[str, Any] | None,
                tool_use_id: str | None,
                context: dict[str, Any],
            ) -> dict[str, Any]:
                return {"continue_": True}

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )
            # Register the hook callback
            query.hook_callbacks["hook_0"] = mock_hook

            request = _make_hook_callback_request("test-request-5")

            # Should not raise - CLIConnectionError should be caught
            await query._handle_control_request(request)

            # Verify write was attempted
            mock_transport.write.assert_called_once()

        anyio.run(_test)

    def test_other_exceptions_still_propagate(self) -> None:
        """Non-CLIConnectionError exceptions should still propagate."""

        async def _test() -> None:
            mock_transport = MagicMock()
            mock_transport.write = AsyncMock(
                side_effect=RuntimeError("Unexpected error")
            )

            # Create a can_use_tool callback that returns Allow
            async def mock_can_use_tool(
                tool_name: str,
                tool_input: dict[str, Any],
                context: object,
            ) -> PermissionResultAllow:
                return PermissionResultAllow()

            query = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                can_use_tool=mock_can_use_tool,
            )

            request = _make_can_use_tool_request("test-request-6")

            # RuntimeError should still propagate
            with pytest.raises(RuntimeError, match="Unexpected error"):
                await query._handle_control_request(request)

        anyio.run(_test)
