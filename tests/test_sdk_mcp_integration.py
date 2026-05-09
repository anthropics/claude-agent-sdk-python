"""Integration tests for SDK MCP server support.

This test file verifies that SDK MCP servers work correctly through the full stack,
matching the TypeScript SDK test/sdk.test.ts pattern.
"""

import base64
import logging
from typing import Any

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

from claude_agent_sdk import (
    ClaudeAgentOptions,
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


@pytest.mark.asyncio
async def test_sdk_mcp_server_handlers():
    """Test that SDK MCP server handlers are properly registered."""
    # Track tool executions
    tool_executions: list[dict[str, Any]] = []

    # Create SDK MCP server with multiple tools
    @tool("greet_user", "Greets a user by name", {"name": str})
    async def greet_user(args: dict[str, Any]) -> dict[str, Any]:
        tool_executions.append({"name": "greet_user", "args": args})
        return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}

    @tool("add_numbers", "Adds two numbers", {"a": float, "b": float})
    async def add_numbers(args: dict[str, Any]) -> dict[str, Any]:
        tool_executions.append({"name": "add_numbers", "args": args})
        result = args["a"] + args["b"]
        return {"content": [{"type": "text", "text": f"The sum is {result}"}]}

    server_config = create_sdk_mcp_server(
        name="test-sdk-server", version="1.0.0", tools=[greet_user, add_numbers]
    )

    # Verify server configuration
    assert server_config["type"] == "sdk"
    assert server_config["name"] == "test-sdk-server"
    assert "instance" in server_config

    # Get the server instance
    server = server_config["instance"]

    # Import the request types to check handlers
    from mcp.types import CallToolRequest, ListToolsRequest

    # Verify handlers are registered
    assert ListToolsRequest in server.request_handlers
    assert CallToolRequest in server.request_handlers

    # Test list_tools handler - the decorator wraps our function
    list_handler = server.request_handlers[ListToolsRequest]
    request = ListToolsRequest(method="tools/list")
    response = await list_handler(request)
    # Response is ServerResult with nested ListToolsResult
    assert len(response.root.tools) == 2

    # Check tool definitions
    tool_names = [t.name for t in response.root.tools]
    assert "greet_user" in tool_names
    assert "add_numbers" in tool_names

    # Test call_tool handler
    call_handler = server.request_handlers[CallToolRequest]

    # Call greet_user - CallToolRequest wraps the call
    from mcp.types import CallToolRequestParams

    greet_request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="greet_user", arguments={"name": "Alice"}),
    )
    result = await call_handler(greet_request)
    # Response is ServerResult with nested CallToolResult
    assert result.root.content[0].text == "Hello, Alice!"
    assert len(tool_executions) == 1
    assert tool_executions[0]["name"] == "greet_user"
    assert tool_executions[0]["args"]["name"] == "Alice"

    # Call add_numbers
    add_request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="add_numbers", arguments={"a": 5, "b": 3}),
    )
    result = await call_handler(add_request)
    assert "8" in result.root.content[0].text
    assert len(tool_executions) == 2
    assert tool_executions[1]["name"] == "add_numbers"
    assert tool_executions[1]["args"]["a"] == 5
    assert tool_executions[1]["args"]["b"] == 3


@pytest.mark.asyncio
async def test_tool_creation():
    """Test that tools can be created with proper schemas."""

    @tool("echo", "Echo input", {"input": str})
    async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"output": args["input"]}

    # Verify tool was created
    assert echo_tool.name == "echo"
    assert echo_tool.description == "Echo input"
    assert echo_tool.input_schema == {"input": str}
    assert callable(echo_tool.handler)

    # Test the handler works
    result = await echo_tool.handler({"input": "test"})
    assert result == {"output": "test"}


@pytest.mark.asyncio
async def test_error_handling():
    """Test that tool errors are properly handled."""

    @tool("fail", "Always fails", {})
    async def fail_tool(args: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("Expected error")

    # Verify the tool raises an error when called directly
    with pytest.raises(ValueError, match="Expected error"):
        await fail_tool.handler({})

    # Test error handling through the server
    server_config = create_sdk_mcp_server(name="error-test", tools=[fail_tool])

    server = server_config["instance"]
    from mcp.types import CallToolRequest

    call_handler = server.request_handlers[CallToolRequest]

    # The handler should return an error result, not raise
    from mcp.types import CallToolRequestParams

    fail_request = CallToolRequest(
        method="tools/call", params=CallToolRequestParams(name="fail", arguments={})
    )
    result = await call_handler(fail_request)
    # MCP SDK catches exceptions and returns error results
    assert result.root.isError
    assert "Expected error" in str(result.root.content[0].text)


@pytest.mark.asyncio
async def test_is_error_flag_propagated():
    """Test that is_error flag from tool result dict is propagated to CallToolResult."""

    @tool("divide", "Divide two numbers", {"a": float, "b": float})
    async def divide(args: dict[str, Any]) -> dict[str, Any]:
        if args["b"] == 0:
            return {
                "content": [{"type": "text", "text": "Division by zero"}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": str(args["a"] / args["b"])}]}

    server_config = create_sdk_mcp_server(name="error-flag-test", tools=[divide])
    server = server_config["instance"]
    call_handler = server.request_handlers[CallToolRequest]

    # Test error case — is_error: True should be propagated
    error_request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="divide", arguments={"a": 1, "b": 0}),
    )
    result = await call_handler(error_request)
    assert result.root.isError is True
    assert result.root.content[0].text == "Division by zero"

    # Test success case — is_error should default to False
    success_request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="divide", arguments={"a": 6, "b": 3}),
    )
    result = await call_handler(success_request)
    assert result.root.isError is not True
    assert "2.0" in result.root.content[0].text


@pytest.mark.asyncio
async def test_mixed_servers():
    """Test that SDK and external MCP servers can work together."""

    # Create an SDK server
    @tool("sdk_tool", "SDK tool", {})
    async def sdk_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"result": "from SDK"}

    sdk_server = create_sdk_mcp_server(name="sdk-server", tools=[sdk_tool])

    # Create configuration with both SDK and external servers
    external_server = {"type": "stdio", "command": "echo", "args": ["test"]}

    options = ClaudeAgentOptions(
        mcp_servers={"sdk": sdk_server, "external": external_server}
    )

    # Verify both server types are in the configuration
    assert "sdk" in options.mcp_servers
    assert "external" in options.mcp_servers
    assert options.mcp_servers["sdk"]["type"] == "sdk"
    assert options.mcp_servers["external"]["type"] == "stdio"


@pytest.mark.asyncio
async def test_server_creation():
    """Test that SDK MCP servers are created correctly."""
    server = create_sdk_mcp_server(name="test-server", version="2.0.0", tools=[])

    # Verify server configuration
    assert server["type"] == "sdk"
    assert server["name"] == "test-server"
    assert "instance" in server
    assert server["instance"] is not None

    # Verify the server instance has the right attributes
    instance = server["instance"]
    assert instance.name == "test-server"
    assert instance.version == "2.0.0"

    # With no tools, no handlers are registered if tools is empty
    from mcp.types import ListToolsRequest

    # When no tools are provided, the handlers are not registered
    assert ListToolsRequest not in instance.request_handlers


@pytest.mark.asyncio
async def test_image_content_support():
    """Test that tools can return image content with base64 data."""

    # Create sample base64 image data (a simple 1x1 pixel PNG)
    png_data = base64.b64encode(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13"
        b"\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\x0cIDATx\x9cc```"
        b"\x00\x00\x00\x04\x00\x01]U!\x1c\x00\x00\x00\x00IEND\xaeB`\x82"
    ).decode("utf-8")

    # Track tool executions
    tool_executions: list[dict[str, Any]] = []

    # Create a tool that returns both text and image content
    @tool(
        "generate_chart", "Generates a chart and returns it as an image", {"title": str}
    )
    async def generate_chart(args: dict[str, Any]) -> dict[str, Any]:
        tool_executions.append({"name": "generate_chart", "args": args})
        return {
            "content": [
                {"type": "text", "text": f"Generated chart: {args['title']}"},
                {
                    "type": "image",
                    "data": png_data,
                    "mimeType": "image/png",
                },
            ]
        }

    server_config = create_sdk_mcp_server(
        name="image-test-server", version="1.0.0", tools=[generate_chart]
    )

    # Get the server instance
    server = server_config["instance"]

    call_handler = server.request_handlers[CallToolRequest]

    # Call the chart generation tool
    chart_request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="generate_chart", arguments={"title": "Sales Report"}
        ),
    )
    result = await call_handler(chart_request)

    # Verify the result contains both text and image content
    assert len(result.root.content) == 2

    # Check text content
    text_content = result.root.content[0]
    assert text_content.type == "text"
    assert text_content.text == "Generated chart: Sales Report"

    # Check image content
    image_content = result.root.content[1]
    assert image_content.type == "image"
    assert image_content.data == png_data
    assert image_content.mimeType == "image/png"

    # Verify the tool was executed correctly
    assert len(tool_executions) == 1
    assert tool_executions[0]["name"] == "generate_chart"
    assert tool_executions[0]["args"]["title"] == "Sales Report"


@pytest.mark.asyncio
async def test_tool_annotations():
    """Test that tool annotations are stored and flow through list_tools."""

    @tool(
        "read_data",
        "Read data from source",
        {"source": str},
        annotations=ToolAnnotations(readOnlyHint=True),
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

    @tool(
        "search",
        "Search the web",
        {"query": str},
        annotations=ToolAnnotations(openWorldHint=True),
    )
    async def search(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"Results for {args['query']}"}]}

    @tool("no_annotations", "Tool without annotations", {"x": str})
    async def no_annotations(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": args["x"]}]}

    # Verify annotations stored on SdkMcpTool
    assert read_data.annotations is not None
    assert read_data.annotations.readOnlyHint is True
    assert delete_item.annotations is not None
    assert delete_item.annotations.destructiveHint is True
    assert delete_item.annotations.idempotentHint is True
    assert search.annotations is not None
    assert search.annotations.openWorldHint is True
    assert no_annotations.annotations is None

    # Verify annotations flow through list_tools handler
    server_config = create_sdk_mcp_server(
        name="annotations-test",
        tools=[read_data, delete_item, search, no_annotations],
    )
    server = server_config["instance"]

    from mcp.types import ListToolsRequest

    list_handler = server.request_handlers[ListToolsRequest]
    request = ListToolsRequest(method="tools/list")
    response = await list_handler(request)

    tools_by_name = {t.name: t for t in response.root.tools}

    assert tools_by_name["read_data"].annotations is not None
    assert tools_by_name["read_data"].annotations.readOnlyHint is True
    assert tools_by_name["delete_item"].annotations is not None
    assert tools_by_name["delete_item"].annotations.destructiveHint is True
    assert tools_by_name["delete_item"].annotations.idempotentHint is True
    assert tools_by_name["search"].annotations is not None
    assert tools_by_name["search"].annotations.openWorldHint is True
    assert tools_by_name["no_annotations"].annotations is None


@pytest.mark.asyncio
async def test_tool_annotations_in_jsonrpc():
    """Test that annotations are included in JSONRPC tools/list response."""
    from claude_agent_sdk._internal.query import Query

    @tool(
        "read_only_tool",
        "A read-only tool",
        {"input": str},
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def read_only_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": args["input"]}]}

    @tool("plain_tool", "A tool without annotations", {"input": str})
    async def plain_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": args["input"]}]}

    server_config = create_sdk_mcp_server(
        name="jsonrpc-annotations-test",
        tools=[read_only_tool, plain_tool],
    )

    # Simulate the JSONRPC tools/list request
    query_instance = Query.__new__(Query)
    query_instance.sdk_mcp_servers = {"test": server_config["instance"]}

    response = await query_instance._handle_sdk_mcp_request(
        "test",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )

    assert response is not None
    tools_data = response["result"]["tools"]
    tools_by_name = {t["name"]: t for t in tools_data}

    # Tool with annotations should include them
    assert "annotations" in tools_by_name["read_only_tool"]
    assert tools_by_name["read_only_tool"]["annotations"]["readOnlyHint"] is True
    assert tools_by_name["read_only_tool"]["annotations"]["openWorldHint"] is False

    # Tool without annotations should not have the key
    assert "annotations" not in tools_by_name["plain_tool"]


def test_max_result_size_chars_annotation_flows_to_cli():
    """maxResultSizeChars annotation reaches the CLI via the tools/list JSONRPC response.

    This is the Python SDK half of the fix for large-MCP-result spill (Option 3).
    The CLI's layer-2 spill threshold (toolResultStorage.ts maybePersistLargeToolResult)
    defaults to 50 000 chars regardless of tool output size.  The companion CLI change
    reads annotations.maxResultSizeChars from the tools/list response and uses it as
    the per-tool threshold, bypassing the 50 K clamp when explicitly declared.

    ToolAnnotations from mcp.types has extra="allow", so maxResultSizeChars is
    preserved through model_dump() and included in the JSONRPC payload the CLI reads.
    No SDK code change is required — this test documents and locks in the behavior.
    """
    import anyio

    from claude_agent_sdk._internal.query import Query

    @tool(
        "get_large_schema",
        "Returns a large DB schema that may exceed 50K chars.",
        {},
        annotations=ToolAnnotations(maxResultSizeChars=500_000),
    )
    async def get_large_schema(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "schema"}]}

    @tool("small_tool", "Returns a small result.", {})
    async def small_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}

    server_config = create_sdk_mcp_server(
        name="large-output-test",
        tools=[get_large_schema, small_tool],
    )

    async def _run():
        query_instance = Query.__new__(Query)
        query_instance.sdk_mcp_servers = {
            "large-output-test": server_config["instance"]
        }
        return await query_instance._handle_sdk_mcp_request(
            "large-output-test",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

    response = anyio.run(_run)
    tools_by_name = {t["name"]: t for t in response["result"]["tools"]}

    # maxResultSizeChars must appear in _meta so the CLI can read it.
    # The MCP SDK's Zod schema strips unknown annotation fields, so we use
    # _meta with a namespaced key instead (matching anthropic/searchHint pattern).
    assert "_meta" in tools_by_name["get_large_schema"], (
        "_meta missing from tools/list response — "
        "the CLI will not see anthropic/maxResultSizeChars and layer-2 spill cannot be bypassed."
    )
    assert (
        tools_by_name["get_large_schema"]["_meta"]["anthropic/maxResultSizeChars"]
        == 500_000
    ), (
        "anthropic/maxResultSizeChars not forwarded correctly in _meta — "
        "CLI MCPTool will use its hardcoded 100K default instead."
    )

    # Tools without the annotation must not have the key.
    assert "anthropic/maxResultSizeChars" not in tools_by_name["small_tool"].get(
        "_meta", {}
    )


@pytest.mark.asyncio
async def test_resource_link_content_converted_to_text():
    """Test that resource_link content blocks are converted to text."""

    @tool("get_resource", "Returns a resource link", {"url": str})
    async def get_resource(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "resource_link",
                    "name": "My Document",
                    "uri": args["url"],
                    "description": "A test document",
                },
            ]
        }

    server_config = create_sdk_mcp_server(
        name="resource-link-test", tools=[get_resource]
    )
    server = server_config["instance"]
    call_handler = server.request_handlers[CallToolRequest]

    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="get_resource",
            arguments={"url": "https://example.com/doc.pdf"},
        ),
    )
    result = await call_handler(request)

    assert len(result.root.content) == 1
    assert result.root.content[0].type == "text"
    assert "My Document" in result.root.content[0].text
    assert "https://example.com/doc.pdf" in result.root.content[0].text
    assert "A test document" in result.root.content[0].text


@pytest.mark.asyncio
async def test_embedded_resource_text_content_converted():
    """Test that embedded resource with text content is converted to text."""

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
                },
            ]
        }

    server_config = create_sdk_mcp_server(
        name="embedded-resource-test", tools=[get_embedded]
    )
    server = server_config["instance"]
    call_handler = server.request_handlers[CallToolRequest]

    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="get_embedded", arguments={}),
    )
    result = await call_handler(request)

    assert len(result.root.content) == 1
    assert result.root.content[0].type == "text"
    assert result.root.content[0].text == "File contents here"


@pytest.mark.asyncio
async def test_binary_embedded_resource_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
):
    """Test that binary embedded resources are skipped with a warning."""

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
                },
            ]
        }

    server_config = create_sdk_mcp_server(
        name="binary-resource-test", tools=[get_binary]
    )
    server = server_config["instance"]
    call_handler = server.request_handlers[CallToolRequest]

    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="get_binary", arguments={}),
    )
    with caplog.at_level(logging.WARNING):
        result = await call_handler(request)

    assert len(result.root.content) == 0
    assert "Binary embedded resource" in caplog.text


@pytest.mark.asyncio
async def test_unknown_content_type_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
):
    """Test that unknown content types are skipped with a warning."""

    @tool("get_unknown", "Returns unknown content type", {})
    async def get_unknown(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {"type": "custom_widget", "data": "some data"},
            ]
        }

    server_config = create_sdk_mcp_server(name="unknown-type-test", tools=[get_unknown])
    server = server_config["instance"]
    call_handler = server.request_handlers[CallToolRequest]

    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="get_unknown", arguments={}),
    )
    with caplog.at_level(logging.WARNING):
        result = await call_handler(request)

    assert len(result.root.content) == 0
    assert "Unsupported content type" in caplog.text
    assert "custom_widget" in caplog.text


@pytest.mark.asyncio
async def test_mixed_content_types_with_resource_link():
    """Test that mixed content with text, image, and resource_link works."""

    png_data = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("utf-8")

    @tool("get_mixed", "Returns mixed content", {})
    async def get_mixed(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": "Here is the document:"},
                {"type": "image", "data": png_data, "mimeType": "image/png"},
                {
                    "type": "resource_link",
                    "name": "Report",
                    "uri": "https://example.com/report",
                },
            ]
        }

    server_config = create_sdk_mcp_server(name="mixed-content-test", tools=[get_mixed])
    server = server_config["instance"]
    call_handler = server.request_handlers[CallToolRequest]

    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="get_mixed", arguments={}),
    )
    result = await call_handler(request)

    assert len(result.root.content) == 3
    assert result.root.content[0].type == "text"
    assert result.root.content[0].text == "Here is the document:"
    assert result.root.content[1].type == "image"
    assert result.root.content[2].type == "text"
    assert "Report" in result.root.content[2].text


@pytest.mark.asyncio
async def test_jsonrpc_bridge_resource_link():
    """Test that the JSONRPC bridge converts resource_link content to text."""
    from claude_agent_sdk._internal.query import Query

    @tool("link_tool", "Returns a link", {})
    async def link_tool(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "resource_link",
                    "name": "API Docs",
                    "uri": "https://api.example.com",
                    "description": "The API documentation",
                }
            ]
        }

    server_config = create_sdk_mcp_server(name="jsonrpc-link-test", tools=[link_tool])

    query_instance = Query.__new__(Query)
    query_instance.sdk_mcp_servers = {"test": server_config["instance"]}

    response = await query_instance._handle_sdk_mcp_request(
        "test",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "link_tool", "arguments": {}},
        },
    )

    assert response is not None
    result_content = response["result"]["content"]
    assert len(result_content) == 1
    assert result_content[0]["type"] == "text"
    assert "API Docs" in result_content[0]["text"]
    assert "https://api.example.com" in result_content[0]["text"]


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

    @pytest.mark.asyncio
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

        server_config = create_sdk_mcp_server(name="typeddict-test", tools=[search])
        server = server_config["instance"]
        list_handler = server.request_handlers[ListToolsRequest]
        request = ListToolsRequest(method="tools/list")
        response = await list_handler(request)

        tools = response.root.tools
        assert len(tools) == 1
        schema = tools[0].inputSchema
        assert schema["type"] == "object"
        assert schema["properties"]["query"] == {"type": "string"}
        assert schema["properties"]["max_results"] == {"type": "integer"}
        assert sorted(schema["required"]) == ["max_results", "query"]

    @pytest.mark.asyncio
    async def test_typeddict_tool_call_works(self) -> None:
        from typing import TypedDict

        class MathParams(TypedDict):
            a: float
            b: float

        @tool("multiply", "Multiply two numbers", MathParams)
        async def multiply(args: dict[str, Any]) -> dict[str, Any]:
            result = args["a"] * args["b"]
            return {"content": [{"type": "text", "text": f"Product: {result}"}]}

        server_config = create_sdk_mcp_server(
            name="typeddict-call-test", tools=[multiply]
        )
        server = server_config["instance"]
        call_handler = server.request_handlers[CallToolRequest]

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="multiply", arguments={"a": 6, "b": 7}),
        )
        result = await call_handler(request)
        assert "42" in result.root.content[0].text

    @pytest.mark.asyncio
    async def test_dict_schema_still_works(self) -> None:
        @tool("echo", "Echo input", {"message": str})
        async def echo(args: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": args["message"]}]}

        server_config = create_sdk_mcp_server(name="dict-schema-test", tools=[echo])
        server = server_config["instance"]
        list_handler = server.request_handlers[ListToolsRequest]
        request = ListToolsRequest(method="tools/list")
        response = await list_handler(request)

        schema = response.root.tools[0].inputSchema
        assert schema["type"] == "object"
        assert schema["properties"]["message"] == {"type": "string"}
        assert schema["required"] == ["message"]

    @pytest.mark.asyncio
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

        server_config = create_sdk_mcp_server(name="passthrough-test", tools=[validate])
        server = server_config["instance"]
        list_handler = server.request_handlers[ListToolsRequest]
        request = ListToolsRequest(method="tools/list")
        response = await list_handler(request)

        schema = response.root.tools[0].inputSchema
        assert schema == json_schema

    @pytest.mark.asyncio
    async def test_cached_tool_list_is_stable(self) -> None:
        @tool("cached", "Test caching", {"x": str})
        async def cached(args: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": args["x"]}]}

        server_config = create_sdk_mcp_server(name="cache-test", tools=[cached])
        server = server_config["instance"]
        list_handler = server.request_handlers[ListToolsRequest]
        request = ListToolsRequest(method="tools/list")

        response1 = await list_handler(request)
        response2 = await list_handler(request)
        assert response1.root.tools == response2.root.tools
