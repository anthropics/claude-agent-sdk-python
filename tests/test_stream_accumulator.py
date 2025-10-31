"""Tests for stream accumulator functionality."""

from claude_agent_sdk._internal.stream_accumulator import StreamAccumulator
from claude_agent_sdk.types import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)


class TestStreamAccumulator:
    """Test StreamAccumulator class."""

    def test_accumulate_text_deltas(self):
        """Test accumulating text deltas into TextBlock."""
        accumulator = StreamAccumulator()

        # Simulate message_start event
        event1 = {
            "session_id": "test",
            "uuid": "uuid1",
            "event": {
                "type": "message_start",
                "message": {"model": "claude-sonnet-4-5"},
            },
        }
        result = accumulator.process_stream_event(event1)
        assert result is None  # message_start doesn't emit a message yet

        # Simulate content_block_start with text
        event2 = {
            "session_id": "test",
            "uuid": "uuid2",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        }
        result = accumulator.process_stream_event(event2)
        assert isinstance(result, AssistantMessage)
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == ""

        # Simulate content_block_delta with text chunks
        event3 = {
            "session_id": "test",
            "uuid": "uuid3",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        }
        result = accumulator.process_stream_event(event3)
        assert isinstance(result, AssistantMessage)
        assert result.content[0].text == "Hello"

        event4 = {
            "session_id": "test",
            "uuid": "uuid4",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": " world"},
            },
        }
        result = accumulator.process_stream_event(event4)
        assert isinstance(result, AssistantMessage)
        assert result.content[0].text == "Hello world"

        # Simulate content_block_stop
        event5 = {
            "session_id": "test",
            "uuid": "uuid5",
            "event": {"type": "content_block_stop", "index": 0},
        }
        result = accumulator.process_stream_event(event5)
        assert isinstance(result, AssistantMessage)
        assert result.content[0].text == "Hello world"

    def test_accumulate_thinking_deltas(self):
        """Test accumulating thinking deltas into ThinkingBlock."""
        accumulator = StreamAccumulator()

        # Start message
        event1 = {
            "session_id": "test",
            "uuid": "uuid1",
            "event": {
                "type": "message_start",
                "message": {"model": "claude-sonnet-4-5"},
            },
        }
        accumulator.process_stream_event(event1)

        # Start thinking block
        event2 = {
            "session_id": "test",
            "uuid": "uuid2",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "thinking",
                    "thinking": "",
                    "signature": "sig123",
                },
            },
        }
        result = accumulator.process_stream_event(event2)
        assert isinstance(result, AssistantMessage)
        assert len(result.content) == 1
        assert isinstance(result.content[0], ThinkingBlock)
        assert result.content[0].thinking == ""
        assert result.content[0].signature == "sig123"

        # Add thinking deltas
        event3 = {
            "session_id": "test",
            "uuid": "uuid3",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Let me think"},
            },
        }
        result = accumulator.process_stream_event(event3)
        assert result.content[0].thinking == "Let me think"

        event4 = {
            "session_id": "test",
            "uuid": "uuid4",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": " about this..."},
            },
        }
        result = accumulator.process_stream_event(event4)
        assert result.content[0].thinking == "Let me think about this..."

    def test_accumulate_multiple_blocks(self):
        """Test accumulating multiple content blocks."""
        accumulator = StreamAccumulator()

        # Start message
        accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid1",
                "event": {
                    "type": "message_start",
                    "message": {"model": "claude-sonnet-4-5"},
                },
            }
        )

        # Start thinking block (index 0)
        accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid2",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "thinking",
                        "thinking": "",
                        "signature": "sig1",
                    },
                },
            }
        )

        # Add thinking delta
        accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid3",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "Thinking..."},
                },
            }
        )

        # Start text block (index 1)
        accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid4",
                "event": {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                },
            }
        )

        # Add text delta
        result = accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid5",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "Here's my answer"},
                },
            }
        )

        # Verify both blocks are present and in order
        assert isinstance(result, AssistantMessage)
        assert len(result.content) == 2
        assert isinstance(result.content[0], ThinkingBlock)
        assert result.content[0].thinking == "Thinking..."
        assert isinstance(result.content[1], TextBlock)
        assert result.content[1].text == "Here's my answer"

    def test_tool_use_block(self):
        """Test accumulating tool use blocks."""
        accumulator = StreamAccumulator()

        # Start message
        accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid1",
                "event": {
                    "type": "message_start",
                    "message": {"model": "claude-sonnet-4-5"},
                },
            }
        )

        # Start tool_use block
        result = accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid2",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "Read",
                        "input": {},
                    },
                },
            }
        )

        assert isinstance(result, AssistantMessage)
        assert len(result.content) == 1
        assert isinstance(result.content[0], ToolUseBlock)
        assert result.content[0].id == "tool_123"
        assert result.content[0].name == "Read"

    def test_multiple_sessions(self):
        """Test that different sessions are tracked independently."""
        accumulator = StreamAccumulator()

        # Session 1 - start message and add text
        accumulator.process_stream_event(
            {
                "session_id": "session1",
                "uuid": "uuid1",
                "event": {
                    "type": "message_start",
                    "message": {"model": "claude-sonnet-4-5"},
                },
            }
        )
        accumulator.process_stream_event(
            {
                "session_id": "session1",
                "uuid": "uuid2",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            }
        )
        result1 = accumulator.process_stream_event(
            {
                "session_id": "session1",
                "uuid": "uuid3",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Session 1"},
                },
            }
        )

        # Session 2 - start message and add different text
        accumulator.process_stream_event(
            {
                "session_id": "session2",
                "uuid": "uuid4",
                "event": {
                    "type": "message_start",
                    "message": {"model": "claude-opus-4"},
                },
            }
        )
        accumulator.process_stream_event(
            {
                "session_id": "session2",
                "uuid": "uuid5",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            }
        )
        result2 = accumulator.process_stream_event(
            {
                "session_id": "session2",
                "uuid": "uuid6",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Session 2"},
                },
            }
        )

        # Verify sessions are independent
        assert result1.content[0].text == "Session 1"
        assert result1.model == "claude-sonnet-4-5"
        assert result2.content[0].text == "Session 2"
        assert result2.model == "claude-opus-4"

    def test_parent_tool_use_id(self):
        """Test that parent_tool_use_id is preserved."""
        accumulator = StreamAccumulator()

        # Start message with parent_tool_use_id
        result = accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid1",
                "parent_tool_use_id": "parent_tool_123",
                "event": {
                    "type": "message_start",
                    "message": {"model": "claude-sonnet-4-5"},
                },
            }
        )

        accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid2",
                "parent_tool_use_id": "parent_tool_123",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            }
        )

        result = accumulator.process_stream_event(
            {
                "session_id": "test",
                "uuid": "uuid3",
                "parent_tool_use_id": "parent_tool_123",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Test"},
                },
            }
        )

        assert result.parent_tool_use_id == "parent_tool_123"
