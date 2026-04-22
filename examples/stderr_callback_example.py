"""Simple example demonstrating stderr callback for capturing CLI debug output."""

import asyncio

from claude_agent_sdk import ClaudeAgentOptions, query


async def main():
    """Capture stderr output from the CLI using a callback."""

    # Collect stderr messages
    stderr_messages = []

    def stderr_callback(message: str):
        """Callback that receives each line of stderr output."""
        stderr_messages.append(message)
        # Optionally print specific messages
        if "[ERROR]" in message:
            print(f"Error detected: {message}")

    # Create options with stderr callback. The callback receives any stderr the
    # CLI emits (warnings, errors). For verbose CLI debug logs, pass
    # extra_args={"debug-file": "/path/to/log"} and read that file instead.
    options = ClaudeAgentOptions(stderr=stderr_callback)

    # Run a query
    print("Running query with stderr capture...")
    async for message in query(
        prompt="What is 2+2?",
        options=options
    ):
        if hasattr(message, 'content'):
            if isinstance(message.content, str):
                print(f"Response: {message.content}")

    # Show what we captured
    print(f"\nCaptured {len(stderr_messages)} stderr lines")
    if stderr_messages:
        print("First stderr line:", stderr_messages[0][:100])


if __name__ == "__main__":
    asyncio.run(main())