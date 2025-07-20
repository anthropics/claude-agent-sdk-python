"""Tests for message parser error handling."""

import pytest

from claude_code_sdk._errors import MessageParseError
from claude_code_sdk._internal.message_parser import parse_message
from claude_code_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)


class TestMessageParser:
    """Test message parsing with the new exception behavior."""

    def test_parse_valid_user_message(self):
        """Test parsing a valid user message."""
        data = {"type": "user", "message": {"content": [{"type": "text", "text": "Hello"}]}}
        message = parse_message(data)
        assert isinstance(message, UserMessage)

    def test_parse_valid_assistant_message(self):
        """Test parsing a valid assistant message."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello"},
                    {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "Read",
                        "input": {"file_path": "/test.txt"},
                    },
                ]
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert len(message.content) == 2
        assert isinstance(message.content[0], TextBlock)
        assert isinstance(message.content[1], ToolUseBlock)

    def test_parse_valid_system_message(self):
        """Test parsing a valid system message."""
        data = {"type": "system", "subtype": "start"}
        message = parse_message(data)
        assert isinstance(message, SystemMessage)
        assert message.subtype == "start"

    def test_parse_valid_result_message(self):
        """Test parsing a valid result message."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 500,
            "is_error": False,
            "num_turns": 2,
            "session_id": "session_123",
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.subtype == "success"

    def test_parse_invalid_data_type(self):
        """Test that non-dict data raises MessageParseError."""
        with pytest.raises(MessageParseError) as exc_info:
            parse_message("not a dict")  # type: ignore
        assert "Invalid message data type" in str(exc_info.value)
        assert "expected dict, got str" in str(exc_info.value)

    def test_parse_missing_type_field(self):
        """Test that missing 'type' field raises MessageParseError."""
        with pytest.raises(MessageParseError) as exc_info:
            parse_message({"message": {"content": []}})
        assert "Message missing 'type' field" in str(exc_info.value)

    def test_parse_unknown_message_type(self):
        """Test that unknown message type raises MessageParseError."""
        with pytest.raises(MessageParseError) as exc_info:
            parse_message({"type": "unknown_type"})
        assert "Unknown message type: unknown_type" in str(exc_info.value)

    def test_parse_user_message_missing_fields(self):
        """Test that user message with missing fields raises MessageParseError."""
        with pytest.raises(MessageParseError) as exc_info:
            parse_message({"type": "user"})
        assert "Missing required field in user message" in str(exc_info.value)

    def test_parse_assistant_message_missing_fields(self):
        """Test that assistant message with missing fields raises MessageParseError."""
        with pytest.raises(MessageParseError) as exc_info:
            parse_message({"type": "assistant"})
        assert "Missing required field in assistant message" in str(exc_info.value)

    def test_parse_system_message_missing_fields(self):
        """Test that system message with missing fields raises MessageParseError."""
        with pytest.raises(MessageParseError) as exc_info:
            parse_message({"type": "system"})
        assert "Missing required field in system message" in str(exc_info.value)

    def test_parse_result_message_missing_fields(self):
        """Test that result message with missing fields raises MessageParseError."""
        with pytest.raises(MessageParseError) as exc_info:
            parse_message({"type": "result", "subtype": "success"})
        assert "Missing required field in result message" in str(exc_info.value)

    def test_message_parse_error_contains_data(self):
        """Test that MessageParseError contains the original data."""
        data = {"type": "unknown", "some": "data"}
        with pytest.raises(MessageParseError) as exc_info:
            parse_message(data)
        assert exc_info.value.data == data
