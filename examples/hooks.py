#!/usr/bin/env python
"""Example of using hooks with Claude Code SDK via ClaudeAgentOptions.

This file demonstrates various hook patterns using the hooks parameter
in ClaudeAgentOptions instead of decorator-based hooks.

Usage:
./examples/hooks.py - List the examples
./examples/hooks.py all - Run all examples
./examples/hooks.py PreToolUse - Run a specific example
"""

import asyncio
import logging
import sys
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    HookContext,
    HookJSONOutput,
    HookMatcher,
    Message,
    ResultMessage,
    TextBlock,
)

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


def display_message(msg: Message) -> None:
    """Standardized message display function."""
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
    elif isinstance(msg, ResultMessage):
        print("Result ended")


##### Hook callback functions
async def check_bash_command(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    """Prevent certain bash commands from being executed."""
    tool_name = input_data["tool_name"]
    tool_input = input_data["tool_input"]

    if tool_name != "Bash":
        return {}

    command = tool_input.get("command", "")
    block_patterns = ["foo.sh"]

    for pattern in block_patterns:
        if pattern in command:
            logger.warning(f"Blocked command: {command}")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Command contains invalid pattern: {pattern}",
                }
            }

    return {}


async def add_custom_instructions(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    """Add custom instructions when a session starts."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "My favorite color is hot pink",
        }
    }


async def example_pretooluse() -> None:
    """Basic example demonstrating hook protection."""
    print("=== PreToolUse Example ===")
    print("This example demonstrates how PreToolUse can block some bash commands but not others.\n")

    # Configure hooks using ClaudeAgentOptions
    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[check_bash_command]),
            ],
        }
    )

    async with ClaudeSDKClient(options=options) as client:
        # Test 1: Command with forbidden pattern (will be blocked)
        print("Test 1: Trying a command that our PreToolUse hook should block...")
        print("User: Run the bash command: ./foo.sh --help")
        await client.query("Run the bash command: ./foo.sh --help")

        async for msg in client.receive_response():
            display_message(msg)

        print("\n" + "=" * 50 + "\n")

        # Test 2: Safe command that should work
        print("Test 2: Trying a command that our PreToolUse hook should allow...")
        print("User: Run the bash command: echo 'Hello from hooks example!'")
        await client.query("Run the bash command: echo 'Hello from hooks example!'")

        async for msg in client.receive_response():
            display_message(msg)

        print("\n" + "=" * 50 + "\n")

    print("\n")


async def example_userpromptsubmit() -> None:
    """Demonstrate context retention across conversation."""
    print("=== UserPromptSubmit Example ===")
    print("This example shows how a UserPromptSubmit hook can add context.\n")

    options = ClaudeAgentOptions(
        hooks={
            "UserPromptSubmit": [
                HookMatcher(matcher=None, hooks=[add_custom_instructions]),
            ],
        }
    )

    async with ClaudeSDKClient(options=options) as client:
        print("User: What's my favorite color?")
        await client.query("What's my favorite color?")

        async for msg in client.receive_response():
            display_message(msg)

    print("\n")


async def main() -> None:
    """Run all examples or a specific example based on command line argument."""
    examples = {
        "PreToolUse": example_pretooluse,
        "UserPromptSubmit": example_userpromptsubmit,
    }

    if len(sys.argv) < 2:
        # List available examples
        print("Usage: python hooks.py <example_name>")
        print("\nAvailable examples:")
        print("  all - Run all examples")
        for name in examples:
            print(f"  {name}")
        sys.exit(0)

    example_name = sys.argv[1]

    if example_name == "all":
        # Run all examples
        for example in examples.values():
            await example()
            print("-" * 50 + "\n")
    elif example_name in examples:
        # Run specific example
        await examples[example_name]()
    else:
        print(f"Error: Unknown example '{example_name}'")
        print("\nAvailable examples:")
        print("  all - Run all examples")
        for name in examples:
            print(f"  {name}")
        sys.exit(1)


if __name__ == "__main__":
    print("Starting Claude SDK Hooks Examples...")
    print("=" * 50 + "\n")
    asyncio.run(main())
