#!/usr/bin/env python3
"""
Comprehensive examples of using ClaudeSDKClient for streaming mode.

This file demonstrates various patterns for building applications with
the ClaudeSDKClient streaming interface.
"""

import asyncio
import contextlib

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    ResultMessage,
    TextBlock,
)


async def example_basic_streaming():
    """Basic streaming with context manager."""
    print("=== Basic Streaming Example ===")

    async with ClaudeSDKClient() as client:
        # Send a message
        await client.send_message("What is 2+2?")

        # Receive complete response using the helper method
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude: {block.text}")
            elif isinstance(msg, ResultMessage) and msg.total_cost_usd:
                print(f"Cost: ${msg.total_cost_usd:.4f}")

    print("Session ended\n")


async def example_multi_turn_conversation():
    """Multi-turn conversation using receive_response helper."""
    print("=== Multi-Turn Conversation Example ===")

    async with ClaudeSDKClient() as client:
        # First turn
        print("User: What's the capital of France?")
        await client.send_message("What's the capital of France?")

        # Extract and print response
        async for msg in client.receive_response():
            content_blocks = getattr(msg, 'content', [])
            for block in content_blocks:
                if isinstance(block, TextBlock):
                    print(f"{block.text}")

        # Second turn - follow-up
        print("\nUser: What's the population of that city?")
        await client.send_message("What's the population of that city?")

        async for msg in client.receive_response():
            content_blocks = getattr(msg, 'content', [])
            for block in content_blocks:
                if isinstance(block, TextBlock):
                    print(f"{block.text}")

    print("\nConversation ended\n")


async def example_concurrent_responses():
    """Handle responses while sending new messages."""
    print("=== Concurrent Send/Receive Example ===")

    async with ClaudeSDKClient() as client:
        # Background task to continuously receive messages
        async def receive_messages():
            async for message in client.receive_messages():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            print(f"Claude: {block.text}")

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
            await client.send_message(question)
            await asyncio.sleep(3)  # Wait between messages

        # Give time for final responses
        await asyncio.sleep(2)

        # Clean up
        receive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receive_task

    print("\nSession ended\n")


async def example_with_interrupt():
    """Demonstrate interrupt capability."""
    print("=== Interrupt Example ===")
    print("IMPORTANT: Interrupts require active message consumption.")

    async with ClaudeSDKClient() as client:
        # Start a long-running task
        print("\nUser: Count from 1 to 100 slowly")
        await client.send_message(
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

                # Stop when we get a result after interrupt
                if isinstance(message, ResultMessage) and interrupt_sent:
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
        await client.send_message("Never mind, just tell me a quick joke")

        # Get the joke
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude: {block.text}")

    print("\nSession ended\n")


async def example_manual_message_handling():
    """Manually handle message stream for custom logic."""
    print("=== Manual Message Handling Example ===")

    async with ClaudeSDKClient() as client:
        await client.send_message(
            "List 5 programming languages and their main use cases"
        )

        # Manually process messages with custom logic
        languages_found = []

        async for message in client.receive_messages():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text
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
                print(f"\nTotal languages mentioned: {len(languages_found)}")
                break

    print("\nSession ended\n")


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
        await client.send_message(
            "Create a simple hello.txt file with a greeting message"
        )

        tool_uses = []
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude: {block.text}")
                    elif hasattr(block, "name"):  # ToolUseBlock
                        tool_uses.append(getattr(block, "name", ""))

        if tool_uses:
            print(f"\nTools used: {', '.join(tool_uses)}")

    print("\nSession ended\n")


async def example_error_handling():
    """Demonstrate proper error handling."""
    print("=== Error Handling Example ===")

    client = ClaudeSDKClient()

    try:
        await client.connect()

        # Send a message that will take time to process
        await client.send_message("Run a bash sleep command for 60 seconds")

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
                        print("Received complete response")
                        break

        except asyncio.TimeoutError:
            print("\nResponse timeout after 10 seconds - demonstrating graceful handling")
            print(f"Received {len(messages)} messages before timeout")

    except CLIConnectionError as e:
        print(f"Connection error: {e}")

    except Exception as e:
        print(f"Unexpected error: {e}")

    finally:
        # Always disconnect
        await client.disconnect()

    print("\nSession ended\n")


async def main():
    """Run all examples."""
    examples = [
        example_basic_streaming,
        example_multi_turn_conversation,
        example_concurrent_responses,
        example_with_interrupt,
        example_manual_message_handling,
        example_with_options,
        example_error_handling,
    ]

    for example in examples:
        await example()
        print("-" * 50 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
