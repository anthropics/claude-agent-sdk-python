#!/usr/bin/env python3
"""
IPython-friendly code snippets for ClaudeSDKClient streaming mode.

These examples are designed to be copy-pasted directly into IPython.
Each example is self-contained and can be run independently.

The queries are intentionally simplistic. In reality, a query can be a more
complex task that Claude SDK uses its agentic capabilities and tools (e.g. run
bash commands, edit files, search the web, fetch web content) to accomplish.
"""

# ============================================================================
# BASIC STREAMING
# ============================================================================

from claude_code_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

async with ClaudeSDKClient() as client:
    print("User: What is 2+2?")
    await client.query("What is 2+2?")

    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")


# ============================================================================
# STREAMING WITH REAL-TIME DISPLAY
# ============================================================================

import asyncio

from claude_code_sdk import AssistantMessage, ClaudeSDKClient, TextBlock

async with ClaudeSDKClient() as client:
    async def send_and_receive(prompt):
        print(f"User: {prompt}")
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude: {block.text}")

    await send_and_receive("Tell me a short joke")
    print("\n---\n")
    await send_and_receive("Now tell me a fun fact")


# ============================================================================
# PERSISTENT CLIENT FOR MULTIPLE QUESTIONS
# ============================================================================

from claude_code_sdk import AssistantMessage, ClaudeSDKClient, TextBlock

# Create client
client = ClaudeSDKClient()
await client.connect()


# Helper to get response
async def get_response():
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")


# Use it multiple times
print("User: What's 2+2?")
await client.query("What's 2+2?")
await get_response()

print("User: What's 10*10?")
await client.query("What's 10*10?")
await get_response()

# Don't forget to disconnect when done
await client.disconnect()


# ============================================================================
# WITH INTERRUPT CAPABILITY
# ============================================================================
# IMPORTANT: Interrupts require active message consumption. You must be
# consuming messages from the client for the interrupt to be processed.

from claude_code_sdk import AssistantMessage, ClaudeSDKClient, TextBlock

async with ClaudeSDKClient() as client:
    print("\n--- Sending initial message ---\n")

    # Send a long-running task
    print("User: Count from 1 to 100, run bash sleep for 1 second in between")
    await client.query("Count from 1 to 100, run bash sleep for 1 second in between")

    # Create a background task to consume messages
    messages_received = []
    interrupt_sent = False

    async def consume_messages():
        async for msg in client.receive_messages():
            messages_received.append(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude: {block.text}")

            # Check if we got a result after interrupt
            if isinstance(msg, ResultMessage) and interrupt_sent:
                break

    # Start consuming messages in the background
    consume_task = asyncio.create_task(consume_messages())

    # Wait a bit then send interrupt
    await asyncio.sleep(10)
    print("\n--- Sending interrupt ---\n")
    interrupt_sent = True
    await client.interrupt()

    # Wait for the consume task to finish
    await consume_task

    # Send a new message after interrupt
    print("\n--- After interrupt, sending new message ---\n")
    await client.query("Just say 'Hello! I was interrupted.'")

    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")


# ============================================================================
# ERROR HANDLING PATTERN
# ============================================================================

from claude_code_sdk import AssistantMessage, ClaudeSDKClient, TextBlock

try:
    async with ClaudeSDKClient() as client:
        print("User: Run a bash sleep command for 60 seconds")
        await client.query("Run a bash sleep command for 60 seconds")

        # Timeout after 20 seconds
        messages = []
        async with asyncio.timeout(20.0):
            async for msg in client.receive_response():
                messages.append(msg)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(f"Claude: {block.text}")

except asyncio.TimeoutError:
    print("Request timed out after 20 seconds")
except Exception as e:
    print(f"Error: {e}")


# ============================================================================
# SENDING ASYNC ITERABLE OF MESSAGES
# ============================================================================

from claude_code_sdk import AssistantMessage, ClaudeSDKClient, TextBlock


async def message_generator():
    """Generate multiple messages as an async iterable."""
    print("User: I have two math questions.")
    yield {
        "type": "user",
        "message": {"role": "user", "content": "I have two math questions."},
        "parent_tool_use_id": None,
        "session_id": "math-session"
    }
    print("User: What is 25 * 4?")
    yield {
        "type": "user",
        "message": {"role": "user", "content": "What is 25 * 4?"},
        "parent_tool_use_id": None,
        "session_id": "math-session"
    }
    print("User: What is 100 / 5?")
    yield {
        "type": "user",
        "message": {"role": "user", "content": "What is 100 / 5?"},
        "parent_tool_use_id": None,
        "session_id": "math-session"
    }

async with ClaudeSDKClient() as client:
    # Send async iterable instead of string
    await client.query(message_generator())

    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")


# ============================================================================
# COLLECTING ALL MESSAGES INTO A LIST
# ============================================================================

from claude_code_sdk import AssistantMessage, ClaudeSDKClient, TextBlock

async with ClaudeSDKClient() as client:
    print("User: What are the primary colors?")
    await client.query("What are the primary colors?")

    # Collect all messages into a list
    messages = [msg async for msg in client.receive_response()]

    # Process them afterwards
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")
        elif isinstance(msg, ResultMessage):
            print(f"Total messages: {len(messages)}")
