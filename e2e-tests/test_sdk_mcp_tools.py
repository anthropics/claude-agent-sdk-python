"""End-to-end tests for SDK MCP (inline) tools with real Claude API calls.

These tests verify that SDK-created MCP tools work correctly through the full stack,
focusing on tool execution mechanics rather than specific tool functionality.
"""

from typing import Any

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    create_sdk_mcp_server,
    tool,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_sdk_mcp_tool_execution():
    """Test that SDK MCP tools can be called and executed with allowed_tools."""
    executions = []

    @tool("echo", "Echo back the input text", {"text": str})
    async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Echo back whatever text is provided."""
        executions.append("echo")
        return {"content": [{"type": "text", "text": f"Echo: {args['text']}"}]}

    server = create_sdk_mcp_server(
        name="test",
        version="1.0.0",
        tools=[echo_tool],
    )

    options = ClaudeAgentOptions(
        mcp_servers={"test": server},
        allowed_tools=["mcp__test__echo"],
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Call the mcp__test__echo tool with any text")

        async for message in client.receive_response():
            pass  # Just consume messages

    # Check if the actual Python function was called
    assert "echo" in executions, "Echo tool function was not executed"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_sdk_mcp_permission_enforcement():
    """Test that disallowed_tools prevents SDK MCP tool execution."""
    executions = []

    @tool("echo", "Echo back the input text", {"text": str})
    async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Echo back whatever text is provided."""
        executions.append("echo")
        return {"content": [{"type": "text", "text": f"Echo: {args['text']}"}]}

    @tool("greet", "Greet a person by name", {"name": str})
    async def greet_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Greet someone by name."""
        executions.append("greet")
        return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}

    server = create_sdk_mcp_server(
        name="test",
        version="1.0.0",
        tools=[echo_tool, greet_tool],
    )

    options = ClaudeAgentOptions(
        mcp_servers={"test": server},
        disallowed_tools=["mcp__test__echo"],  # Block echo tool
        allowed_tools=["mcp__test__greet"],  # But allow greet
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Use the echo tool to echo 'test' and use greet tool to greet 'Alice'"
        )

        async for message in client.receive_response():
            pass  # Just consume messages

    # Check actual function executions
    assert "echo" not in executions, "Disallowed echo tool was executed"
    assert "greet" in executions, "Allowed greet tool was not executed"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_sdk_mcp_multiple_tools():
    """Test that multiple SDK MCP tools can be called in sequence."""
    executions = []

    @tool("echo", "Echo back the input text", {"text": str})
    async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Echo back whatever text is provided."""
        executions.append("echo")
        return {"content": [{"type": "text", "text": f"Echo: {args['text']}"}]}

    @tool("greet", "Greet a person by name", {"name": str})
    async def greet_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Greet someone by name."""
        executions.append("greet")
        return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}

    server = create_sdk_mcp_server(
        name="multi",
        version="1.0.0",
        tools=[echo_tool, greet_tool],
    )

    options = ClaudeAgentOptions(
        mcp_servers={"multi": server},
        allowed_tools=["mcp__multi__echo", "mcp__multi__greet"],
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Call mcp__multi__echo with text='test' and mcp__multi__greet with name='Bob'"
        )

        async for message in client.receive_response():
            pass  # Just consume messages

    # Both tools should have been executed
    assert "echo" in executions, "Echo tool was not executed"
    assert "greet" in executions, "Greet tool was not executed"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_sdk_mcp_without_permissions():
    """Test SDK MCP tool behavior without explicit allowed_tools."""
    executions = []

    @tool("echo", "Echo back the input text", {"text": str})
    async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Echo back whatever text is provided."""
        executions.append("echo")
        return {"content": [{"type": "text", "text": f"Echo: {args['text']}"}]}

    server = create_sdk_mcp_server(
        name="noperm",
        version="1.0.0",
        tools=[echo_tool],
    )

    # No allowed_tools specified
    options = ClaudeAgentOptions(
        mcp_servers={"noperm": server},
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Call the mcp__noperm__echo tool")

        async for message in client.receive_response():
            pass  # Just consume messages

    assert "echo" not in executions, "SDK MCP tool was executed"
