#!/usr/bin/env python3
"""
Comprehensive examples of using ClaudeSDKClient for streaming mode.

This file demonstrates various patterns for building applications with
the ClaudeSDKClient streaming interface.

The queries are intentionally simplistic. In reality, a query can be a more
complex task that Claude SDK uses its agentic capabilities and tools (e.g. run
bash commands, edit files, search the web, fetch web content) to accomplish.

Usage:
./examples/streaming_mode.py - List the examples
./examples/streaming_mode.py all - Run all examples
./examples/streaming_mode.py basic_streaming - Run a specific example
"""

import asyncio
import contextlib
import sys

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    UserMessage,
)


def display_message(msg):
    """Standardized message display function.

    - UserMessage: "User: <content>"
    - AssistantMessage: "Claude: <content>"
    - SystemMessage: ignored
    - ResultMessage: "Result ended" + cost if available
    """
    if isinstance(msg, UserMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"User: {block.text}")
    elif isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
    elif isinstance(msg, SystemMessage):
        # Ignore system messages
        pass
    elif isinstance(msg, ResultMessage):
        print("Result ended")


async def example_basic_streaming():
    """Basic streaming with context manager."""
    print("=== Basic Streaming Example ===")

    async with ClaudeSDKClient() as client:
        print("User: What is 2+2?")
        await client.query("What is 2+2?")

        # Receive complete response using the helper method
        async for msg in client.receive_response():
            display_message(msg)

    print("\n")


async def example_multi_turn_conversation():
    """Multi-turn conversation using receive_response helper."""
    print("=== Multi-Turn Conversation Example ===")

    async with ClaudeSDKClient() as client:
        # First turn
        print("User: What's the capital of France?")
        await client.query("What's the capital of France?")

        # Extract and print response
        async for msg in client.receive_response():
            display_message(msg)

        # Second turn - follow-up
        print("\nUser: What's the population of that city?")
        await client.query("What's the population of that city?")

        async for msg in client.receive_response():
            display_message(msg)

    print("\n")


async def example_concurrent_responses():
    """Handle responses while sending new messages."""
    print("=== Concurrent Send/Receive Example ===")

    async with ClaudeSDKClient() as client:
        # Background task to continuously receive messages
        async def receive_messages():
            async for message in client.receive_messages():
                display_message(message)

        # Start receiving in background
        receive_task = asyncio.create_task(receive_messages())

        # Send multiple messages with delays
        questions = [
            "What is 2 + 2?",
            "What is the square root of 144?",
            "What is 10% of 80?",
        ]

        for question in questions:
            print(f"\nUser: {question}")
            await client.query(question)
            await asyncio.sleep(3)  # Wait between messages

        # Give time for final responses
        await asyncio.sleep(2)

        # Clean up
        receive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receive_task

    print("\n")


async def example_with_interrupt():
    """Demonstrate interrupt capability."""
    print("=== Interrupt Example ===")
    print("IMPORTANT: Interrupts require active message consumption.")

    async with ClaudeSDKClient() as client:
        # Start a long-running task
        print("\nUser: Count from 1 to 100 slowly")
        await client.query(
            "Count from 1 to 100 slowly, with a brief pause between each number"
        )

        # Create a background task to consume messages
        messages_received = []
        interrupt_sent = False

        async def consume_messages():
            """Consume messages in the background to enable interrupt processing."""
            async for message in client.receive_messages():
                messages_received.append(message)
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            # Print first few numbers
                            print(f"Claude: {block.text[:50]}...")
                elif isinstance(message, ResultMessage):
                    display_message(message)
                    if interrupt_sent:
                        break

        # Start consuming messages in the background
        consume_task = asyncio.create_task(consume_messages())

        # Wait 2 seconds then send interrupt
        await asyncio.sleep(2)
        print("\n[After 2 seconds, sending interrupt...]")
        interrupt_sent = True
        await client.interrupt()

        # Wait for the consume task to finish processing the interrupt
        await consume_task

        # Send new instruction after interrupt
        print("\nUser: Never mind, just tell me a quick joke")
        await client.query("Never mind, just tell me a quick joke")

        # Get the joke
        async for msg in client.receive_response():
            display_message(msg)

    print("\n")


async def example_manual_message_handling():
    """Manually handle message stream for custom logic."""
    print("=== Manual Message Handling Example ===")

    async with ClaudeSDKClient() as client:
        await client.query(
            "List 5 programming languages and their main use cases"
        )

        # Manually process messages with custom logic
        languages_found = []

        async for message in client.receive_messages():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text
                        print(f"Claude: {text}")
                        # Custom logic: extract language names
                        for lang in [
                            "Python",
                            "JavaScript",
                            "Java",
                            "C++",
                            "Go",
                            "Rust",
                            "Ruby",
                        ]:
                            if lang in text and lang not in languages_found:
                                languages_found.append(lang)
                                print(f"Found language: {lang}")
            elif isinstance(message, ResultMessage):
                display_message(message)
                print(f"Total languages mentioned: {len(languages_found)}")
                break

    print("\n")


async def example_with_options():
    """Use ClaudeCodeOptions to configure the client."""
    print("=== Custom Options Example ===")

    # Configure options
    options = ClaudeCodeOptions(
        allowed_tools=["Read", "Write"],  # Allow file operations
        max_thinking_tokens=10000,
        system_prompt="You are a helpful coding assistant.",
    )

    async with ClaudeSDKClient(options=options) as client:
        print("User: Create a simple hello.txt file with a greeting message")
        await client.query(
            "Create a simple hello.txt file with a greeting message"
        )

        tool_uses = []
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                display_message(msg)
                for block in msg.content:
                    if hasattr(block, "name") and not isinstance(
                        block, TextBlock
                    ):  # ToolUseBlock
                        tool_uses.append(getattr(block, "name", ""))
            else:
                display_message(msg)

        if tool_uses:
            print(f"Tools used: {', '.join(tool_uses)}")

    print("\n")


async def example_async_iterable_prompt():
    """Demonstrate send_message with async iterable."""
    print("=== Async Iterable Prompt Example ===")

    async def create_message_stream():
        """Generate a stream of messages."""
        print("User: Hello! I have multiple questions.")
        yield {
            "type": "user",
            "message": {"role": "user", "content": "Hello! I have multiple questions."},
            "parent_tool_use_id": None,
            "session_id": "qa-session",
        }

        print("User: First, what's the capital of Japan?")
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": "First, what's the capital of Japan?",
            },
            "parent_tool_use_id": None,
            "session_id": "qa-session",
        }

        print("User: Second, what's 15% of 200?")
        yield {
            "type": "user",
            "message": {"role": "user", "content": "Second, what's 15% of 200?"},
            "parent_tool_use_id": None,
            "session_id": "qa-session",
        }

    async with ClaudeSDKClient() as client:
        # Send async iterable of messages
        await client.query(create_message_stream())

        # Receive the three responses
        async for msg in client.receive_response():
            display_message(msg)
        async for msg in client.receive_response():
            display_message(msg)
        async for msg in client.receive_response():
            display_message(msg)

    print("\n")


async def example_error_handling():
    """Demonstrate proper error handling."""
    print("=== Error Handling Example ===")

    client = ClaudeSDKClient()

    try:
        await client.connect()

        # Send a message that will take time to process
        print("User: Run a bash sleep command for 60 seconds")
        await client.query("Run a bash sleep command for 60 seconds")

        # Try to receive response with a short timeout
        try:
            messages = []
            async with asyncio.timeout(10.0):
                async for msg in client.receive_response():
                    messages.append(msg)
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                print(f"Claude: {block.text[:50]}...")
                    elif isinstance(msg, ResultMessage):
                        display_message(msg)
                        break

        except asyncio.TimeoutError:
            print(
                "\nResponse timeout after 10 seconds - demonstrating graceful handling"
            )
            print(f"Received {len(messages)} messages before timeout")

    except CLIConnectionError as e:
        print(f"Connection error: {e}")

    except Exception as e:
        print(f"Unexpected error: {e}")

    finally:
        # Always disconnect
        await client.disconnect()

    print("\n")


async def main():
    """Run all examples or a specific example based on command line argument."""
    examples = {
        "basic_streaming": example_basic_streaming,
        "multi_turn_conversation": example_multi_turn_conversation,
        "concurrent_responses": example_concurrent_responses,
        "with_interrupt": example_with_interrupt,
        "manual_message_handling": example_manual_message_handling,
        "with_options": example_with_options,
        "async_iterable_prompt": example_async_iterable_prompt,
        "error_handling": example_error_handling,
    }

    if len(sys.argv) < 2:
        # List available examples
        print("Usage: python streaming_mode.py <example_name>")
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
    asyncio.run(main())
