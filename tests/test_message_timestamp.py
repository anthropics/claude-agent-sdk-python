"""Unit tests for timestamp field in Message models - Issue #258."""

import pytest

from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    UserMessage,
)


class TestMessageTimestampField:
    """Test cases for timestamp field in message models."""

    def test_user_message_with_timestamp(self):
        """Test that UserMessage includes timestamp when provided in data."""
        data = {
            "type": "user",
            "message": {"content": "Hello world"},
            "timestamp": "2025-10-16T10:25:00.000Z",
        }
        message = parse_message(data)

        assert isinstance(message, UserMessage)
        assert message.timestamp == "2025-10-16T10:25:00.000Z"
        assert message.content == "Hello world"

    def test_user_message_without_timestamp(self):
        """Test that UserMessage handles missing timestamp gracefully."""
        data = {
            "type": "user",
            "message": {"content": "Hello world"},
        }
        message = parse_message(data)

        assert isinstance(message, UserMessage)
        assert message.timestamp is None

    def test_assistant_message_with_timestamp(self):
        """Test that AssistantMessage includes timestamp."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hi there"}],
                "model": "claude-3-opus-20240229",
            },
            "timestamp": "2025-10-16T10:26:00.000Z",
        }
        message = parse_message(data)

        assert isinstance(message, AssistantMessage)
        assert message.timestamp == "2025-10-16T10:26:00.000Z"
        assert message.model == "claude-3-opus-20240229"
        assert len(message.content) == 1
        assert isinstance(message.content[0], TextBlock)

    def test_assistant_message_without_timestamp(self):
        """Test that AssistantMessage handles missing timestamp."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hi"}],
                "model": "claude-3-opus-20240229",
            },
        }
        message = parse_message(data)

        assert isinstance(message, AssistantMessage)
        assert message.timestamp is None

    def test_system_message_with_timestamp(self):
        """Test that SystemMessage includes timestamp."""
        data = {
            "type": "system",
            "subtype": "session_start",
            "timestamp": "2025-10-16T10:24:00.000Z",
        }
        message = parse_message(data)

        assert isinstance(message, SystemMessage)
        assert message.timestamp == "2025-10-16T10:24:00.000Z"
        assert message.subtype == "session_start"

    def test_system_message_without_timestamp(self):
        """Test that SystemMessage handles missing timestamp."""
        data = {
            "type": "system",
            "subtype": "session_start",
        }
        message = parse_message(data)

        assert isinstance(message, SystemMessage)
        assert message.timestamp is None

    def test_result_message_with_timestamp(self):
        """Test that ResultMessage includes timestamp."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1500,
            "duration_api_ms": 1200,
            "is_error": False,
            "num_turns": 3,
            "session_id": "session-123",
            "timestamp": "2025-10-16T10:27:00.000Z",
        }
        message = parse_message(data)

        assert isinstance(message, ResultMessage)
        assert message.timestamp == "2025-10-16T10:27:00.000Z"
        assert message.session_id == "session-123"
        assert message.duration_ms == 1500

    def test_result_message_without_timestamp(self):
        """Test that ResultMessage handles missing timestamp."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1500,
            "duration_api_ms": 1200,
            "is_error": False,
            "num_turns": 3,
            "session_id": "session-123",
        }
        message = parse_message(data)

        assert isinstance(message, ResultMessage)
        assert message.timestamp is None

    def test_timestamp_format_variations(self):
        """Test that various timestamp formats are accepted."""
        timestamps = [
            "2025-10-16T10:25:00.000Z",
            "2025-10-16T10:25:00Z",
            "2025-10-16T10:25:00.123456Z",
            "2025-10-16 10:25:00",
        ]

        for ts in timestamps:
            data = {
                "type": "user",
                "message": {"content": "Test"},
                "timestamp": ts,
            }
            message = parse_message(data)
            assert message.timestamp == ts

    def test_user_message_with_blocks_and_timestamp(self):
        """Test UserMessage with content blocks and timestamp."""
        data = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello"},
                    {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "TestTool",
                        "input": {"arg": "value"},
                    },
                ]
            },
            "timestamp": "2025-10-16T10:28:00.000Z",
        }
        message = parse_message(data)

        assert isinstance(message, UserMessage)
        assert message.timestamp == "2025-10-16T10:28:00.000Z"
        assert len(message.content) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
