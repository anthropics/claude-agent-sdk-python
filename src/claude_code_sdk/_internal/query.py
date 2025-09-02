"""Query class for handling bidirectional control protocol."""

import json
import logging
import os
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

import anyio

from ..types import (
    SDKControlPermissionRequest,
    SDKControlRequest,
    SDKControlResponse,
    SDKHookCallbackRequest,
)
from .transport import Transport

logger = logging.getLogger(__name__)


class Query:
    """Handles bidirectional control protocol on top of Transport.

    This class manages:
    - Control request/response routing
    - Hook callbacks
    - Tool permission callbacks
    - Message streaming
    - Initialization handshake
    """

    def __init__(
        self,
        transport: Transport,
        is_streaming_mode: bool,
        can_use_tool: Callable[
            [str, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]
        ]
        | None = None,
        hooks: dict[str, list[dict[str, Any]]] | None = None,
    ):
        """Initialize Query with transport and callbacks.

        Args:
            transport: Low-level transport for I/O
            is_streaming_mode: Whether using streaming (bidirectional) mode
            can_use_tool: Optional callback for tool permission requests
            hooks: Optional hook configurations
        """
        self.transport = transport
        self.is_streaming_mode = is_streaming_mode
        self.can_use_tool = can_use_tool
        self.hooks = hooks or {}

        # Control protocol state
        self.pending_control_responses: dict[str, anyio.Event] = {}
        self.pending_control_results: dict[str, dict[str, Any] | Exception] = {}
        self.hook_callbacks: dict[str, Callable[..., Any]] = {}
        self.next_callback_id = 0
        self._request_counter = 0

        # Message stream
        self._message_send, self._message_receive = anyio.create_memory_object_stream[
            dict[str, Any]
        ](max_buffer_size=100)
        self._tg: anyio.abc.TaskGroup | None = None
        self._initialized = False
        self._closed = False
        self._initialization_result: dict[str, Any] | None = None

    async def initialize(self) -> dict[str, Any] | None:
        """Initialize control protocol if in streaming mode.

        Returns:
            Initialize response with supported commands, or None if not streaming
        """
        if not self.is_streaming_mode:
            return None

        # Build hooks configuration for initialization
        hooks_config: dict[str, Any] = {}
        if self.hooks:
            for event, matchers in self.hooks.items():
                if matchers:
                    hooks_config[event] = []
                    for matcher in matchers:
                        callback_ids = []
                        for callback in matcher.get("hooks", []):
                            callback_id = f"hook_{self.next_callback_id}"
                            self.next_callback_id += 1
                            self.hook_callbacks[callback_id] = callback
                            callback_ids.append(callback_id)
                        hooks_config[event].append(
                            {
                                "matcher": matcher.get("matcher"),
                                "hookCallbackIds": callback_ids,
                            }
                        )

        # Send initialize request
        request = {
            "subtype": "initialize",
            "hooks": hooks_config if hooks_config else None,
        }

        response = await self._send_control_request(request)
        self._initialized = True
        self._initialization_result = response  # Store for later access
        return response

    async def start(self) -> None:
        """Start reading messages from transport."""
        if self._tg is None:
            self._tg = anyio.create_task_group()
            await self._tg.__aenter__()
            self._tg.start_soon(self._read_messages)

    async def _read_messages(self) -> None:
        """Read messages from transport and route them."""
        try:
            async for message in self.transport.read_messages():
                if self._closed:
                    break

                msg_type = message.get("type")

                # Route control messages
                if msg_type == "control_response":
                    response = message.get("response", {})
                    request_id = response.get("request_id")
                    if request_id in self.pending_control_responses:
                        event = self.pending_control_responses[request_id]
                        if response.get("subtype") == "error":
                            self.pending_control_results[request_id] = Exception(
                                response.get("error", "Unknown error")
                            )
                        else:
                            self.pending_control_results[request_id] = response
                        event.set()
                    continue

                elif msg_type == "control_request":
                    # Handle incoming control requests from CLI
                    # Cast message to SDKControlRequest for type safety
                    request: SDKControlRequest = message  # type: ignore[assignment]
                    if self._tg:
                        self._tg.start_soon(self._handle_control_request, request)
                    continue

                elif msg_type == "control_cancel_request":
                    # Handle cancel requests
                    # TODO: Implement cancellation support
                    continue

                # Regular SDK messages go to the stream
                await self._message_send.send(message)

        except anyio.get_cancelled_exc_class():
            # Task was cancelled - this is expected behavior
            logger.debug("Read task cancelled")
            raise  # Re-raise to properly handle cancellation
        except Exception as e:
            logger.error(f"Fatal error in message reader: {e}")
            # Put error in stream so iterators can handle it
            await self._message_send.send({"type": "error", "error": str(e)})
        finally:
            # Always signal end of stream
            await self._message_send.send({"type": "end"})

    async def _handle_control_request(self, request: SDKControlRequest) -> None:
        """Handle incoming control request from CLI."""
        request_id = request["request_id"]
        request_data = request["request"]
        subtype = request_data["subtype"]

        try:
            response_data = {}

            if subtype == "can_use_tool":
                permission_request: SDKControlPermissionRequest = request_data  # type: ignore[assignment]
                # Handle tool permission request
                if not self.can_use_tool:
                    raise Exception("canUseTool callback is not provided")

                response_data = await self.can_use_tool(
                    permission_request["tool_name"],
                    permission_request["input"],
                    {
                        "signal": None,  # TODO: Add abort signal support
                        "suggestions": permission_request.get("permission_suggestions"),
                    },
                )

            elif subtype == "hook_callback":
                hook_callback_request: SDKHookCallbackRequest = request_data  # type: ignore[assignment]
                # Handle hook callback
                callback_id = hook_callback_request["callback_id"]
                callback = self.hook_callbacks.get(callback_id)
                if not callback:
                    raise Exception(f"No hook callback found for ID: {callback_id}")

                response_data = await callback(
                    request_data.get("input"),
                    request_data.get("tool_use_id"),
                    {"signal": None},  # TODO: Add abort signal support
                )

            else:
                raise Exception(f"Unsupported control request subtype: {subtype}")

            # Send success response
            success_response: SDKControlResponse = {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": response_data,
                },
            }
            await self.transport.write(json.dumps(success_response) + "\n")

        except Exception as e:
            # Send error response
            error_response: SDKControlResponse = {
                "type": "control_response",
                "response": {
                    "subtype": "error",
                    "request_id": request_id,
                    "error": str(e),
                },
            }
            await self.transport.write(json.dumps(error_response) + "\n")

    async def _send_control_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send control request to CLI and wait for response."""
        if not self.is_streaming_mode:
            raise Exception("Control requests require streaming mode")

        # Generate unique request ID
        self._request_counter += 1
        request_id = f"req_{self._request_counter}_{os.urandom(4).hex()}"

        # Create event for response
        event = anyio.Event()
        self.pending_control_responses[request_id] = event

        # Build and send request
        control_request = {
            "type": "control_request",
            "request_id": request_id,
            "request": request,
        }

        await self.transport.write(json.dumps(control_request) + "\n")

        # Wait for response
        try:
            with anyio.fail_after(60.0):
                await event.wait()

            result = self.pending_control_results.pop(request_id)
            self.pending_control_responses.pop(request_id, None)

            if isinstance(result, Exception):
                raise result

            response_data = result.get("response", {})
            return response_data if isinstance(response_data, dict) else {}
        except TimeoutError as e:
            self.pending_control_responses.pop(request_id, None)
            self.pending_control_results.pop(request_id, None)
            raise Exception(f"Control request timeout: {request.get('subtype')}") from e

    async def interrupt(self) -> None:
        """Send interrupt control request."""
        await self._send_control_request({"subtype": "interrupt"})

    async def set_permission_mode(self, mode: str) -> None:
        """Change permission mode."""
        await self._send_control_request(
            {
                "subtype": "set_permission_mode",
                "mode": mode,
            }
        )

    async def stream_input(self, stream: AsyncIterable[dict[str, Any]]) -> None:
        """Stream input messages to transport."""
        try:
            async for message in stream:
                if self._closed:
                    break
                await self.transport.write(json.dumps(message) + "\n")
            # After all messages sent, end input
            await self.transport.end_input()
        except Exception as e:
            logger.debug(f"Error streaming input: {e}")

    async def receive_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Receive SDK messages (not control messages)."""
        async with self._message_receive:
            async for message in self._message_receive:
                # Check for special messages
                if message.get("type") == "end":
                    break
                elif message.get("type") == "error":
                    raise Exception(message.get("error", "Unknown error"))

                yield message

    async def close(self) -> None:
        """Close the query and transport."""
        self._closed = True
        if self._tg:
            self._tg.cancel_scope.cancel()
            # Wait for task group to complete cancellation
            with suppress(anyio.get_cancelled_exc_class()):
                await self._tg.__aexit__(None, None, None)
        await self.transport.close()

    # Make Query an async iterator
    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        """Return async iterator for messages."""
        return self.receive_messages()

    async def __anext__(self) -> dict[str, Any]:
        """Get next message."""
        async for message in self.receive_messages():
            return message
        raise StopAsyncIteration
