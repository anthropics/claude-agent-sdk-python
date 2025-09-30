"""End-to-end tests for include_partial_messages option with real Claude API calls.

These tests verify that the SDK properly handles partial message streaming,
including StreamEvent parsing and message interleaving.
"""

import asyncio
from typing import List, Any

import pytest

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    StreamEvent,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    ThinkingBlock,
    TextBlock,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_include_partial_messages_stream_events():
    """Test that include_partial_messages produces StreamEvent messages."""

    options = ClaudeAgentOptions(
        include_partial_messages=True,
        model="claude-sonnet-4-5",
        max_turns=2,
        env={
            "MAX_THINKING_TOKENS": "8000",
        },
    )

    collected_messages: List[Any] = []

    async with ClaudeSDKClient(options) as client:
        # Send a simple prompt that will generate streaming response with thinking
        await client.query("Think of three jokes, then tell one")

        async for message in client.receive_response():
            collected_messages.append(message)

    # Verify we got the expected message types
    message_types = [type(msg).__name__ for msg in collected_messages]

    # Should have SystemMessage(init) at the start
    assert message_types[0] == "SystemMessage"
    assert isinstance(collected_messages[0], SystemMessage)
    assert collected_messages[0].subtype == "init"

    # Should have multiple StreamEvent messages
    stream_events = [msg for msg in collected_messages if isinstance(msg, StreamEvent)]
    assert len(stream_events) > 0, "No StreamEvent messages received"

    # Check for expected StreamEvent types
    event_types = [event.event.get("type") for event in stream_events]
    assert "message_start" in event_types, "No message_start StreamEvent"
    assert "content_block_start" in event_types, "No content_block_start StreamEvent"
    assert "content_block_delta" in event_types, "No content_block_delta StreamEvent"
    assert "content_block_stop" in event_types, "No content_block_stop StreamEvent"
    assert "message_stop" in event_types, "No message_stop StreamEvent"

    # Should have AssistantMessage messages with thinking and text
    assistant_messages = [msg for msg in collected_messages if isinstance(msg, AssistantMessage)]
    assert len(assistant_messages) >= 1, "No AssistantMessage received"

    # Check for thinking block in at least one AssistantMessage
    has_thinking = any(
        any(isinstance(block, ThinkingBlock) for block in msg.content)
        for msg in assistant_messages
    )
    assert has_thinking, "No ThinkingBlock found in AssistantMessages"

    # Check for text block (the joke) in at least one AssistantMessage
    has_text = any(
        any(isinstance(block, TextBlock) for block in msg.content)
        for msg in assistant_messages
    )
    assert has_text, "No TextBlock found in AssistantMessages"

    # Should end with ResultMessage
    assert isinstance(collected_messages[-1], ResultMessage)
    assert collected_messages[-1].subtype == "success"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_include_partial_messages_thinking_deltas():
    """Test that thinking content is streamed incrementally via deltas."""

    options = ClaudeAgentOptions(
        include_partial_messages=True,
        model="claude-sonnet-4-5",
        max_turns=2,
        env={
            "MAX_THINKING_TOKENS": "8000",
        },
    )

    thinking_deltas = []

    async with ClaudeSDKClient(options) as client:
        await client.query("Think step by step about what 2 + 2 equals")

        async for message in client.receive_response():
            if isinstance(message, StreamEvent):
                event = message.event
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "thinking_delta":
                        thinking_deltas.append(delta.get("thinking", ""))

    # Should have received multiple thinking deltas
    assert len(thinking_deltas) > 0, "No thinking deltas received"

    # Combined thinking should form coherent text
    combined_thinking = "".join(thinking_deltas)
    assert len(combined_thinking) > 10, "Thinking content too short"

    # Should contain some reasoning about the calculation
    assert "2" in combined_thinking.lower(), "Thinking doesn't mention the numbers"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_partial_messages_disabled_by_default():
    """Test that partial messages are not included when option is not set."""

    options = ClaudeAgentOptions(
        # include_partial_messages not set (defaults to False)
        model="claude-sonnet-4-5",
        max_turns=2,
    )

    collected_messages: List[Any] = []

    async with ClaudeSDKClient(options) as client:
        await client.query("Say hello")

        async for message in client.receive_response():
            collected_messages.append(message)

    # Should NOT have any StreamEvent messages
    stream_events = [msg for msg in collected_messages if isinstance(msg, StreamEvent)]
    assert len(stream_events) == 0, "StreamEvent messages present when partial messages disabled"

    # Should still have the regular messages
    assert any(isinstance(msg, SystemMessage) for msg in collected_messages)
    assert any(isinstance(msg, AssistantMessage) for msg in collected_messages)
    assert any(isinstance(msg, ResultMessage) for msg in collected_messages)