"""Integration tests for SDK MCP server support.

These drive in-process servers the way the CLI does: raw JSON-RPC messages
routed through ``Query`` (initialize first, then tools/list, tools/call and
so on), asserting on the wire payloads the CLI actually receives. They run
unchanged against every supported ``mcp`` major version.
"""

import base64
import gc
import json
import logging
import threading
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from itertools import count
from typing import Any
from unittest.mock import AsyncMock, Mock

import anyio
import mcp.types
import pytest
import sniffio
from mcp.server import Server

from claude_agent_sdk import (
    ClaudeAgentOptions,
    McpSdkServerConfig,
    SdkMcpTool,
    ToolAnnotations,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk import (
    _python_type_to_json_schema as python_type_to_json_schema,
)
from claude_agent_sdk import (
    _typeddict_to_json_schema as typeddict_to_json_schema,
)
from claude_agent_sdk._internal import sdk_mcp_bridge
from claude_agent_sdk._internal._mcp_compat import MCP_MAJOR
from claude_agent_sdk._internal.query import Query

INITIALIZE_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test-client", "version": "0.0.0"},
}


class SdkMcpClient:
    """Plays the CLI's part: sends JSON-RPC to SDK MCP servers through a Query."""

    def __init__(self, servers: dict[str, McpSdkServerConfig]) -> None:
        transport = AsyncMock()
        transport.is_ready = Mock(return_value=True)
        self.query = Query(
            transport=transport,
            is_streaming_mode=True,
            sdk_mcp_servers={name: cfg["instance"] for name, cfg in servers.items()},
        )
        self._ids = count(0)  # like the CLI, whose initialize is id 0

    async def send(self, server: str, message: dict[str, Any]) -> dict[str, Any] | None:
        return await self.query._handle_sdk_mcp_request(server, message)

    async def request(
        self, server: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        message = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            message["params"] = params
        response = await self.send(server, message)
        assert response is not None
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == message["id"]
        return response

    async def notify(
        self, server: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        return await self.send(server, message)

    async def initialize(self, server: str) -> dict[str, Any]:
        response = await self.request(server, "initialize", INITIALIZE_PARAMS)
        await self.notify(server, "notifications/initialized")
        return response["result"]

    async def list_tools(self, server: str) -> list[dict[str, Any]]:
        response = await self.request(server, "tools/list", {})
        return response["result"]["tools"]

    async def call_tool(
        self, server: str, name: str, arguments: Any = None
    ) -> dict[str, Any]:
        params = {"name": name, "arguments": {} if arguments is None else arguments}
        response = await self.request(server, "tools/call", params)
        assert "error" not in response, response
        return response["result"]

    async def aclose(self) -> None:
        await self.query.close()
        self.query.close_receive_stream()


@asynccontextmanager
async def connected(config: McpSdkServerConfig) -> AsyncIterator[SdkMcpClient]:
    """An SdkMcpClient for one server, registered as ``srv`` and initialized."""
    client = SdkMcpClient({"srv": config})
    try:
        await client.initialize("srv")
        yield client
    finally:
        await client.aclose()


def texts(result: dict[str, Any]) -> list[str]:
    return [block["text"] for block in result["content"] if block["type"] == "text"]


# --- Server construction ------------------------------------------------------


@pytest.mark.anyio
async def test_server_creation():
    """create_sdk_mcp_server returns a real, runnable mcp Server in the config."""
    config = create_sdk_mcp_server(name="test-server", version="2.0.0", tools=[])

    assert config["type"] == "sdk"
    assert config["name"] == "test-server"
    assert isinstance(config["instance"], Server)
    assert config["instance"].name == "test-server"
    assert config["instance"].version == "2.0.0"

    async with connected(config) as client:
        assert await client.list_tools("srv") == []


@pytest.mark.anyio
async def test_tool_creation():
    """Test that tools can be created with proper schemas."""

    @tool("echo", "Echo input", {"input": str})
    async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"output": args["input"]}

    assert echo_tool.name == "echo"
    assert echo_tool.description == "Echo input"
    assert echo_tool.input_schema == {"input": str}
    assert echo_tool.annotations is None
    assert callable(echo_tool.handler)

    result = await echo_tool.handler({"input": "test"})
    assert result == {"output": "test"}


@pytest.mark.anyio
async def test_mixed_servers():
    """Test that SDK and external MCP servers can work together."""

    @tool("sdk_tool", "SDK tool", {})
    async def sdk_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"result": "from SDK"}

    sdk_server = create_sdk_mcp_server(name="sdk-server", tools=[sdk_tool])
    external_server = {"type": "stdio", "command": "echo", "args": ["test"]}

    options = ClaudeAgentOptions(
        mcp_servers={"sdk": sdk_server, "external": external_server}
    )

    assert "sdk" in options.mcp_servers
    assert "external" in options.mcp_servers
    assert options.mcp_servers["sdk"]["type"] == "sdk"
    assert options.mcp_servers["external"]["type"] == "stdio"


# --- Handshake -----------------------------------------------------------------


@pytest.mark.anyio
async def test_initialize_reports_real_server_info_and_capabilities():
    @tool("noop", "Does nothing", {})
    async def noop(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": []}

    config = create_sdk_mcp_server(name="hello", version="3.2.1", tools=[noop])
    client = SdkMcpClient({"hello": config})
    try:
        response = await client.request("hello", "initialize", INITIALIZE_PARAMS)
    finally:
        await client.aclose()

    result = response["result"]
    assert result["serverInfo"]["name"] == "hello"
    assert result["serverInfo"]["version"] == "3.2.1"
    assert result["protocolVersion"] == INITIALIZE_PARAMS["protocolVersion"]
    assert "tools" in result["capabilities"]


@pytest.mark.anyio
async def test_initialized_notification_gets_no_jsonrpc_reply():
    config = create_sdk_mcp_server(name="srv", tools=[])
    client = SdkMcpClient({"srv": config})
    try:
        await client.request("srv", "initialize", INITIALIZE_PARAMS)
        assert await client.notify("srv", "notifications/initialized") is None
        # The session is still healthy afterwards.
        assert await client.list_tools("srv") == []
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_control_request_for_notification_is_still_acknowledged():
    """The CLI wraps every MCP message in a control request and expects a
    well-formed success response even when the message was a notification."""
    config = create_sdk_mcp_server(name="srv", tools=[])
    client = SdkMcpClient({"srv": config})
    written: list[str] = []

    async def capture(data: str) -> None:
        written.append(data)

    client.query.transport.write = capture
    try:
        await client.request("srv", "initialize", INITIALIZE_PARAMS)
        await client.query._handle_control_request(
            {
                "type": "control_request",
                "request_id": "req-1",
                "request": {
                    "subtype": "mcp_message",
                    "server_name": "srv",
                    "message": {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    },
                },
            }
        )
    finally:
        await client.aclose()

    [frame] = [json.loads(line) for line in written]
    assert frame == {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": "req-1",
            "response": {"mcp_response": {"jsonrpc": "2.0", "result": {}}},
        },
    }


@pytest.mark.anyio
async def test_unknown_server_is_a_jsonrpc_error():
    client = SdkMcpClient({})
    try:
        response = await client.request("missing", "tools/list", {})
    finally:
        await client.aclose()
    assert response["error"]["code"] == -32601
    assert "missing" in response["error"]["message"]


@pytest.mark.anyio
async def test_unimplemented_method_is_answered_by_the_server():
    config = create_sdk_mcp_server(name="srv", tools=[])
    async with connected(config) as client:
        response = await client.request("srv", "resources/list", {})
    assert response["error"]["code"] == -32601


@pytest.mark.anyio
async def test_malformed_message_is_a_jsonrpc_error_and_the_session_survives():
    config = create_sdk_mcp_server(name="srv", tools=[])
    async with connected(config) as client:
        response = await client.send("srv", {"jsonrpc": "2.0", "id": 5})
        assert response is not None
        assert response["id"] == 5
        assert response["error"]["code"] == -32603
        assert await client.list_tools("srv") == []


@pytest.mark.anyio
async def test_message_mcp_reads_as_a_notification_gets_no_reply():
    """Whether a reply is due is decided once, by mcp's own parse: a frame
    whose id is not a valid JSON-RPC id is a notification to mcp, so the
    bridge must not sit waiting for a response to it."""
    config = create_sdk_mcp_server(name="srv", tools=[])
    async with connected(config) as client:
        with anyio.fail_after(5):
            reply = await client.send(
                "srv", {"jsonrpc": "2.0", "id": 2.5, "method": "tools/list"}
            )
        assert reply is None
        assert await client.list_tools("srv") == []


@pytest.mark.anyio
async def test_repeated_handshake_is_served_by_the_same_session():
    """The CLI re-initializes live SDK servers in some situations (a sibling
    SDK server that failed, a change to the set of servers). mcp accepts a
    repeated handshake on a live connection, so it must simply flow through:
    same session, same run(), and a call in flight at the time undisturbed."""
    release = anyio.Event()

    @tool("slow", "Finishes when released", {})
    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await release.wait()
        return {"content": [{"type": "text", "text": "slow done"}]}

    @tool("echo", "Echo", {"text": str})
    async def echo(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": args["text"]}]}

    config = create_sdk_mcp_server(name="srv", tools=[slow, echo])
    async with connected(config) as client:
        [bridge] = client.query._sdk_mcp_bridges.values()
        session = bridge._session
        in_flight: dict[str, Any] = {}

        async def call_slow() -> None:
            in_flight["result"] = await client.call_tool("srv", "slow", {})

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(call_slow)
                await anyio.sleep(0.05)
                again = await client.initialize("srv")
                assert again["serverInfo"]["name"] == "srv"
                assert texts(await client.call_tool("srv", "echo", {"text": "hi"})) == [
                    "hi"
                ]
                release.set()

        assert texts(in_flight["result"]) == ["slow done"]
        assert bridge._session is session
        assert [t["name"] for t in await client.list_tools("srv")] == ["slow", "echo"]


@pytest.mark.anyio
async def test_message_before_any_initialize_starts_the_session():
    config = create_sdk_mcp_server(name="srv", tools=[])
    client = SdkMcpClient({"srv": config})
    try:
        with anyio.fail_after(5):
            reply = await client.request("srv", "ping")
        assert reply["result"] == {}
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_messages_after_close_are_refused_and_start_nothing():
    config = create_sdk_mcp_server(name="srv", tools=[])
    client = SdkMcpClient({"srv": config})
    await client.initialize("srv")
    [bridge] = client.query._sdk_mcp_bridges.values()
    await client.aclose()

    reply = await client.send(
        "srv", {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}
    )
    assert reply is not None and reply["id"] == 9
    assert "is closed" in reply["error"]["message"]
    assert bridge._session is None


@pytest.mark.anyio
async def test_close_tears_down_every_bridge_without_leaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Older mcp 1.x releases do not cancel a running tool when the connection
    # closes; ending such a session waits out the grace period.
    monkeypatch.setattr(sdk_mcp_bridge, "SHUTDOWN_GRACE_SECONDS", 0.5)

    @tool("slow", "Sleeps", {})
    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await anyio.sleep(10)
        return {"content": []}

    one = create_sdk_mcp_server(name="one", tools=[slow])
    two = create_sdk_mcp_server(name="two", tools=[])
    client = SdkMcpClient({"one": one, "two": two})
    await client.initialize("one")
    await client.initialize("two")
    sessions = [b._session for b in client.query._sdk_mcp_bridges.values()]
    assert all(s is not None for s in sessions)

    # A tool call still in flight must not keep close() from finishing.
    in_flight = client.query.spawn_task(client.call_tool("one", "slow"))
    await anyio.sleep(0.05)

    tasks_before = _asyncio_task_count()
    gc.collect()  # settle garbage left by other tests before recording warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with anyio.fail_after(5):
            await client.aclose()
        gc.collect()

    assert in_flight.done()
    assert all(s is not None and s._task.done() for s in sessions)
    assert all(b._session is None for b in client.query._sdk_mcp_bridges.values())
    leaked = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
    assert not leaked
    if tasks_before is not None:
        # The two session tasks and the in-flight handler are gone.
        assert _asyncio_task_count() <= tasks_before - 3

    # Closing again is a no-op.
    await client.query.close()


@pytest.mark.anyio
async def test_close_does_not_hang_on_a_tool_blocked_outside_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A tool stuck in a worker thread cannot be cancelled. Closing gives it a
    grace period and then stops waiting, instead of hanging disconnect()."""
    monkeypatch.setattr(sdk_mcp_bridge, "SHUTDOWN_GRACE_SECONDS", 0.2)
    release = threading.Event()
    entered = anyio.Event()

    @tool("blocked", "Blocks in a thread", {})
    async def blocked(args: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        await anyio.to_thread.run_sync(release.wait)
        return {"content": []}

    config = create_sdk_mcp_server(name="srv", tools=[blocked])
    client = SdkMcpClient({"srv": config})
    await client.initialize("srv")
    [bridge] = client.query._sdk_mcp_bridges.values()
    session = bridge._session
    assert session is not None
    in_flight = client.query.spawn_task(client.call_tool("srv", "blocked"))
    await entered.wait()
    try:
        with caplog.at_level(logging.WARNING, logger=sdk_mcp_bridge.__name__):
            with anyio.fail_after(3):
                await client.aclose()
        assert in_flight.done()
        assert bridge._session is None
        assert any("did not stop" in r.message for r in caplog.records)
    finally:
        release.set()
    # Once the thread lets go, the abandoned session finishes on its own.
    with anyio.fail_after(3):
        await session._task.wait()


@pytest.mark.anyio
async def test_close_with_a_cancellable_tool_in_flight_is_prompt_and_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Older mcp 1.x releases do not cancel a running handler when the
    connection closes, so closing must cancel in-flight calls through mcp
    first: no grace-period wait, no warning, and no handler answering into a
    stream that is already closed."""
    events: list[str] = []
    started = anyio.Event()

    @tool("sleepy", "Sleeps", {})
    async def sleepy(args: dict[str, Any]) -> dict[str, Any]:
        events.append("start")
        started.set()
        try:
            await anyio.sleep(30)
        except BaseException as e:
            events.append(f"cancelled:{type(e).__name__}")
            raise
        events.append("finished")
        return {"content": []}

    config = create_sdk_mcp_server(name="srv", tools=[sleepy])
    client = SdkMcpClient({"srv": config})
    await client.initialize("srv")
    in_flight = client.query.spawn_task(client.call_tool("srv", "sleepy"))
    await started.wait()
    with caplog.at_level(logging.WARNING):
        t0 = anyio.current_time()
        await client.aclose()
        elapsed = anyio.current_time() - t0
    assert elapsed < sdk_mcp_bridge.SHUTDOWN_GRACE_SECONDS / 5
    assert in_flight.done()
    assert (
        events[0] == "start"
        and events[1].startswith("cancelled")
        and "finished" not in events
    )
    noisy = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and "sdk_mcp" in r.name.lower() + r.message.lower()
    ]
    assert not noisy, [r.message for r in noisy]


def _asyncio_task_count() -> int | None:
    if sniffio.current_async_library() != "asyncio":
        return None
    import asyncio

    return len(asyncio.all_tasks())


# --- tools/list wire format -----------------------------------------------------


@pytest.mark.anyio
async def test_tools_list_wire_format():
    @tool(
        "read_data",
        "Read data from source",
        {"source": str},
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def read_data(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"Data from {args['source']}"}]}

    @tool(
        "delete_item",
        "Delete an item",
        {"id": str},
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True),
    )
    async def delete_item(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"Deleted {args['id']}"}]}

    @tool("plain", "Tool without annotations", {"x": str, "n": int})
    async def plain(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": args["x"]}]}

    # Annotations are kept on the SdkMcpTool as given.
    assert read_data.annotations is not None
    assert read_data.annotations.model_dump(by_alias=True, exclude_none=True) == {
        "readOnlyHint": True,
        "openWorldHint": False,
    }
    assert plain.annotations is None

    config = create_sdk_mcp_server(name="srv", tools=[read_data, delete_item, plain])
    async with connected(config) as client:
        tools = {t["name"]: t for t in await client.list_tools("srv")}

    assert set(tools) == {"read_data", "delete_item", "plain"}
    assert tools["plain"] == {
        "name": "plain",
        "description": "Tool without annotations",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["x", "n"],
        },
    }
    assert tools["read_data"]["annotations"] == {
        "readOnlyHint": True,
        "openWorldHint": False,
    }
    assert tools["delete_item"]["annotations"] == {
        "destructiveHint": True,
        "idempotentHint": True,
    }
    assert "_meta" not in tools["plain"]
    assert "_meta" not in tools["read_data"]


@pytest.mark.anyio
async def test_plain_mcp_tool_annotations_are_accepted():
    @tool(
        "search",
        "Search the web",
        {"query": str},
        annotations=mcp.types.ToolAnnotations.model_validate({"openWorldHint": True}),
    )
    async def search(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": []}

    config = create_sdk_mcp_server(name="srv", tools=[search])
    async with connected(config) as client:
        [listed] = await client.list_tools("srv")
    assert listed["annotations"] == {"openWorldHint": True}


@pytest.mark.anyio
async def test_max_result_size_chars_annotation_flows_to_cli():
    """``ToolAnnotations(maxResultSizeChars=N)`` reaches the CLI as
    ``_meta["anthropic/maxResultSizeChars"]``, which it reads to keep a large
    result inline instead of persisting it. It is not an MCP field, so it has
    to survive on the annotations object as an extra: the SDK's ToolAnnotations
    keeps it on every mcp version (mcp 2.x's own class would drop it)."""

    @tool(
        "get_large_schema",
        "Returns a large DB schema.",
        {},
        annotations=ToolAnnotations(readOnlyHint=True, maxResultSizeChars=500_000),
    )
    async def get_large_schema(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "schema"}]}

    @tool("small_tool", "Returns a small result.", {})
    async def small_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}

    hand_built = SdkMcpTool(
        name="hand_built",
        description="Constructed without the decorator",
        input_schema={},
        handler=small_tool.handler,
        annotations=ToolAnnotations(maxResultSizeChars=333),
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the documented usage stays warning-free
        config = create_sdk_mcp_server(
            name="srv", tools=[get_large_schema, small_tool, hand_built]
        )

    async with connected(config) as client:
        tools = {t["name"]: t for t in await client.list_tools("srv")}

    assert tools["get_large_schema"]["_meta"] == {
        "anthropic/maxResultSizeChars": 500_000
    }
    assert tools["get_large_schema"]["annotations"]["readOnlyHint"] is True
    assert tools["hand_built"]["_meta"] == {"anthropic/maxResultSizeChars": 333}
    assert "anthropic/maxResultSizeChars" not in tools["small_tool"].get("_meta", {})


@pytest.mark.anyio
async def test_max_result_size_chars_declared_as_a_field_on_a_subclass_flows_too():
    class Declared(mcp.types.ToolAnnotations):
        maxResultSizeChars: int | None = None  # noqa: N815 - the wire spelling

    @tool(
        "declared",
        "Uses a typed subclass",
        {},
        annotations=Declared(maxResultSizeChars=444),
    )
    async def declared(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": []}

    config = create_sdk_mcp_server(name="srv", tools=[declared])
    async with connected(config) as client:
        [listed] = await client.list_tools("srv")

    assert listed["_meta"] == {"anthropic/maxResultSizeChars": 444}


def test_tool_annotations_is_an_mcp_tool_annotations():
    annotations = ToolAnnotations(readOnlyHint=True, maxResultSizeChars=99)
    assert isinstance(annotations, mcp.types.ToolAnnotations)
    assert annotations.model_dump(by_alias=True, exclude_none=True) == {
        "readOnlyHint": True,
        "maxResultSizeChars": 99,
    }
    validated = mcp.types.Tool.model_validate(
        {"name": "t", "inputSchema": {"type": "object"}, "annotations": annotations}
    )
    assert validated.annotations is annotations


# --- tools/call ---------------------------------------------------------------


PNG_DATA = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13"
    b"\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\x0cIDATx\x9cc```"
    b"\x00\x00\x00\x04\x00\x01]U!\x1c\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("utf-8")


@pytest.mark.anyio
async def test_tool_call_text_results():
    tool_executions: list[dict[str, Any]] = []

    @tool("greet_user", "Greets a user by name", {"name": str})
    async def greet_user(args: dict[str, Any]) -> dict[str, Any]:
        tool_executions.append({"name": "greet_user", "args": args})
        return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}

    @tool("add_numbers", "Adds two numbers", {"a": float, "b": float})
    async def add_numbers(args: dict[str, Any]) -> dict[str, Any]:
        tool_executions.append({"name": "add_numbers", "args": args})
        return {
            "content": [{"type": "text", "text": f"The sum is {args['a'] + args['b']}"}]
        }

    config = create_sdk_mcp_server(
        name="test-sdk-server", version="1.0.0", tools=[greet_user, add_numbers]
    )
    async with connected(config) as client:
        assert {t["name"] for t in await client.list_tools("srv")} == {
            "greet_user",
            "add_numbers",
        }

        greeting = await client.call_tool("srv", "greet_user", {"name": "Alice"})
        assert greeting["content"] == [{"type": "text", "text": "Hello, Alice!"}]
        assert greeting["isError"] is False

        total = await client.call_tool("srv", "add_numbers", {"a": 5, "b": 3})
        assert texts(total) == ["The sum is 8"]

    assert tool_executions == [
        {"name": "greet_user", "args": {"name": "Alice"}},
        {"name": "add_numbers", "args": {"a": 5, "b": 3}},
    ]


@pytest.mark.anyio
async def test_tool_call_image_content():
    @tool("generate_chart", "Generates a chart", {"title": str})
    async def generate_chart(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": f"Generated chart: {args['title']}"},
                {"type": "image", "data": PNG_DATA, "mimeType": "image/png"},
            ]
        }

    config = create_sdk_mcp_server(name="srv", tools=[generate_chart])
    async with connected(config) as client:
        result = await client.call_tool("srv", "generate_chart", {"title": "Sales"})

    assert result["content"] == [
        {"type": "text", "text": "Generated chart: Sales"},
        {"type": "image", "data": PNG_DATA, "mimeType": "image/png"},
    ]


@pytest.mark.anyio
async def test_is_error_flag_propagated():
    @tool("divide", "Divide two numbers", {"a": float, "b": float})
    async def divide(args: dict[str, Any]) -> dict[str, Any]:
        if args["b"] == 0:
            return {
                "content": [{"type": "text", "text": "Division by zero"}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": str(args["a"] / args["b"])}]}

    config = create_sdk_mcp_server(name="srv", tools=[divide])
    async with connected(config) as client:
        failure = await client.call_tool("srv", "divide", {"a": 1, "b": 0})
        success = await client.call_tool("srv", "divide", {"a": 6, "b": 3})

    assert failure["isError"] is True
    assert "is_error" not in failure
    assert texts(failure) == ["Division by zero"]
    assert success["isError"] is False
    assert texts(success) == ["2.0"]


@pytest.mark.anyio
async def test_unknown_tool_is_an_error_result():
    @tool("known", "Known tool", {})
    async def known(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": []}

    config = create_sdk_mcp_server(name="srv", tools=[known])
    async with connected(config) as client:
        result = await client.call_tool("srv", "mystery", {})

    assert result["isError"] is True
    assert texts(result) == ["Tool 'mystery' not found"]


@pytest.mark.anyio
async def test_handler_exception_is_an_error_result():
    @tool("fail", "Always fails", {})
    async def fail_tool(args: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("Expected error")

    with pytest.raises(ValueError, match="Expected error"):
        await fail_tool.handler({})

    config = create_sdk_mcp_server(name="srv", tools=[fail_tool])
    async with connected(config) as client:
        result = await client.call_tool("srv", "fail", {})

    assert result["isError"] is True
    assert texts(result) == ["Expected error"]


@pytest.mark.anyio
async def test_malformed_handler_payload_is_an_error_result():
    @tool("sloppy", "Forgets the text key", {})
    async def sloppy(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text"}]}

    config = create_sdk_mcp_server(name="srv", tools=[sloppy])
    async with connected(config) as client:
        result = await client.call_tool("srv", "sloppy", {})

    assert result["isError"] is True
    assert texts(result) == ["'text'"]


@pytest.mark.anyio
async def test_invalid_arguments_are_rejected_before_the_handler_runs():
    calls: list[dict[str, Any]] = []

    @tool("add", "Add two numbers", {"a": float, "b": float})
    async def add(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}

    config = create_sdk_mcp_server(name="srv", tools=[add])
    async with connected(config) as client:
        missing = await client.call_tool("srv", "add", {"a": 1})
        wrong_type = await client.call_tool("srv", "add", {"a": 1, "b": "two"})
        fine = await client.call_tool("srv", "add", {"a": 1, "b": 2})

    assert missing["isError"] is True
    assert texts(missing) == ["Input validation error: 'b' is a required property"]
    assert wrong_type["isError"] is True
    [message] = texts(wrong_type)
    assert message.startswith("Input validation error: ")
    assert "'two'" in message
    assert texts(fine) == ["3"]
    assert calls == [{"a": 1, "b": 2}]


@pytest.mark.anyio
async def test_json_schema_constraints_are_enforced():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 2}},
        "required": ["name"],
    }

    @tool("validate", "Validate input", schema)
    async def validate(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "OK"}]}

    config = create_sdk_mcp_server(name="srv", tools=[validate])
    async with connected(config) as client:
        too_short = await client.call_tool("srv", "validate", {"name": "x"})
        ok = await client.call_tool("srv", "validate", {"name": "xy"})

    assert too_short["isError"] is True
    assert texts(too_short)[0].startswith("Input validation error: ")
    assert texts(ok) == ["OK"]


@pytest.mark.anyio
async def test_invalid_tool_schema_is_an_error_result():
    """A schema jsonschema cannot use fails the call the same way on every mcp
    version: as an error result, not a protocol error."""

    async def handler(args: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "unreachable"}]}

    broken = SdkMcpTool(
        name="broken",
        description="Has an invalid schema",
        input_schema={"type": "object", "properties": {"x": {"type": "bogus"}}},
        handler=handler,
    )
    config = create_sdk_mcp_server(name="srv", tools=[broken])
    async with connected(config) as client:
        result = await client.call_tool("srv", "broken", {"x": 1})
    assert result["isError"] is True
    assert "bogus" in texts(result)[0]


@pytest.mark.anyio
async def test_cancelled_tool_call_is_ended():
    """When the CLI cancels a call (notifications/cancelled), the running tool
    is cancelled and the request still gets a terminal answer, so nothing is
    left waiting on either side."""
    started = anyio.Event()
    outcome: list[str] = []

    @tool("slow", "Sleeps", {})
    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        started.set()
        try:
            await anyio.sleep(30)
        except BaseException:
            outcome.append("cancelled")
            raise
        outcome.append("finished")
        return {"content": []}

    config = create_sdk_mcp_server(name="srv", tools=[slow])
    call = {
        "jsonrpc": "2.0",
        "id": 77,
        "method": "tools/call",
        "params": {"name": "slow", "arguments": {}},
    }
    async with connected(config) as client:
        response: dict[str, Any] = {}

        async def send() -> None:
            response.update(await client.send("srv", call) or {})

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(send)
                await started.wait()
                await client.notify(
                    "srv",
                    "notifications/cancelled",
                    {"requestId": 77, "reason": "user interrupted"},
                )

        assert outcome == ["cancelled"]
        assert response["id"] == 77
        assert "cancelled" in response["error"]["message"].lower()
        # The session carries on.
        assert [t["name"] for t in await client.list_tools("srv")] == ["slow"]


@pytest.mark.anyio
async def test_reusing_an_in_flight_request_id_is_refused_without_reaching_the_server():
    release = anyio.Event()
    calls = 0

    @tool("wait", "Waits", {})
    async def wait(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        await release.wait()
        return {"content": [{"type": "text", "text": "done"}]}

    config = create_sdk_mcp_server(name="srv", tools=[wait])
    call = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "wait", "arguments": {}},
    }
    async with connected(config) as client:
        replies: dict[str, dict[str, Any] | None] = {}

        async def send(label: str) -> None:
            replies[label] = await client.send("srv", call)

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(send, "first")
                await anyio.sleep(0.05)
                tg.start_soon(send, "second")
                await anyio.sleep(0.05)
                assert "second" in replies and "first" not in replies
                release.set()

    first, second = replies["first"], replies["second"]
    assert second is not None and second["id"] == 7
    assert "already in flight" in second["error"]["message"]
    assert first is not None and texts(first["result"]) == ["done"]
    assert calls == 1


@pytest.mark.anyio
async def test_concurrent_tool_calls_on_one_server_both_resolve():
    """Two calls in flight at once must both complete.

    Each handler waits until the other has started, so this can only pass if
    the bridge really runs them concurrently over the one session.
    """
    arrived: list[str] = []
    both_arrived = anyio.Event()

    @tool("rendezvous", "Waits for its sibling call", {"tag": str})
    async def rendezvous(args: dict[str, Any]) -> dict[str, Any]:
        arrived.append(args["tag"])
        if len(arrived) == 2:
            both_arrived.set()
        await both_arrived.wait()
        return {"content": [{"type": "text", "text": args["tag"]}]}

    config = create_sdk_mcp_server(name="srv", tools=[rendezvous])
    results: dict[str, dict[str, Any]] = {}

    async with connected(config) as client:

        async def call(tag: str) -> None:
            results[tag] = await client.call_tool("srv", "rendezvous", {"tag": tag})

        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(call, "a")
                tg.start_soon(call, "b")

    assert texts(results["a"]) == ["a"]
    assert texts(results["b"]) == ["b"]


# --- Content conversion --------------------------------------------------------


@pytest.mark.anyio
async def test_resource_link_content_converted_to_text():
    @tool("get_resource", "Returns a resource link", {"url": str})
    async def get_resource(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "resource_link",
                    "name": "My Document",
                    "uri": args["url"],
                    "description": "A test document",
                }
            ]
        }

    config = create_sdk_mcp_server(name="srv", tools=[get_resource])
    async with connected(config) as client:
        result = await client.call_tool(
            "srv", "get_resource", {"url": "https://example.com/doc.pdf"}
        )

    assert result["content"] == [
        {
            "type": "text",
            "text": "My Document\nhttps://example.com/doc.pdf\nA test document",
        }
    ]


@pytest.mark.anyio
async def test_embedded_resource_text_content_converted():
    @tool("get_embedded", "Returns an embedded resource", {})
    async def get_embedded(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///test.txt",
                        "text": "File contents here",
                        "mimeType": "text/plain",
                    },
                }
            ]
        }

    config = create_sdk_mcp_server(name="srv", tools=[get_embedded])
    async with connected(config) as client:
        result = await client.call_tool("srv", "get_embedded")

    assert result["content"] == [{"type": "text", "text": "File contents here"}]


@pytest.mark.anyio
async def test_binary_embedded_resource_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
):
    @tool("get_binary", "Returns a binary embedded resource", {})
    async def get_binary(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///image.png",
                        "blob": "iVBORw0KGgo=",
                        "mimeType": "image/png",
                    },
                }
            ]
        }

    config = create_sdk_mcp_server(name="srv", tools=[get_binary])
    async with connected(config) as client:
        with caplog.at_level(logging.WARNING):
            result = await client.call_tool("srv", "get_binary")

    assert result["content"] == []
    assert "Binary embedded resource" in caplog.text


@pytest.mark.anyio
async def test_unknown_content_type_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
):
    @tool("get_unknown", "Returns unknown content type", {})
    async def get_unknown(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "custom_widget", "data": "some data"}]}

    config = create_sdk_mcp_server(name="srv", tools=[get_unknown])
    async with connected(config) as client:
        with caplog.at_level(logging.WARNING):
            result = await client.call_tool("srv", "get_unknown")

    assert result["content"] == []
    assert "Unsupported content type" in caplog.text
    assert "custom_widget" in caplog.text


@pytest.mark.anyio
async def test_mixed_content_types_with_resource_link():
    @tool("get_mixed", "Returns mixed content", {})
    async def get_mixed(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": "Here is the document:"},
                {"type": "image", "data": PNG_DATA, "mimeType": "image/png"},
                {
                    "type": "resource_link",
                    "name": "Report",
                    "uri": "https://example.com/report",
                },
            ]
        }

    config = create_sdk_mcp_server(name="srv", tools=[get_mixed])
    async with connected(config) as client:
        result = await client.call_tool("srv", "get_mixed")

    assert result["content"] == [
        {"type": "text", "text": "Here is the document:"},
        {"type": "image", "data": PNG_DATA, "mimeType": "image/png"},
        {"type": "text", "text": "Report\nhttps://example.com/report"},
    ]


# --- Hand-built servers ---------------------------------------------------------


AUDIO_RESULT = {
    "content": [
        {"type": "text", "text": "verbatim"},
        {"type": "audio", "data": "UklGRg==", "mimeType": "audio/wav"},
    ],
    "structuredContent": {"tool": "raw", "arguments": {"n": 7}},
    "isError": False,
}


def _hand_built_server() -> Server:
    """A lowlevel mcp Server written directly against the installed mcp API,
    the way applications that do not use create_sdk_mcp_server() build one."""
    tools = [
        mcp.types.Tool.model_validate(
            {
                "name": "raw",
                "description": "Returns content the SDK factory never produces",
                "inputSchema": {
                    "type": "object",
                    "properties": {"n": {"type": "integer"}},
                },
            }
        )
    ]
    resources = [
        mcp.types.Resource.model_validate({"uri": "memo://readme", "name": "readme"})
    ]

    def call_result(arguments: dict[str, Any]) -> mcp.types.CallToolResult:
        payload: dict[str, Any] = {**AUDIO_RESULT}
        payload["structuredContent"] = {"tool": "raw", "arguments": arguments}
        return mcp.types.CallToolResult.model_validate(payload)

    if MCP_MAJOR >= 2:

        async def on_list_tools(ctx: Any, params: Any) -> mcp.types.ListToolsResult:
            return mcp.types.ListToolsResult(tools=tools)

        async def on_call_tool(ctx: Any, params: Any) -> mcp.types.CallToolResult:
            return call_result(params.arguments or {})

        async def on_list_resources(
            ctx: Any, params: Any
        ) -> mcp.types.ListResourcesResult:
            return mcp.types.ListResourcesResult(resources=resources)

        return Server(
            "hand-built",
            version="0.1.0",
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
            on_list_resources=on_list_resources,
        )

    server = Server("hand-built", version="0.1.0")

    @server.list_tools()
    async def list_tools() -> list[mcp.types.Tool]:
        return tools

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> mcp.types.CallToolResult:
        return call_result(arguments)

    @server.list_resources()
    async def list_resources() -> list[mcp.types.Resource]:
        return resources

    return server


@pytest.mark.anyio
async def test_hand_built_server_is_served_verbatim():
    config = McpSdkServerConfig(
        type="sdk", name="hand-built", instance=_hand_built_server()
    )

    async with connected(config) as client:
        info = await client.initialize("srv")
        listed = await client.list_tools("srv")
        result = await client.call_tool("srv", "raw", {"n": 7})
        resources = await client.request("srv", "resources/list", {})

    assert info["serverInfo"] == {"name": "hand-built", "version": "0.1.0"}
    assert {"tools", "resources"} <= set(info["capabilities"])
    assert [t["name"] for t in listed] == ["raw"]
    assert result["content"] == AUDIO_RESULT["content"]
    assert result["structuredContent"] == {"tool": "raw", "arguments": {"n": 7}}
    assert result["isError"] is False
    assert [r["uri"] for r in resources["result"]["resources"]] == ["memo://readme"]


def _chatty_server() -> Server:
    """A hand-built server whose tool notifies the client before answering."""
    tools = [
        mcp.types.Tool.model_validate(
            {"name": "chat", "inputSchema": {"type": "object", "properties": {}}}
        )
    ]
    done = mcp.types.CallToolResult.model_validate(
        {"content": [{"type": "text", "text": "done"}]}
    )

    if MCP_MAJOR >= 2:

        async def on_list_tools(ctx: Any, params: Any) -> mcp.types.ListToolsResult:
            return mcp.types.ListToolsResult(tools=tools)

        async def on_call_tool(ctx: Any, params: Any) -> mcp.types.CallToolResult:
            await ctx.session.send_tool_list_changed()
            return done

        return Server(
            "chatty",
            version="0.1.0",
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
        )

    server = Server("chatty", version="0.1.0")

    @server.list_tools()
    async def list_tools() -> list[mcp.types.Tool]:
        return tools

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> mcp.types.CallToolResult:
        await server.request_context.session.send_tool_list_changed()
        return done

    return server


@pytest.mark.anyio
async def test_server_initiated_notifications_are_dropped_and_the_call_completes():
    """Nothing carries server-to-client traffic to the CLI yet; it must not
    get in the way of the response the CLI is waiting for."""
    config = McpSdkServerConfig(type="sdk", name="chatty", instance=_chatty_server())

    async with connected(config) as client:
        with anyio.fail_after(5):
            result = await client.call_tool("srv", "chat", {})
            again = await client.call_tool("srv", "chat", {})

    assert texts(result) == ["done"]
    assert texts(again) == ["done"]


class _CrashingServer(Server):  # type: ignore[type-arg]
    """A server whose run() fails as soon as it reads its first message."""

    runs = 0

    async def run(self, read_stream: Any, *args: Any, **kwargs: Any) -> None:
        type(self).runs += 1
        async for _ in read_stream:
            raise RuntimeError("cannot serve today")


@pytest.mark.anyio
async def test_server_that_fails_stays_stopped_until_the_cli_initializes_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A server whose run() dies is not restarted behind the CLI's back: every
    message gets a prompt error naming the server and the cause. A new
    initialize (the CLI retries a server it considers failed) starts it
    again."""
    _CrashingServer.runs = 0
    config = McpSdkServerConfig(
        type="sdk", name="crashing", instance=_CrashingServer("crashing")
    )
    initialize = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": INITIALIZE_PARAMS,
    }
    client = SdkMcpClient({"srv": config})
    try:
        with caplog.at_level(logging.ERROR, logger=sdk_mcp_bridge.__name__):
            with anyio.fail_after(5):
                first = await client.send("srv", initialize)
                while not any("'srv' failed" in r.message for r in caplog.records):
                    await anyio.sleep(0.01)
                later = [
                    await client.send(
                        "srv", {"jsonrpc": "2.0", "id": i, "method": "ping"}
                    )
                    for i in (1, 2)
                ]
                assert _CrashingServer.runs == 1
                retried = await client.send("srv", initialize)
    finally:
        await client.aclose()

    assert first is not None and first["error"]["code"] == -32603
    for i, reply in zip((1, 2), later, strict=True):
        assert reply is not None and reply["id"] == i
        assert reply["error"]["code"] == -32603
        assert "'srv' stopped: cannot serve today" in reply["error"]["message"]
    assert retried is not None and retried["error"]["code"] == -32603
    assert _CrashingServer.runs == 2


@pytest.mark.anyio
async def test_late_response_for_a_caller_that_gave_up_is_dropped():
    """The CLI can stop waiting for a call (its own timeout); when the tool
    finishes anyway, the orphaned response is discarded and the session
    keeps serving."""
    release = anyio.Event()
    finished = anyio.Event()

    @tool("slow", "Finishes when released", {})
    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await release.wait()
        finished.set()
        return {"content": [{"type": "text", "text": "late"}]}

    @tool("fast", "Answers at once", {})
    async def fast(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "fast"}]}

    config = create_sdk_mcp_server(name="srv", tools=[slow, fast])
    async with connected(config) as client:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(client.call_tool, "srv", "slow", {})
                await anyio.sleep(0.05)
                tg.cancel_scope.cancel()
            release.set()
            await finished.wait()
            await anyio.sleep(0.05)
            result = await client.call_tool("srv", "fast", {})

    assert texts(result) == ["fast"]


@pytest.mark.anyio
async def test_input_schema_that_is_a_plain_class_lists_as_an_empty_object():
    class Opaque:
        pass

    @tool("opaque", "Takes anything", Opaque)
    async def opaque(args: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}

    config = create_sdk_mcp_server(name="srv", tools=[opaque])
    async with connected(config) as client:
        listed = await client.list_tools("srv")
        result = await client.call_tool("srv", "opaque", {"whatever": 1})

    assert listed[0]["inputSchema"] == {"type": "object", "properties": {}}
    assert texts(result) == ["ok"]


@pytest.mark.anyio
async def test_tool_that_outlives_its_cancellation_does_not_break_the_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tool can swallow the cancellation the CLI asked for and return a
    result anyway. That must not take the server down (mcp 1.x stops the
    whole server if a cancelled call is answered a second time)."""
    release = anyio.Event()

    @tool("stubborn", "Ignores cancellation", {})
    async def stubborn(args: dict[str, Any]) -> dict[str, Any]:
        with suppress(BaseException):
            await release.wait()
        return {"content": [{"type": "text", "text": "finished anyway"}]}

    @tool("echo", "Echo", {"text": str})
    async def echo(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": args["text"]}]}

    config = create_sdk_mcp_server(name="srv", tools=[stubborn, echo])
    call = {
        "jsonrpc": "2.0",
        "id": 41,
        "method": "tools/call",
        "params": {"name": "stubborn", "arguments": {}},
    }
    async with connected(config) as client:
        replies: list[dict[str, Any] | None] = []

        async def send() -> None:
            replies.append(await client.send("srv", call))

        with caplog.at_level(logging.WARNING, logger=sdk_mcp_bridge.__name__):
            with anyio.fail_after(5):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(send)
                    await anyio.sleep(0.05)
                    await client.notify(
                        "srv",
                        "notifications/cancelled",
                        {"requestId": 41, "reason": "t"},
                    )
                await anyio.sleep(0.05)
                result = await client.call_tool("srv", "echo", {"text": "still here"})

    assert replies[0] is not None and "error" in replies[0]
    assert texts(result) == ["still here"]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _threaded_server(events: list[str], release: threading.Event) -> Server:
    """A hand-built server whose tool waits on a worker thread, the idiomatic
    way to call blocking code, which is also a handler that returns after a
    cancellation instead of raising."""
    tools = [
        mcp.types.Tool.model_validate(
            {"name": name, "inputSchema": {"type": "object", "properties": {}}}
        )
        for name in ("blocking", "echo")
    ]

    async def work(name: str) -> mcp.types.CallToolResult:
        if name == "blocking":
            events.append("start")
            await anyio.to_thread.run_sync(release.wait)
            events.append("finish")
        return mcp.types.CallToolResult.model_validate(
            {"content": [{"type": "text", "text": name}]}
        )

    if MCP_MAJOR >= 2:

        async def on_list_tools(ctx: Any, params: Any) -> mcp.types.ListToolsResult:
            return mcp.types.ListToolsResult(tools=tools)

        async def on_call_tool(ctx: Any, params: Any) -> mcp.types.CallToolResult:
            return await work(params.name)

        return Server(
            "threaded",
            version="0.1.0",
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
        )

    server = Server("threaded", version="0.1.0")

    @server.list_tools()
    async def list_tools() -> list[mcp.types.Tool]:
        return tools

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> mcp.types.CallToolResult:
        return await work(name)

    return server


@pytest.mark.anyio
async def test_cancelling_a_hand_built_servers_call_never_stops_the_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mcp 1.x stops the whole server when a handler answers a call the client
    already cancelled, and a hand-built server's handlers cannot be made to
    do otherwise, so on 1.x the cancellation is not passed on and the tool
    runs to completion as it always has. On 2.x it is passed on."""
    events: list[str] = []
    release = threading.Event()
    config = McpSdkServerConfig(
        type="sdk", name="threaded", instance=_threaded_server(events, release)
    )
    call = {
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {"name": "blocking", "arguments": {}},
    }
    async with connected(config) as client:
        replies: list[dict[str, Any] | None] = []

        async def send() -> None:
            replies.append(await client.send("srv", call))

        with caplog.at_level(logging.WARNING, logger=sdk_mcp_bridge.__name__):
            with anyio.fail_after(10):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(send)
                    while "start" not in events:
                        await anyio.sleep(0.01)
                    await client.notify(
                        "srv",
                        "notifications/cancelled",
                        {"requestId": 21, "reason": "t"},
                    )
                    await anyio.sleep(0.05)
                    release.set()
                while "finish" not in events:
                    await anyio.sleep(0.01)
                await anyio.sleep(0.05)
                result = await client.call_tool("srv", "echo", {})

    assert texts(result) == ["echo"]  # the server survived
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    [reply] = replies
    assert reply is not None
    if MCP_MAJOR >= 2:
        assert reply["error"]["message"] == "Request cancelled"
    else:
        assert texts(reply["result"]) == ["blocking"]  # ran to completion, as before


def _asking_server() -> Server:
    """A hand-built server whose tools send the client a request: ``ping``
    pings it, ``ask`` elicits an answer from the user."""
    tools = [
        mcp.types.Tool.model_validate(
            {"name": name, "inputSchema": {"type": "object", "properties": {}}}
        )
        for name in ("ping", "ask")
    ]

    def outcome(error: BaseException | None) -> mcp.types.CallToolResult:
        text = "answered" if error is None else f"refused: {error}"
        return mcp.types.CallToolResult.model_validate(
            {"content": [{"type": "text", "text": text}]}
        )

    async def ask(session: Any, name: str) -> mcp.types.CallToolResult:
        try:
            if name == "ping":
                await session.send_ping()
            else:
                # positional: the schema parameter is spelled differently per major
                await session.elicit("Proceed?", {"type": "object", "properties": {}})
        except Exception as e:
            return outcome(e)
        return outcome(None)

    if MCP_MAJOR >= 2:

        async def on_list_tools(ctx: Any, params: Any) -> mcp.types.ListToolsResult:
            return mcp.types.ListToolsResult(tools=tools)

        async def on_call_tool(ctx: Any, params: Any) -> mcp.types.CallToolResult:
            return await ask(ctx.session, params.name)

        return Server(
            "asking",
            version="0.1.0",
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
        )

    server = Server("asking", version="0.1.0")

    @server.list_tools()
    async def list_tools() -> list[mcp.types.Tool]:
        return tools

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> mcp.types.CallToolResult:
        return await ask(server.request_context.session, name)

    return server


@pytest.mark.anyio
async def test_request_from_server_to_client_is_refused_at_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing carries server-to-client requests to the CLI yet. The server
    must hear "no" immediately rather than wait forever for an answer."""
    config = McpSdkServerConfig(type="sdk", name="asking", instance=_asking_server())

    async with connected(config) as client:
        with caplog.at_level(logging.WARNING, logger=sdk_mcp_bridge.__name__):
            with anyio.fail_after(5):
                result = await client.call_tool("srv", "ask", {})

    [text] = texts(result)
    assert text.startswith("refused:") and "not supported" in text
    assert any("'elicitation/create' request" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_ping_from_server_to_client_is_answered():
    config = McpSdkServerConfig(type="sdk", name="asking", instance=_asking_server())

    async with connected(config) as client:
        with anyio.fail_after(5):
            result = await client.call_tool("srv", "ping", {})

    assert texts(result) == ["answered"]


def _server_with_lifespan(events: list[str]) -> Server:
    @asynccontextmanager
    async def lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:
        events.append("setup")
        try:
            yield {}
        finally:
            events.append("teardown:start")
            await anyio.sleep(0.01)  # real teardowns await: pools, clients
            events.append("teardown:done")

    if MCP_MAJOR >= 2:
        return Server("lived", version="0.1.0", lifespan=lifespan)
    return Server("lived", version="0.1.0", lifespan=lifespan)


@pytest.mark.anyio
async def test_lifespan_runs_once_per_query_and_its_teardown_completes():
    events: list[str] = []
    config = McpSdkServerConfig(
        type="sdk", name="lived", instance=_server_with_lifespan(events)
    )

    async with connected(config) as client:
        await client.request("srv", "ping")
        await client.initialize("srv")  # a repeated handshake changes nothing
        await client.request("srv", "ping")
        assert events == ["setup"]

    with anyio.fail_after(5):
        while "teardown:done" not in events:
            await anyio.sleep(0.01)
    assert events == ["setup", "teardown:start", "teardown:done"]


@pytest.mark.anyio
async def test_session_cancelled_before_it_starts_still_closes():
    server = create_sdk_mcp_server(name="srv", tools=[])["instance"]
    session = sdk_mcp_bridge._Session("srv", server)
    session._task.cancel()  # before it has run a single step

    with anyio.fail_after(2):
        await session.aclose()
    assert session.finished


# --- Tests for _python_type_to_json_schema and TypedDict schema conversion ---


class TestPythonTypeToJsonSchema:
    """Tests for the _python_type_to_json_schema helper."""

    def test_basic_str(self) -> None:
        assert python_type_to_json_schema(str) == {"type": "string"}

    def test_basic_int(self) -> None:
        assert python_type_to_json_schema(int) == {"type": "integer"}

    def test_basic_float(self) -> None:
        assert python_type_to_json_schema(float) == {"type": "number"}

    def test_basic_bool(self) -> None:
        assert python_type_to_json_schema(bool) == {"type": "boolean"}

    def test_bare_list(self) -> None:
        assert python_type_to_json_schema(list) == {"type": "array"}

    def test_bare_dict(self) -> None:
        assert python_type_to_json_schema(dict) == {"type": "object"}

    def test_parameterized_list(self) -> None:
        assert python_type_to_json_schema(list[str]) == {
            "type": "array",
            "items": {"type": "string"},
        }

    def test_parameterized_list_int(self) -> None:
        assert python_type_to_json_schema(list[int]) == {
            "type": "array",
            "items": {"type": "integer"},
        }

    def test_parameterized_dict(self) -> None:
        assert python_type_to_json_schema(dict[str, int]) == {"type": "object"}

    def test_optional_str(self) -> None:
        result = python_type_to_json_schema(str | None)
        assert result == {"type": "string"}

    def test_optional_int_union_syntax(self) -> None:
        result = python_type_to_json_schema(int | None)
        assert result == {"type": "integer"}

    def test_multi_type_union(self) -> None:
        result = python_type_to_json_schema(str | int)
        assert result == {
            "anyOf": [{"type": "string"}, {"type": "integer"}],
        }

    def test_multi_type_union_with_none(self) -> None:
        result = python_type_to_json_schema(str | int | None)
        assert result == {
            "anyOf": [{"type": "string"}, {"type": "integer"}],
        }

    def test_unknown_type_defaults_to_string(self) -> None:
        class Custom:
            pass

        assert python_type_to_json_schema(Custom) == {"type": "string"}

    def test_nested_typeddict(self) -> None:
        from typing import TypedDict

        class Address(TypedDict):
            street: str
            city: str

        result = python_type_to_json_schema(Address)
        assert result["type"] == "object"
        assert result["properties"]["street"] == {"type": "string"}
        assert result["properties"]["city"] == {"type": "string"}
        assert sorted(result["required"]) == ["city", "street"]

    def test_annotated_with_description(self) -> None:
        from typing import Annotated

        result = python_type_to_json_schema(Annotated[str, "The search query"])
        assert result == {"type": "string", "description": "The search query"}

    def test_annotated_list_with_description(self) -> None:
        from typing import Annotated

        result = python_type_to_json_schema(Annotated[list[int], "List of IDs"])
        assert result == {
            "type": "array",
            "items": {"type": "integer"},
            "description": "List of IDs",
        }

    def test_annotated_without_string_metadata(self) -> None:
        from typing import Annotated

        result = python_type_to_json_schema(Annotated[int, 42])
        assert result == {"type": "integer"}

    def test_annotated_in_dict_style_schema(self) -> None:
        from typing import Annotated

        from claude_agent_sdk import create_sdk_mcp_server, tool

        @tool(
            "search",
            "Search for items",
            {
                "query": Annotated[str, "The search query"],
                "limit": Annotated[int, "Max results to return"],
            },
        )
        async def search(args: dict) -> dict:
            return {"content": [{"type": "text", "text": "ok"}]}

        server = create_sdk_mcp_server("test", tools=[search])
        assert server["type"] == "sdk"


class TestTypedDictToJsonSchema:
    """Tests for the _typeddict_to_json_schema helper."""

    def test_simple_typeddict(self) -> None:
        from typing import TypedDict

        class SearchParams(TypedDict):
            query: str
            max_results: int

        result = typeddict_to_json_schema(SearchParams)
        assert result == {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["max_results", "query"],
        }

    def test_typeddict_with_all_basic_types(self) -> None:
        from typing import TypedDict

        class AllTypes(TypedDict):
            name: str
            count: int
            score: float
            active: bool

        result = typeddict_to_json_schema(AllTypes)
        assert result["type"] == "object"
        assert result["properties"]["name"] == {"type": "string"}
        assert result["properties"]["count"] == {"type": "integer"}
        assert result["properties"]["score"] == {"type": "number"}
        assert result["properties"]["active"] == {"type": "boolean"}
        assert sorted(result["required"]) == ["active", "count", "name", "score"]

    def test_typeddict_with_optional_fields(self) -> None:
        import sys

        if sys.version_info >= (3, 11):
            from typing import NotRequired, TypedDict
        else:
            from typing_extensions import NotRequired, TypedDict

        class Config(TypedDict):
            name: str
            timeout: NotRequired[int]

        result = typeddict_to_json_schema(Config)
        assert result["type"] == "object"
        assert result["properties"]["name"] == {"type": "string"}
        assert result["properties"]["timeout"] == {"type": "integer"}
        assert result["required"] == ["name"]

    def test_typeddict_with_list_field(self) -> None:
        from typing import TypedDict

        class TaggedItem(TypedDict):
            name: str
            tags: list[str]

        result = typeddict_to_json_schema(TaggedItem)
        assert result["properties"]["tags"] == {
            "type": "array",
            "items": {"type": "string"},
        }

    def test_typeddict_with_annotated_descriptions(self) -> None:
        from typing import Annotated, TypedDict

        class SearchParams(TypedDict):
            query: Annotated[str, "The search query"]
            limit: Annotated[int, "Max results to return"]
            verbose: bool

        result = typeddict_to_json_schema(SearchParams)
        assert result["properties"]["query"] == {
            "type": "string",
            "description": "The search query",
        }
        assert result["properties"]["limit"] == {
            "type": "integer",
            "description": "Max results to return",
        }
        assert result["properties"]["verbose"] == {"type": "boolean"}
        assert sorted(result["required"]) == ["limit", "query", "verbose"]

    def test_typeddict_annotated_with_notrequired(self) -> None:
        import sys
        from typing import Annotated

        if sys.version_info >= (3, 11):
            from typing import NotRequired, TypedDict
        else:
            from typing_extensions import NotRequired, TypedDict

        class Config(TypedDict):
            name: Annotated[str, "Config name"]
            timeout: NotRequired[Annotated[int, "Timeout in seconds"]]

        result = typeddict_to_json_schema(Config)
        assert result["properties"]["name"] == {
            "type": "string",
            "description": "Config name",
        }
        assert result["properties"]["timeout"] == {
            "type": "integer",
            "description": "Timeout in seconds",
        }
        assert result["required"] == ["name"]

    def test_nested_typeddict(self) -> None:
        from typing import TypedDict

        class Address(TypedDict):
            street: str
            city: str

        class Person(TypedDict):
            name: str
            address: Address

        result = typeddict_to_json_schema(Person)
        assert result["type"] == "object"
        assert result["properties"]["name"] == {"type": "string"}
        address_schema = result["properties"]["address"]
        assert address_schema["type"] == "object"
        assert address_schema["properties"]["street"] == {"type": "string"}
        assert address_schema["properties"]["city"] == {"type": "string"}

    def test_typeddict_empty(self) -> None:
        from typing import TypedDict

        class Empty(TypedDict):
            pass

        result = typeddict_to_json_schema(Empty)
        assert result == {
            "type": "object",
            "properties": {},
        }


class TestTypedDictMcpIntegration:
    """Tests for TypedDict schemas flowing through create_sdk_mcp_server."""

    @pytest.mark.anyio
    async def test_typeddict_tool_schema_in_list_tools(self) -> None:
        from typing import TypedDict

        class SearchParams(TypedDict):
            query: str
            max_results: int

        @tool("search", "Search for items", SearchParams)
        async def search(args: dict[str, Any]) -> dict[str, Any]:
            return {
                "content": [{"type": "text", "text": f"Results for {args['query']}"}]
            }

        config = create_sdk_mcp_server(name="typeddict-test", tools=[search])
        async with connected(config) as client:
            [listed] = await client.list_tools("srv")

        schema = listed["inputSchema"]
        assert schema["type"] == "object"
        assert schema["properties"]["query"] == {"type": "string"}
        assert schema["properties"]["max_results"] == {"type": "integer"}
        assert sorted(schema["required"]) == ["max_results", "query"]

    @pytest.mark.anyio
    async def test_typeddict_tool_call_works(self) -> None:
        from typing import TypedDict

        class MathParams(TypedDict):
            a: float
            b: float

        @tool("multiply", "Multiply two numbers", MathParams)
        async def multiply(args: dict[str, Any]) -> dict[str, Any]:
            result = args["a"] * args["b"]
            return {"content": [{"type": "text", "text": f"Product: {result}"}]}

        config = create_sdk_mcp_server(name="typeddict-call-test", tools=[multiply])
        async with connected(config) as client:
            result = await client.call_tool("srv", "multiply", {"a": 6, "b": 7})
        assert texts(result) == ["Product: 42"]

    @pytest.mark.anyio
    async def test_dict_schema_still_works(self) -> None:
        @tool("echo", "Echo input", {"message": str})
        async def echo(args: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": args["message"]}]}

        config = create_sdk_mcp_server(name="dict-schema-test", tools=[echo])
        async with connected(config) as client:
            [listed] = await client.list_tools("srv")

        schema = listed["inputSchema"]
        assert schema["type"] == "object"
        assert schema["properties"]["message"] == {"type": "string"}
        assert schema["required"] == ["message"]

    @pytest.mark.anyio
    async def test_json_schema_dict_passthrough(self) -> None:
        json_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "age": {"type": "integer", "minimum": 0},
            },
            "required": ["name"],
        }

        @tool("validate", "Validate input", json_schema)
        async def validate(args: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": "OK"}]}

        config = create_sdk_mcp_server(name="passthrough-test", tools=[validate])
        async with connected(config) as client:
            [listed] = await client.list_tools("srv")
        assert listed["inputSchema"] == json_schema

    @pytest.mark.anyio
    async def test_tool_list_is_stable(self) -> None:
        @tool("cached", "Test caching", {"x": str})
        async def cached(args: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": args["x"]}]}

        config = create_sdk_mcp_server(name="cache-test", tools=[cached])
        async with connected(config) as client:
            first = await client.list_tools("srv")
            second = await client.list_tools("srv")
        assert first == second
