#!/usr/bin/env python3
"""Example: discovering and running slash commands through the SDK.

Slash commands are sent as ordinary prompts with a leading ``/``. Only commands
that work in a non-interactive session are available: built-ins such as
``/compact``, ``/context`` and ``/cost``, plus custom commands and skills the
CLI loaded. Interactive-only commands such as ``/help`` are not.

``ClaudeSDKClient.supported_commands()`` returns the commands available in the
current session, with each command's description and argument hint.

Custom commands in a project's ``.claude/commands/`` directory load only when
``cwd`` points at the project and ``setting_sources`` includes ``"project"``.

Usage:
./examples/slash_commands.py
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    UserMessage,
)


async def main() -> None:
    options = ClaudeAgentOptions(
        cwd=Path(__file__).parent.parent,
        setting_sources=["project"],
    )

    async with ClaudeSDKClient(options=options) as client:
        print("=== Available slash commands ===")
        for cmd in await client.supported_commands():
            hint = f" {cmd['argumentHint']}" if cmd["argumentHint"] else ""
            print(f"/{cmd['name']}{hint} - {cmd['description']}")

        await client.query("Remember the number 42.")
        async for _ in client.receive_response():
            pass

        # Built-in commands print their output as a UserMessage wrapped in
        # <local-command-stdout> tags. /compact also emits a SystemMessage
        # with subtype "compact_boundary".
        print("\n=== /compact ===")
        await client.query("/compact Keep only the number to remember.")
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
                print("Conversation compacted.")
            elif isinstance(msg, UserMessage) and isinstance(msg.content, str):
                print(msg.content)
            elif isinstance(msg, ResultMessage):
                print(f"Done ({msg.subtype}).")

        # Always drain receive_response() before sending the next query.
        print("\n=== /cost ===")
        await client.query("/cost")
        async for msg in client.receive_response():
            if isinstance(msg, UserMessage) and isinstance(msg.content, str):
                print(msg.content)


if __name__ == "__main__":
    asyncio.run(main())
