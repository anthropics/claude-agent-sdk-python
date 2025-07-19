#!/usr/bin/env python3
"""
IPython-friendly code snippets for ClaudeSDKClient streaming mode.

These examples are designed to be copy-pasted directly into IPython.
Each example is self-contained and can be run independently.
"""

# ============================================================================
# BASIC STREAMING
# ============================================================================

from claude_code_sdk import ClaudeSDKClient, AssistantMessage, TextBlock

async with ClaudeSDKClient() as client:
    await client.send_message("What is 2+2?")
    messages, result = await client.receive_response()

    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")


# ============================================================================
# STREAMING WITH REAL-TIME DISPLAY
# ============================================================================

import asyncio
from claude_code_sdk import ClaudeSDKClient, AssistantMessage, TextBlock

async with ClaudeSDKClient() as client:
    async def receive_response():
        messages, _ = await client.receive_response()
        for msg in messages:
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"Claude: {block.text}")

    await client.send_message("Tell me a short joke")
    await receive_response()
    await client.send_message("Now tell me a fun fact")
    await receive_response()


# ============================================================================
# PERSISTENT CLIENT FOR MULTIPLE QUESTIONS
# ============================================================================

from claude_code_sdk import ClaudeSDKClient, AssistantMessage, TextBlock

# Create client
client = ClaudeSDKClient()
await client.connect()


# Helper to get response
async def get_response():
    messages, result = await client.receive_response()
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")


# Use it multiple times
await client.send_message("What's 2+2?")
await get_response()

await client.send_message("What's 10*10?")
await get_response()

# Don't forget to disconnect when done
await client.disconnect()


# ============================================================================
# WITH INTERRUPT CAPABILITY
# ============================================================================
# IMPORTANT: Interrupts require active message consumption. You must be
# consuming messages from the client for the interrupt to be processed.

import asyncio
from claude_code_sdk import ClaudeSDKClient, AssistantMessage, TextBlock, ResultMessage

async with ClaudeSDKClient() as client:
    print("\n--- Sending initial message ---\n")

    # Send a long-running task
    await client.send_message("Count from 1 to 100 slowly using bash sleep")

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
    await client.send_message("Just say 'Hello! I was interrupted.'")
    messages, result = await client.receive_response()

    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")


# ============================================================================
# ERROR HANDLING PATTERN
# ============================================================================

from claude_code_sdk import ClaudeSDKClient, AssistantMessage, TextBlock

try:
    async with ClaudeSDKClient() as client:
        await client.send_message("Run a bash sleep command for 60 seconds")

        # Timeout after 30 seconds
        messages, result = await asyncio.wait_for(
            client.receive_response(), timeout=20.0
        )

except asyncio.TimeoutError:
    print("Request timed out")
except Exception as e:
    print(f"Error: {e}")