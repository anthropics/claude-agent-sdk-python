"""Query class for handling bidirectional control protocol."""

import json
import logging
import os
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal

import anyio
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    ListToolsRequest,
)

from ..types import (
    PermissionMode,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    SDKControlPermissionRequest,
    SDKControlRequest,
    SDKControlResponse,
    SDKHookCallbackRequest,
    ToolPermissionContext,
)
from ._task_compat import TaskHandle, spawn_detached
from .transport import Transport

if TYPE_CHECKING:
    from mcp.server import Server as McpServer

    from ..types import SessionKey
    from .transcript_mirror_batcher import TranscriptMirrorBatcher

logger = logging.getLogger(__name__)


def _convert_hook_output_for_cli(hook_output: dict[str, Any]) -> dict[str, Any]:
    """Convert Python-safe field names to CLI-expected field names.

    The Python SDK uses `async_` and `continue_` to avoid keyword conflicts,
    but the CLI expects `async` and `continue`. This function performs the
    necessary conversion.
    """
    converted = {}
    for key, value in hook_output.items():
        # Convert Python-safe names to JavaScript names
        if key == "async_":
            converted["async"] = value
        elif key == "continue_":
            converted["continue"] = value
        else:
            converted[key] = value
    return converted


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
            [str, dict[str, Any], ToolPermissionContext],
            Awaitable[PermissionResultAllow | PermissionResultDeny],
        ]
        | None = None,
        hooks: dict[str, list[dict[str, Any]]] | None = None,
        sdk_mcp_servers: dict[str, "McpServer"] | None = None,
        initialize_timeout: float = 60.0,
        agents: dict[str, dict[str, Any]] | None = None,
        exclude_dynamic_sections: bool | None = None,
        skills: list[str] | Literal["all"] | None = None,
    ):
        """Initialize Query with transport and callbacks.

        Args:
            transport: Low-level transport for I/O
            is_streaming_mode: Whether using streaming (bidirectional) mode
            can_use_tool: Optional callback for tool permission requests
            hooks: Optional hook configurations
            sdk_mcp_servers: Optional SDK MCP server instances
            initialize_timeout: Timeout in seconds for the initialize request
            agents: Optional agent definitions to send via initialize
            exclude_dynamic_sections: Optional preset-prompt flag to send via
                initialize (see ``SystemPromptPreset``)
            skills: Optional skill allowlist to send via initialize so the CLI
                can filter which skills are loaded into the system prompt
        """
        self._initialize_timeout = initialize_timeout
        self.transport = transport
        self.is_streaming_mode = is_streaming_mode
        self.can_use_tool = can_use_tool
        self.hooks = hooks or {}
        self.sdk_mcp_servers = sdk_mcp_servers or {}
        self._agents = agents
        self._exclude_dynamic_sections = exclude_dynamic_sections
        self._skills = skills

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
        self._read_task: TaskHandle | None = None
        self._child_tasks: set[TaskHandle] = set()
        self._inflight_requests: dict[str, TaskHandle] = {}
        self._initialized = False
        self._closed = False
        self._initialization_result: dict[str, Any] | None = None

        # Track first result for proper stream closure with SDK MCP servers
        self._first_result_event = anyio.Event()

        # SessionStore mirroring (set via set_transcript_mirror_batcher)
        self._transcript_mirror_batcher: TranscriptMirrorBatcher | None = None

    def set_transcript_mirror_batcher(self, batcher: "TranscriptMirrorBatcher") -> None:
        """Attach a batcher that receives ``transcript_mirror`` frames.

        When set, the read loop peels ``transcript_mirror`` frames off stdout
        (they are not yielded to consumers), enqueues them on the batcher, and
        flushes before yielding each ``result`` message.
        """
        self._transcript_mirror_batcher = batcher

    def report_mirror_error(self, key: "SessionKey | None", error: str) -> None:
        """Surface a :meth:`SessionStore.append` failure as a system message.

        Called from the batcher's ``on_error``; the dropped batch is not
        retried (at-most-once delivery), so this is the consumer's only signal.
        Non-blocking — if the message buffer is full the error is logged and
        dropped rather than back-pressuring the read loop.
        """
        msg: dict[str, Any] = {
            "type": "system",
            "subtype": "mirror_error",
            "error": error,
            "key": key,
            "uuid": str(uuid.uuid4()),
            "session_id": key.get("session_id", "") if key else "",
        }
        try:
            self._message_send.send_nowait(msg)
        except Exception as e:  # pragma: no cover - buffer-full edge case
            logger.warning("Dropping mirror_error message (buffer full): %s", e)

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
                        hook_matcher_config: dict[str, Any] = {
                            "matcher": matcher.get("matcher"),
                            "hookCallbackIds": callback_ids,
                        }
                        if matcher.get("timeout") is not None:
                            hook_matcher_config["timeout"] = matcher.get("timeout")
                        hooks_config[event].append(hook_matcher_config)

        # Send initialize request
        request: dict[str, Any] = {
            "subtype": "initialize",
            "hooks": hooks_config if hooks_config else None,
        }
        if self._agents:
            request["agents"] = self._agents
        if self._exclude_dynamic_sections is not None:
            request["excludeDynamicSections"] = self._exclude_dynamic_sections
        # 'all' and omitted are equivalent at the wire level (no filter), so
        # only send the field when it's an explicit list.
        if isinstance(self._skills, list):
            request["skills"] = self._skills

        # Use longer timeout for initialize since MCP servers may take time to start
        response = await self._send_control_request(
            request, timeout=self._initialize_timeout
        )
        self._initialized = True
        self._initialization_result = response  # Store for later access
        return response

    async def start(self) -> None:
        """Start reading messages from transport."""
        if self._read_task is None:
            self._read_task = spawn_detached(self._read_messages())

    def spawn_task(self, coro: Any) -> TaskHandle:
        """Spawn a child task that will be cancelled on close()."""
        task = spawn_detached(coro)
        self._child_tasks.add(task)
        task.add_done_callback(self._child_tasks.discard)
        return task

    def _spawn_control_request_handler(self, request: SDKControlRequest) -> None:
        """Spawn a control request handler and track it for cancellation."""
        req_id = request["request_id"]
        task = self.spawn_task(self._handle_control_request(request))
        self._inflight_requests[req_id] = task

        def _done(_t: TaskHandle) -> None:
            self._inflight_requests.pop(req_id, None)

        task.add_done_callback(_done)

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
                    if not self._closed:
                        self._spawn_control_request_handler(request)
                    continue

                elif msg_type == "control_cancel_request":
                    cancel_id = message.get("request_id")
                    if cancel_id:
                        inflight = self._inflight_requests.pop(cancel_id, None)
                        if inflight:
                            inflight.cancel()
                    continue

                elif msg_type == "transcript_mirror":
                    # SessionStore write path: peel mirror frames off stdout
                    # and hand to the batcher; do NOT yield to consumers.
                    if self._transcript_mirror_batcher is not None:
                        self._transcript_mirror_batcher.enqueue(
                            message["filePath"], message["entries"]
                        )
                    continue

                # Track results for proper stream closure
                if msg_type == "result":
                    # Flush pending transcript mirror entries before yielding
                    # result so consumers observing the result can rely on the
                    # SessionStore being up to date for this turn.
                    if self._transcript_mirror_batcher is not None:
                        await self._transcript_mirror_batcher.flush()
                    self._first_result_event.set()

                # Regular SDK messages go to the stream
                await self._message_send.send(message)

        except anyio.get_cancelled_exc_class():
            # Task was cancelled - this is expected behavior
            logger.debug("Read task cancelled")
            raise  # Re-raise to properly handle cancellation
        except Exception as e:
            logger.error(f"Fatal error in message reader: {e}")
            # Signal all pending control requests so they fail fast instead of timing out
            for request_id, event in list(self.pending_control_responses.items()):
                if request_id not in self.pending_control_results:
                    self.pending_control_results[request_id] = e
                    event.set()
            # Put error in stream so iterators can handle it
            await self._message_send.send({"type": "error", "error": str(e)})
        finally:
            # Flush any remaining transcript mirror entries before closing so
            # an early stdout EOF or transport error doesn't drop entries
            # batched this turn. flush() never raises. Shielded so the await
            # still runs when this finally is reached via cancellation.
            if self._transcript_mirror_batcher is not None:
                with anyio.CancelScope(shield=True):
                    await self._transcript_mirror_batcher.flush()
            # Unblock any waiters (e.g. string-prompt path waiting for first
            # result) so they don't stall for the full timeout on early exit.
            self._first_result_event.set()
            # Always signal end of stream. send_nowait: trio's level-triggered
            # cancellation would re-raise Cancelled at an await checkpoint
            # here, dropping the sentinel and leaving receive_messages() hung.
            # close() is the fallback for the buffer-full case where
            # send_nowait raises WouldBlock — receivers then exit on
            # EndOfStream after draining.
            with suppress(anyio.WouldBlock):
                self._message_send.send_nowait({"type": "end"})
            self._message_send.close()

    async def _handle_control_request(self, request: SDKControlRequest) -> None:
        """Handle incoming control request from CLI."""
        request_id = request["request_id"]
        request_data = request["request"]
        subtype = request_data["subtype"]

        try:
            response_data: dict[str, Any] = {}

            if subtype == "can_use_tool":
                permission_request: SDKControlPermissionRequest = request_data  # type: ignore[assignment]
                original_input = permission_request["input"]
                # Handle tool permission request
                if not self.can_use_tool:
                    raise Exception("canUseTool callback is not provided")

                context = ToolPermissionContext(
                    signal=None,  # TODO: Add abort signal support
                    suggestions=[
                        PermissionUpdate.from_dict(s)
                        for s in (
                            permission_request.get("permission_suggestions") or []
                        )
                    ],
                    tool_use_id=permission_request.get("tool_use_id"),
                    agent_id=permission_request.get("agent_id"),
                    blocked_path=permission_request.get("blocked_path"),
                    decision_reason=permission_request.get("decision_reason"),
                    title=permission_request.get("title"),
                    display_name=permission_request.get("display_name"),
                    description=permission_request.get("description"),
                )

                response = await self.can_use_tool(
                    permission_request["tool_name"],
                    permission_request["input"],
                    context,
                )

                # Convert PermissionResult to expected dict format
                if isinstance(response, PermissionResultAllow):
                    response_data = {
                        "behavior": "allow",
                        "updatedInput": (
                            response.updated_input
                            if response.updated_input is not None
                            else original_input
                        ),
                    }
                    if response.updated_permissions is not None:
                        response_data["updatedPermissions"] = [
                            permission.to_dict()
                            for permission in response.updated_permissions
                        ]
                elif isinstance(response, PermissionResultDeny):
                    response_data = {"behavior": "deny", "message": response.message}
                    if response.interrupt:
                        response_data["interrupt"] = response.interrupt
                else:
                    raise TypeError(
                        f"Tool permission callback must return PermissionResult (PermissionResultAllow or PermissionResultDeny), got {type(response)}"
                    )

            elif subtype == "hook_callback":
                hook_callback_request: SDKHookCallbackRequest = request_data  # type: ignore[assignment]
                # Handle hook callback
                callback_id = hook_callback_request["callback_id"]
                callback = self.hook_callbacks.get(callback_id)
                if not callback:
                    raise Exception(f"No hook callback found for ID: {callback_id}")

                hook_output = await callback(
                    request_data.get("input"),
                    request_data.get("tool_use_id"),
                    {"signal": None},  # TODO: Add abort signal support
                )
                # Convert Python-safe field names (async_, continue_) to CLI-expected names (async, continue)
                response_data = _convert_hook_output_for_cli(hook_output)

            elif subtype == "mcp_message":
                # Handle SDK MCP request
                server_name = request_data.get("server_name")
                mcp_message = request_data.get("message")

                if not server_name or not mcp_message:
                    raise Exception("Missing server_name or message for MCP request")

                # Type narrowing - we've verified these are not None above
                assert isinstance(server_name, str)
                assert isinstance(mcp_message, dict)
                mcp_response = await self._handle_sdk_mcp_request(
                    server_name, mcp_message
                )
                # Wrap the MCP response as expected by the control protocol
                response_data = {"mcp_response": mcp_response}

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

        except anyio.get_cancelled_exc_class():
            # Request was cancelled via control_cancel_request; the CLI has
            # already abandoned this request, so don't write a response.
            raise
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

    async def _send_control_request(
        self, request: dict[str, Any], timeout: float = 60.0
    ) -> dict[str, Any]:
        """Send control request to CLI and wait for response.

        Args:
            request: The control request to send
            timeout: Timeout in seconds to wait for response (default 60s)
        """
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
            with anyio.fail_after(timeout):
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

    async def _handle_sdk_mcp_request(
        self, server_name: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle an MCP request for an SDK server.

        This acts as a bridge between JSONRPC messages from the CLI
        and the in-process MCP server. Ideally the MCP SDK would provide
        a method to handle raw JSONRPC, but for now we route manually.

        Args:
            server_name: Name of the SDK MCP server
            message: The JSONRPC message

        Returns:
            The response message
        """
        if server_name not in self.sdk_mcp_servers:
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {
                    "code": -32601,
                    "message": f"Server '{server_name}' not found",
                },
            }

        server = self.sdk_mcp_servers[server_name]
        method = message.get("method")
        params = message.get("params", {})

        try:
            # TODO: Python MCP SDK lacks the Transport abstraction that TypeScript has.
            # TypeScript: server.connect(transport) allows custom transports
            # Python: server.run(read_stream, write_stream) requires actual streams
            #
            # This forces us to manually route methods. When Python MCP adds Transport
            # support, we can refactor to match the TypeScript approach.
            if method == "initialize":
                # Handle MCP initialization - hardcoded for tools only, no listChanged
                return {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}  # Tools capability without listChanged
                        },
                        "serverInfo": {
                            "name": server.name,
                            "version": server.version or "1.0.0",
                        },
                    },
                }

            elif method == "tools/list":
                request = ListToolsRequest(method=method)
                handler = server.request_handlers.get(ListToolsRequest)
                if handler:
                    result = await handler(request)
                    # Convert MCP result to JSONRPC response
                    tools_data = []
                    for tool in result.root.tools:  # type: ignore[union-attr]
                        tool_data: dict[str, Any] = {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": (
                                tool.inputSchema.model_dump()
                                if hasattr(tool.inputSchema, "model_dump")
                                else tool.inputSchema
                            )
                            if tool.inputSchema
                            else {},
                        }
                        if tool.annotations:
                            tool_data["annotations"] = tool.annotations.model_dump(
                                exclude_none=True
                            )
                        if tool.meta:
                            tool_data["_meta"] = tool.meta
                        tools_data.append(tool_data)
                    return {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "result": {"tools": tools_data},
                    }

            elif method == "tools/call":
                call_request = CallToolRequest(
                    method=method,
                    params=CallToolRequestParams(
                        name=params.get("name"), arguments=params.get("arguments", {})
                    ),
                )
                handler = server.request_handlers.get(CallToolRequest)
                if handler:
                    result = await handler(call_request)
                    # Convert MCP result to JSONRPC response
                    content = []
                    for item in result.root.content:  # type: ignore[union-attr]
                        item_type = getattr(item, "type", None)
                        if item_type == "text":
                            content.append(
                                {"type": "text", "text": getattr(item, "text", "")}
                            )
                        elif item_type == "image":
                            content.append(
                                {
                                    "type": "image",
                                    "data": getattr(item, "data", ""),
                                    "mimeType": getattr(item, "mimeType", ""),
                                }
                            )
                        elif item_type == "resource_link":
                            parts = []
                            name = getattr(item, "name", None)
                            uri = getattr(item, "uri", None)
                            desc = getattr(item, "description", None)
                            if name:
                                parts.append(name)
                            if uri:
                                parts.append(str(uri))
                            if desc:
                                parts.append(desc)
                            content.append(
                                {
                                    "type": "text",
                                    "text": "\n".join(parts)
                                    if parts
                                    else "Resource link",
                                }
                            )
                        elif item_type == "resource":
                            resource = getattr(item, "resource", None)
                            if resource and hasattr(resource, "text"):
                                content.append({"type": "text", "text": resource.text})
                            else:
                                logger.warning(
                                    "Binary embedded resource cannot be converted to text, skipping"
                                )
                        else:
                            logger.warning(
                                "Unsupported content type %r in tool result, skipping",
                                item_type,
                            )

                    response_data = {"content": content}
                    if hasattr(result.root, "isError") and result.root.isError:
                        response_data["isError"] = True  # type: ignore[assignment]

                    return {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "result": response_data,
                    }

            elif method == "notifications/initialized":
                # Handle initialized notification - just acknowledge it
                return {"jsonrpc": "2.0", "result": {}}

            # Add more methods here as MCP SDK adds them (resources, prompts, etc.)
            # This is the limitation Ashwin pointed out - we have to manually update

            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": f"Method '{method}' not found"},
            }

        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32603, "message": str(e)},
            }

    async def get_mcp_status(self) -> dict[str, Any]:
        """Get current MCP server connection status."""
        return await self._send_control_request({"subtype": "mcp_status"})

    async def get_context_usage(self) -> dict[str, Any]:
        """Get a breakdown of current context window usage by category."""
        return await self._send_control_request({"subtype": "get_context_usage"})

    async def interrupt(self) -> None:
        """Send interrupt control request."""
        await self._send_control_request({"subtype": "interrupt"})

    async def set_permission_mode(self, mode: PermissionMode) -> None:
        """Change permission mode."""
        await self._send_control_request(
            {
                "subtype": "set_permission_mode",
                "mode": mode,
            }
        )

    async def set_model(self, model: str | None) -> None:
        """Change the AI model."""
        await self._send_control_request(
            {
                "subtype": "set_model",
                "model": model,
            }
        )

    async def rewind_files(self, user_message_id: str) -> None:
        """Rewind tracked files to their state at a specific user message.

        Requires file checkpointing to be enabled via the `enable_file_checkpointing` option.

        Args:
            user_message_id: UUID of the user message to rewind to
        """
        await self._send_control_request(
            {
                "subtype": "rewind_files",
                "user_message_id": user_message_id,
            }
        )

    async def reconnect_mcp_server(self, server_name: str) -> None:
        """Reconnect a disconnected or failed MCP server.

        Args:
            server_name: The name of the MCP server to reconnect
        """
        await self._send_control_request(
            {
                "subtype": "mcp_reconnect",
                "serverName": server_name,
            }
        )

    async def toggle_mcp_server(self, server_name: str, enabled: bool) -> None:
        """Enable or disable an MCP server.

        Args:
            server_name: The name of the MCP server to toggle
            enabled: Whether the server should be enabled
        """
        await self._send_control_request(
            {
                "subtype": "mcp_toggle",
                "serverName": server_name,
                "enabled": enabled,
            }
        )

    async def stop_task(self, task_id: str) -> None:
        """Stop a running task.

        Args:
            task_id: The task ID from task_notification events
        """
        await self._send_control_request(
            {
                "subtype": "stop_task",
                "task_id": task_id,
            }
        )

    async def wait_for_result_and_end_input(self) -> None:
        """Wait for the first result (if needed) then close stdin.

        If SDK MCP servers or hooks require bidirectional communication,
        keeps stdin open until the first result arrives. The control protocol
        requires stdin to remain open for the entire conversation, so no
        timeout is applied. The event is guaranteed to fire: either when the
        result message arrives, or in _read_messages' finally block if the
        process exits early.
        """
        if self.sdk_mcp_servers or self.hooks:
            logger.debug(
                "Waiting for first result before closing stdin "
                f"(sdk_mcp_servers={len(self.sdk_mcp_servers)}, "
                f"has_hooks={bool(self.hooks)})"
            )
            await self._first_result_event.wait()

        await self.transport.end_input()

    async def stream_input(self, stream: AsyncIterable[dict[str, Any]]) -> None:
        """Stream input messages to transport.

        If SDK MCP servers or hooks are present, waits for the first result
        before closing stdin to allow bidirectional control protocol communication.
        """
        try:
            async for message in stream:
                if self._closed:
                    break
                await self.transport.write(json.dumps(message) + "\n")

            await self.wait_for_result_and_end_input()
        except Exception as e:
            logger.debug(f"Error streaming input: {e}")

    async def receive_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Receive SDK messages (not control messages)."""
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
        # Final-flush mirror entries before tearing down so .return()/break
        # don't drop the current turn when the process exits immediately.
        if self._transcript_mirror_batcher is not None:
            await self._transcript_mirror_batcher.close()
        for task in list(self._child_tasks):
            task.cancel()
        if self._read_task is not None and not self._read_task.done():
            self._read_task.cancel()
            await self._read_task.wait()
        self._read_task = None
        # The read task's finally closed the send side; repeat here for the
        # case where start() was never called. Do NOT close the receive
        # side — it belongs to the consumer, and anyio's receive_nowait()
        # checks _closed before the buffer, so closing it here would make a
        # non-parked consumer drop buffered messages with
        # ClosedResourceError. _message_send.close() alone yields
        # EndOfStream after the buffer drains; the consumer calls
        # close_receive_stream() once it's done iterating (#859).
        self._message_send.close()
        await self.transport.close()

    def close_receive_stream(self) -> None:
        """Close the receive side of the message stream.

        Call once the consumer has finished iterating ``receive_messages()``.
        ``close()`` leaves this open so a still-draining consumer can read
        buffered messages; the consumer is responsible for closing it to
        avoid a ``ResourceWarning`` from anyio's ``__del__``.
        """
        self._message_receive.close()

    # Make Query an async iterator
    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        """Return async iterator for messages."""
        return self.receive_messages()

    async def __anext__(self) -> dict[str, Any]:
        """Get next message."""
        async for message in self.receive_messages():
            return message
        raise StopAsyncIteration
