"""The one place that knows which major version of ``mcp`` is installed.

The SDK supports both mcp 1.x and 2.x. Almost everything it needs (the
in-memory transport, ``Server.run``, ``SessionMessage``, the pydantic models
and their camelCase wire aliases) is identical across the two, so the rest of
the package is written once against that shared surface. The things that
genuinely differ live here:

- decoding a raw JSON-RPC dict into the message model (a ``RootModel`` on
  1.x, a plain union behind a ``TypeAdapter`` on 2.x), including how a
  request that the server settles without a response gets ended, and
- registering ``tools/list`` / ``tools/call`` handlers on a lowlevel
  ``Server`` (decorators on 1.x, constructor callbacks on 2.x), and
- whether a served ``Server`` can take a client's cancellation (any server
  on 2.x; on 1.x only the ones built here, see ``can_cancel_requests``).

Nothing outside this module may branch on the mcp version.
"""

from __future__ import annotations

import weakref
from collections.abc import Awaitable, Callable
from typing import Any

import anyio.lowlevel
import mcp.shared.message
import mcp.types
from mcp.server import Server
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, ListToolsResult, RequestId, Tool

# mcp 2 replaced the JSONRPCMessage RootModel with a TypeAdapter over a plain
# union, and every other difference handled below arrived in the same
# release, so its presence is what tells the two supported majors apart.
MCP_MAJOR: int = 2 if hasattr(mcp.types, "jsonrpc_message_adapter") else 1
"""Major version of the installed ``mcp`` package."""

# Only one major is ever installed, so a type checker running against either
# one sees the other major's calls below as errors. Erasing to Any at these
# names keeps both branches checkable without per-line ignores (which
# ``warn_unused_ignores`` would reject on whichever major does not need them).
_types: Any = mcp.types
_message: Any = mcp.shared.message
_Server: Any = Server

# JSON-RPC error answering a request that ended without a response because
# the client cancelled it. mcp 1.x servers send this themselves (with code
# 0); mcp 2.x servers stay silent and leave ending the request to the
# transport, and this is what mcp 2's own HTTP transport answers with.
_REQUEST_CANCELLED = {"code": -32800, "message": "Request cancelled"}

Respond = Callable[[dict[str, Any]], None]


def parse_jsonrpc(
    message: dict[str, Any], respond: Respond
) -> tuple[SessionMessage, RequestId | None]:
    """Decode a raw JSON-RPC dict into the transport message ``Server.run`` reads.

    Returns the message together with its request id when mcp parsed it as a
    request (so exactly one place decides whether a response is due), or
    ``None`` for notifications and responses. ``respond`` is where responses
    for this connection are delivered; it is used to end a request the server
    settles without writing anything back.
    """
    if MCP_MAJOR >= 2:
        parsed = _types.jsonrpc_message_adapter.validate_python(message, by_name=False)
        if not isinstance(parsed, _types.JSONRPCRequest):
            return SessionMessage(parsed), None

        async def unanswered() -> None:
            respond({"jsonrpc": "2.0", "id": parsed.id, "error": _REQUEST_CANCELLED})

        metadata = _message.ServerMessageMetadata(on_request_unanswered=unanswered)
        return SessionMessage(parsed, metadata=metadata), parsed.id

    wrapped = _types.JSONRPCMessage.model_validate(message)
    request = wrapped.root if isinstance(wrapped.root, _types.JSONRPCRequest) else None
    return SessionMessage(wrapped), None if request is None else request.id


def dump_jsonrpc(message: SessionMessage) -> dict[str, Any]:
    """Render a transport message back to a raw JSON-RPC dict for the CLI."""
    dumped: dict[str, Any] = message.message.model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    return dumped


ToolRunner = Callable[[str, dict[str, Any]], Awaitable[CallToolResult]]

# Servers built here, whose tool handler is known to end at a checkpoint when
# its call was cancelled (see build_tool_server).
_cancel_safe: weakref.WeakSet[Server] = weakref.WeakSet()


def can_cancel_requests(server: Server) -> bool:
    """Whether a client's ``notifications/cancelled`` may be passed to ``server``.

    mcp 1.x answers a cancelled request itself and stops the whole server if
    the handler then answers as well, which any handler that returns after
    the cancellation (one waiting on a worker thread, say) will do. So on
    1.x cancellation is only safe for servers built by ``build_tool_server``;
    a hand-built server's tools are left to run to completion instead, as
    they always have. mcp 2.x discards a cancelled handler's result, so
    every server can take it.
    """
    return MCP_MAJOR >= 2 or server in _cancel_safe


def build_tool_server(
    name: str, version: str, tools: list[Tool], run_tool: ToolRunner
) -> Server:
    """Build a lowlevel ``Server`` that lists ``tools`` and delegates calls to ``run_tool``.

    ``run_tool`` owns argument validation, error mapping and content shaping,
    so tool behavior does not depend on either major's defaults. On 1.x that
    means turning off the decorator's own jsonschema validation.
    """
    if MCP_MAJOR >= 2:

        async def on_list_tools(ctx: Any, params: Any) -> ListToolsResult:
            return ListToolsResult(tools=tools)

        async def on_call_tool(ctx: Any, params: Any) -> CallToolResult:
            return await run_tool(params.name, params.arguments or {})

        server: Server = _Server(
            name,
            version=version,
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
        )
        return server

    async def list_tools() -> list[Tool]:
        return tools

    async def call_tool(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        result = await run_tool(tool_name, arguments or {})
        # A call the client has cancelled must not produce a result: mcp 1.x
        # answers the cancellation itself and treats a second response as a
        # broken invariant that stops the whole server. A tool that outlived
        # its cancellation ends here instead.
        await anyio.lowlevel.checkpoint_if_cancelled()
        return result

    legacy_server = _Server(name, version=version)
    legacy_server.list_tools()(list_tools)
    legacy_server.call_tool(validate_input=False)(call_tool)
    built: Server = legacy_server
    _cancel_safe.add(built)
    return built
