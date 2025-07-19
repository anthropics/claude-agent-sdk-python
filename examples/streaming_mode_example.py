#!/usr/bin/env python3
"""Example demonstrating streaming mode with bidirectional communication."""

import asyncio
from collections.abc import AsyncIterator

from claude_code_sdk import ClaudeCodeOptions, ClaudeSDKClient, query


async def create_message_stream() -> AsyncIterator[dict]:
    """Create an async stream of user messages."""
    # Example messages to send
    messages = [
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "Hello! Please tell me a bit about Python async programming.",
            },
            "parent_tool_use_id": None,
            "session_id": "example-session-1",
        },
        # Add a delay to simulate interactive conversation
        None,  # We'll use this as a signal to delay
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "Can you give me a simple code example?",
            },
            "parent_tool_use_id": None,
            "session_id": "example-session-1",
        },
    ]

    for msg in messages:
        if msg is None:
            await asyncio.sleep(2)  # Simulate user thinking time
            continue
        yield msg


async def example_string_mode():
    """Example using traditional string mode (backward compatible)."""
    print("=== String Mode Example ===")

    # Option 1: Using query function
    async for message in query(
        prompt="What is 2+2? Please give a brief answer.", options=ClaudeCodeOptions()
    ):
        print(f"Received: {type(message).__name__}")
        if hasattr(message, "content"):
            print(f"  Content: {message.content}")

    print("Completed\n")


async def example_streaming_mode():
    """Example using new streaming mode with async iterable."""
    print("=== Streaming Mode Example ===")

    options = ClaudeCodeOptions()

    # Create message stream
    message_stream = create_message_stream()

    # Use query with async iterable
    message_count = 0
    async for message in query(prompt=message_stream, options=options):
        message_count += 1
        msg_type = type(message).__name__

        print(f"\nMessage #{message_count} ({msg_type}):")

        if hasattr(message, "content"):
            content = message.content
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "text"):
                        print(f"  {block.text}")
            else:
                print(f"  {content}")
        elif hasattr(message, "subtype"):
            print(f"  Subtype: {message.subtype}")

    print("\nCompleted")


async def example_with_context_manager():
    """Example using context manager for cleaner code."""
    print("=== Context Manager Example ===")

    # Simple one-shot query with automatic cleanup
    async with ClaudeSDKClient() as client:
        await client.send_message("What is the meaning of life?")
        async for message in client.receive_messages():
            if hasattr(message, "content"):
                print(f"Response: {message.content}")

    print("\nCompleted with automatic cleanup\n")


async def example_with_interrupt():
    """Example demonstrating interrupt functionality."""
    print("=== Streaming Mode with Interrupt Example ===")

    options = ClaudeCodeOptions()
    client = ClaudeSDKClient(options=options)

    async def interruptible_stream():
        """Stream that we'll interrupt."""
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": "Count to 1000 slowly, saying each number.",
            },
            "parent_tool_use_id": None,
            "session_id": "interrupt-example",
        }
        # Keep the stream open by waiting indefinitely
        # This prevents stdin from being closed
        await asyncio.Event().wait()

    try:
        await client.connect(interruptible_stream())
        print("Connected - will interrupt after 3 seconds")

        # Create tasks for receiving and interrupting
        async def receive_and_interrupt():
            # Start a background task to continuously receive messages
            async def receive_messages():
                async for message in client.receive_messages():
                    msg_type = type(message).__name__
                    print(f"Received: {msg_type}")

                    if hasattr(message, "content") and isinstance(
                        message.content, list
                    ):
                        for block in message.content:
                            if hasattr(block, "text"):
                                print(f"  {block.text[:50]}...")  # First 50 chars

            # Start receiving in background
            receive_task = asyncio.create_task(receive_messages())

            # Wait 3 seconds then interrupt
            await asyncio.sleep(3)
            print("\nSending interrupt signal...")

            try:
                await client.interrupt()
                print("Interrupt sent successfully")
            except Exception as e:
                print(f"Interrupt error: {e}")

            # Give some time to see any final messages
            await asyncio.sleep(2)

            # Cancel the receive task
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass

        await receive_and_interrupt()

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.disconnect()
        print("\nDisconnected")


async def main():
    """Run all examples."""
    # Run string mode example
    await example_string_mode()

    # Run streaming mode example
    await example_streaming_mode()

    # Run context manager example
    await example_with_context_manager()

    # Run interrupt example
    await example_with_interrupt()


if __name__ == "__main__":
    asyncio.run(main())
