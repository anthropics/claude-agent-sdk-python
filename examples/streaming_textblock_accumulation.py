#!/usr/bin/env python3
"""
Example demonstrating automatic TextBlock accumulation from streaming responses.

This example shows how to use the `accumulate_streaming_content` option to
automatically receive TextBlock and ThinkingBlock objects that update as the
LLM streams content, rather than having to manually process raw stream events.

The accumulate_streaming_content option works with include_partial_messages to:
1. Receive raw StreamEvent objects (if you need them)
2. Automatically receive updated AssistantMessage objects with accumulated content
3. Get TextBlock and ThinkingBlock objects that grow as deltas arrive

This makes it much easier to build real-time UIs showing streaming text.
"""

import asyncio
from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    StreamEvent,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
)


async def example_basic_accumulation():
    """Basic example showing TextBlock accumulation."""
    print("=== Basic TextBlock Accumulation Example ===\n")

    options = ClaudeAgentOptions(
        include_partial_messages=True,  # Enable streaming
        accumulate_streaming_content=True,  # Enable automatic accumulation
        model="claude-sonnet-4-5",
        max_turns=2,
        env={
            "MAX_THINKING_TOKENS": "8000",
        },
    )

    async with ClaudeSDKClient(options) as client:
        print("User: Tell me a short joke\n")
        await client.query("Tell me a short joke")

        # Track the accumulated text
        last_text = ""

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                # Get accumulated text from TextBlock
                for block in message.content:
                    if isinstance(block, TextBlock):
                        # Only print new text (delta)
                        new_text = block.text[len(last_text):]
                        if new_text:
                            print(new_text, end="", flush=True)
                            last_text = block.text

            elif isinstance(message, ResultMessage):
                print(f"\n\n[Completed in {message.duration_ms}ms]")
                if message.total_cost_usd:
                    print(f"[Cost: ${message.total_cost_usd:.6f}]")

    print("\n")


async def example_with_thinking():
    """Example showing both ThinkingBlock and TextBlock accumulation."""
    print("=== Thinking + Text Accumulation Example ===\n")

    options = ClaudeAgentOptions(
        include_partial_messages=True,
        accumulate_streaming_content=True,
        model="claude-sonnet-4-5",
        max_turns=2,
        env={
            "MAX_THINKING_TOKENS": "8000",
        },
    )

    async with ClaudeSDKClient(options) as client:
        print("User: Think step by step: what is 15 * 23?\n")
        await client.query("Think step by step: what is 15 * 23?")

        last_thinking = ""
        last_text = ""
        thinking_shown = False

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ThinkingBlock):
                        # Show thinking as it accumulates
                        if not thinking_shown:
                            print("🤔 Thinking:", end=" ", flush=True)
                            thinking_shown = True

                        new_thinking = block.thinking[len(last_thinking):]
                        if new_thinking:
                            print(new_thinking, end="", flush=True)
                            last_thinking = block.thinking

                    elif isinstance(block, TextBlock):
                        # Show final answer
                        if thinking_shown and not last_text:
                            print("\n\n💬 Answer:", end=" ", flush=True)

                        new_text = block.text[len(last_text):]
                        if new_text:
                            print(new_text, end="", flush=True)
                            last_text = block.text

            elif isinstance(message, ResultMessage):
                print(f"\n\n[Completed]")

    print("\n")


async def example_compare_with_without():
    """Compare behavior with and without accumulation."""
    print("=== Comparison: With vs Without Accumulation ===\n")

    # First, without accumulation
    print("1. WITHOUT accumulation (raw StreamEvents):\n")

    options_raw = ClaudeAgentOptions(
        include_partial_messages=True,
        accumulate_streaming_content=False,  # Disabled
        model="claude-sonnet-4-5",
        max_turns=2,
    )

    async with ClaudeSDKClient(options_raw) as client:
        await client.query("Say 'Hello'")

        stream_event_count = 0
        assistant_msg_count = 0

        async for message in client.receive_response():
            if isinstance(message, StreamEvent):
                stream_event_count += 1
                # Would need to manually extract text deltas
                event = message.event
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        print(f"  [StreamEvent] text_delta: {repr(text)}")

            elif isinstance(message, AssistantMessage):
                assistant_msg_count += 1
                # Only complete message at end
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"  [AssistantMessage] Complete text: {repr(block.text)}")

        print(f"\n  Stats: {stream_event_count} StreamEvents, {assistant_msg_count} AssistantMessage\n")

    # Now with accumulation
    print("2. WITH accumulation (accumulated TextBlocks):\n")

    options_accumulated = ClaudeAgentOptions(
        include_partial_messages=True,
        accumulate_streaming_content=True,  # Enabled
        model="claude-sonnet-4-5",
        max_turns=2,
    )

    async with ClaudeSDKClient(options_accumulated) as client:
        await client.query("Say 'Hello'")

        stream_event_count = 0
        assistant_msg_count = 0

        async for message in client.receive_response():
            if isinstance(message, StreamEvent):
                stream_event_count += 1
                # Still get raw events if needed

            elif isinstance(message, AssistantMessage):
                assistant_msg_count += 1
                # Get accumulated message at each delta
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"  [AssistantMessage] Accumulated text: {repr(block.text)}")

        print(f"\n  Stats: {stream_event_count} StreamEvents, {assistant_msg_count} AssistantMessages")
        print("  (Multiple AssistantMessages with growing TextBlock content)\n")

    print("\n")


async def example_real_time_ui():
    """Example simulating a real-time UI update pattern."""
    print("=== Real-Time UI Pattern ===\n")

    options = ClaudeAgentOptions(
        include_partial_messages=True,
        accumulate_streaming_content=True,
        model="claude-sonnet-4-5",
        max_turns=2,
    )

    async with ClaudeSDKClient(options) as client:
        print("User: Write a haiku about coding\n")
        print("Claude: ", end="", flush=True)

        await client.query("Write a haiku about coding")

        last_content = ""

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                # In a real UI, you would update the displayed text here
                # This pattern is much simpler than manual delta tracking
                for block in message.content:
                    if isinstance(block, TextBlock):
                        current_content = block.text

                        # Only print new characters (simulate UI update)
                        if current_content != last_content:
                            new_chars = current_content[len(last_content):]
                            print(new_chars, end="", flush=True)
                            last_content = current_content

            elif isinstance(message, ResultMessage):
                print("\n\n[Done]")

    print("\n")


async def main():
    """Run all examples."""
    examples = [
        ("Basic Accumulation", example_basic_accumulation),
        ("Thinking + Text", example_with_thinking),
        ("Comparison", example_compare_with_without),
        ("Real-Time UI", example_real_time_ui),
    ]

    import sys

    if len(sys.argv) > 1:
        # Run specific example
        example_name = sys.argv[1]
        for name, func in examples:
            if name.lower().replace(" ", "_") == example_name.lower():
                await func()
                return
        print(f"Unknown example: {example_name}")
        print("\nAvailable examples:")
        for name, _ in examples:
            print(f"  - {name.lower().replace(' ', '_')}")
    else:
        # Run all examples
        for name, func in examples:
            print(f"\n{'=' * 60}")
            print(f"Running: {name}")
            print(f"{'=' * 60}\n")
            await func()
            await asyncio.sleep(1)  # Brief pause between examples


if __name__ == "__main__":
    print("TextBlock Accumulation Examples")
    print("=" * 60)
    print(
        "\nThese examples demonstrate the accumulate_streaming_content option,"
    )
    print("which automatically builds TextBlock/ThinkingBlock objects from")
    print("streaming deltas, making it easy to build real-time UIs.\n")

    asyncio.run(main())
