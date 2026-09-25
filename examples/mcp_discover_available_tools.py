#!/usr/bin/env python3
"""Example: Discover Available MCP Tools.

This example demonstrates how to inspect which MCP servers connected
successfully and which tools are available after a query starts.

At the beginning of every query the SDK emits a SystemMessage with
subtype "init". That message's ``data`` dict contains:

* ``mcp_servers`` – list of ``{"name": str, "status": str}`` dicts
  describing each configured server and its connection status.
* ``tools`` – flat list of every tool name Claude can call in this
  session, including MCP tools that follow the pattern
  ``mcp__<server-name>__<tool-name>``.

Common status values
--------------------
- ``"connected"``  – server is ready and its tools are available.
- ``"pending"``    – server is still starting up (e.g. slow npx install).
- ``"failed"``     – server could not start (missing binary, bad config).
- ``"needs-auth"`` – server requires authentication before it can be used.

Important: use ``isinstance(message, SystemMessage)`` to detect this
message type. ``SystemMessage`` does not expose a ``.type`` attribute —
accessing it raises ``AttributeError``.
"""

import asyncio

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    query,
)


def _print_server_status(mcp_servers: list[dict]) -> None:
    """Print connection status for every configured MCP server."""
    if not mcp_servers:
        print("No MCP servers configured.")
        return

    print("MCP server connection status:")
    for server in mcp_servers:
        name = server.get("name", "unknown")
        status = server.get("status", "unknown")
        icon = "✓" if status == "connected" else "✗"
        print(f"  {icon} {name}: {status}")


def _print_available_mcp_tools(all_tools: list[str]) -> None:
    """Filter and print only the MCP tools from the full tools list."""
    mcp_tools = [t for t in all_tools if t.startswith("mcp__")]
    if not mcp_tools:
        print("\nNo MCP tools available in this session.")
        return

    print(f"\nAvailable MCP tools ({len(mcp_tools)}):")
    for tool_name in mcp_tools:
        print(f"  - {tool_name}")


async def main() -> None:
    """Connect to the Claude Code docs MCP server and report available tools."""
    options = ClaudeAgentOptions(
        mcp_servers={
            "claude-code-docs": {
                "type": "http",
                "url": "https://code.claude.com/docs/mcp",
            }
        },
        # Wildcard allows every tool the server exposes.
        allowed_tools=["mcp__claude-code-docs__*"],
        max_turns=1,
    )

    async for message in query(
        prompt="List the tools you have available from the MCP server.",
        options=options,
    ):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            # Inspect server connection status at session start.
            _print_server_status(message.data.get("mcp_servers", []))
            _print_available_mcp_tools(message.data.get("tools", []))
            print()

        elif isinstance(message, ResultMessage) and message.subtype == "success":
            print(f"Result:\n{message.result}")

            if message.total_cost_usd:
                print(f"\nCost: ${message.total_cost_usd:.6f}")


if __name__ == "__main__":
    asyncio.run(main())
