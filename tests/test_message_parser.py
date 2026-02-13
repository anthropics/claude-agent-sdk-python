"""Tests for message parser error handling."""

import pytest

from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


class TestMessageParser:
    """Test message parsing with the new exception behavior."""

    def test_parse_valid_user_message(self):
        """Test parsing a valid user message."""
        data = {
            "type": "user",
            "message": {"content": [{"type": "text", "text": "Hello"}]},
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert len(message.content) == 1
        assert isinstance(message.content[0], TextBlock)
        assert message.content[0].text == "Hello"

    def test_parse_user_message_with_uuid(self):
        """Test parsing a user message with uuid field (issue #414).

        The uuid field is needed for file checkpointing with rewind_files().
        """
        data = {
            "type": "user",
            "uuid": "msg-abc123-def456",
            "message": {"content": [{"type": "text", "text": "Hello"}]},
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert message.uuid == "msg-abc123-def456"
        assert len(message.content) == 1

    def test_parse_user_message_with_tool_use(self):
        """Test parsing a user message with tool_use block."""
        data = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me read this file"},
                    {
                        "type": "tool_use",
                        "id": "tool_456",
                        "name": "Read",
                        "input": {"file_path": "/example.txt"},
                    },
                ]
            },
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert len(message.content) == 2
        assert isinstance(message.content[0], TextBlock)
        assert isinstance(message.content[1], ToolUseBlock)
        assert message.content[1].id == "tool_456"
        assert message.content[1].name == "Read"
        assert message.content[1].input == {"file_path": "/example.txt"}

    def test_parse_user_message_with_tool_result(self):
        """Test parsing a user message with tool_result block."""
        data = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_789",
                        "content": "File contents here",
                    }
                ]
            },
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert len(message.content) == 1
        assert isinstance(message.content[0], ToolResultBlock)
        assert message.content[0].tool_use_id == "tool_789"
        assert message.content[0].content == "File contents here"

    def test_parse_user_message_with_tool_result_error(self):
        """Test parsing a user message with error tool_result block."""
        data = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_error",
                        "content": "File not found",
                        "is_error": True,
                    }
                ]
            },
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert len(message.content) == 1
        assert isinstance(message.content[0], ToolResultBlock)
        assert message.content[0].tool_use_id == "tool_error"
        assert message.content[0].content == "File not found"
        assert message.content[0].is_error is True

    def test_parse_user_message_with_mixed_content(self):
        """Test parsing a user message with mixed content blocks."""
        data = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "Here's what I found:"},
                    {
                        "type": "tool_use",
                        "id": "use_1",
                        "name": "Search",
                        "input": {"query": "test"},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "use_1",
                        "content": "Search results",
                    },
                    {"type": "text", "text": "What do you think?"},
                ]
            },
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert len(message.content) == 4
        assert isinstance(message.content[0], TextBlock)
        assert isinstance(message.content[1], ToolUseBlock)
        assert isinstance(message.content[2], ToolResultBlock)
        assert isinstance(message.content[3], TextBlock)

    def test_parse_user_message_inside_subagent(self):
        """Test parsing a valid user message."""
        data = {
            "type": "user",
            "message": {"content": [{"type": "text", "text": "Hello"}]},
            "parent_tool_use_id": "toolu_01Xrwd5Y13sEHtzScxR77So8",
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert message.parent_tool_use_id == "toolu_01Xrwd5Y13sEHtzScxR77So8"

    def test_parse_user_message_with_tool_use_result(self):
        """Test parsing a user message with tool_use_result field.

        The tool_use_result field contains metadata about tool execution results,
        including file edit details like oldString, newString, and structuredPatch.
        """
        tool_result_data = {
            "filePath": "/path/to/file.py",
            "oldString": "old code",
            "newString": "new code",
            "originalFile": "full file contents",
            "structuredPatch": [
                {
                    "oldStart": 33,
                    "oldLines": 7,
                    "newStart": 33,
                    "newLines": 7,
                    "lines": [
                        "   # comment",
                        "-      old line",
                        "+      new line",
                    ],
                }
            ],
            "userModified": False,
            "replaceAll": False,
        }
        data = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "toolu_vrtx_01KXWexk3NJdwkjWzPMGQ2F1",
                        "type": "tool_result",
                        "content": "The file has been updated.",
                    }
                ],
            },
            "parent_tool_use_id": None,
            "session_id": "84afb479-17ae-49af-8f2b-666ac2530c3a",
            "uuid": "2ace3375-1879-48a0-a421-6bce25a9295a",
            "tool_use_result": tool_result_data,
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert message.tool_use_result == tool_result_data
        assert message.tool_use_result["filePath"] == "/path/to/file.py"
        assert message.tool_use_result["oldString"] == "old code"
        assert message.tool_use_result["newString"] == "new code"
        assert message.tool_use_result["structuredPatch"][0]["oldStart"] == 33
        assert message.uuid == "2ace3375-1879-48a0-a421-6bce25a9295a"

    def test_parse_user_message_with_string_content_and_tool_use_result(self):
        """Test parsing a user message with string content and tool_use_result."""
        tool_result_data = {"filePath": "/path/to/file.py", "userModified": True}
        data = {
            "type": "user",
            "message": {"content": "Simple string content"},
            "tool_use_result": tool_result_data,
        }
        message = parse_message(data)
        assert isinstance(message, UserMessage)
        assert message.content == "Simple string content"
        assert message.tool_use_result == tool_result_data

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
                ],
                "model": "claude-opus-4-1-20250805",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert len(message.content) == 2
        assert isinstance(message.content[0], TextBlock)
        assert isinstance(message.content[1], ToolUseBlock)

    def test_parse_assistant_message_with_thinking(self):
        """Test parsing an assistant message with thinking block."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "I'm thinking about the answer...",
                        "signature": "sig-123",
                    },
                    {"type": "text", "text": "Here's my response"},
                ],
                "model": "claude-opus-4-1-20250805",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert len(message.content) == 2
        assert isinstance(message.content[0], ThinkingBlock)
        assert message.content[0].thinking == "I'm thinking about the answer..."
        assert message.content[0].signature == "sig-123"
        assert isinstance(message.content[1], TextBlock)
        assert message.content[1].text == "Here's my response"

    def test_parse_valid_system_message(self):
        """Test parsing a valid system message."""
        data = {"type": "system", "subtype": "start"}
        message = parse_message(data)
        assert isinstance(message, SystemMessage)
        assert message.subtype == "start"

    def test_parse_assistant_message_inside_subagent(self):
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
                ],
                "model": "claude-opus-4-1-20250805",
            },
            "parent_tool_use_id": "toolu_01Xrwd5Y13sEHtzScxR77So8",
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.parent_tool_use_id == "toolu_01Xrwd5Y13sEHtzScxR77So8"

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

    def test_parse_assistant_message_without_error(self):
        """Test that assistant message without error has error=None."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello"}],
                "model": "claude-opus-4-5-20251101",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.error is None

    def test_parse_assistant_message_with_authentication_error(self):
        """Test parsing assistant message with authentication_failed error.

        The error field is at the top level of the data, not inside message.
        This matches the actual CLI output format.
        """
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Invalid API key · Fix external API key"}
                ],
                "model": "<synthetic>",
            },
            "session_id": "test-session",
            "error": "authentication_failed",
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.error == "authentication_failed"
        assert len(message.content) == 1
        assert isinstance(message.content[0], TextBlock)

    def test_parse_assistant_message_with_unknown_error(self):
        """Test parsing assistant message with unknown error (e.g., 404, 500).

        When the CLI encounters API errors like model not found or server errors,
        it sets error to 'unknown' and includes the error details in the text content.
        """
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": 'API Error: 500 {"type":"error","error":{"type":"api_error","message":"Internal server error"}}',
                    }
                ],
                "model": "<synthetic>",
            },
            "session_id": "test-session",
            "error": "unknown",
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.error == "unknown"

    def test_parse_assistant_message_with_rate_limit_error(self):
        """Test parsing assistant message with rate_limit error."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Rate limit exceeded"}],
                "model": "<synthetic>",
            },
            "error": "rate_limit",
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.error == "rate_limit"

    def test_parse_assistant_message_preserves_all_fields(self):
        """Test that AssistantMessage preserves id, usage, stop_reason, session_id, uuid.

        These fields are present in the raw CLI JSON output but were previously
        dropped during parsing. See issue #562.
        """
        data = {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-5-20250929",
                "id": "msg_01HRq7YZE3apPqSHydvG77Ve",
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Hi! I'm ready to help.",
                    }
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 3,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 20012,
                    "output_tokens": 1,
                    "service_tier": "standard",
                },
            },
            "parent_tool_use_id": None,
            "session_id": "fdf2d90a-fd9e-4736-ae35-806edd13643f",
            "uuid": "0dbd2453-1209-4fe9-bd51-4102f64e33df",
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.model == "claude-sonnet-4-5-20250929"
        assert message.id == "msg_01HRq7YZE3apPqSHydvG77Ve"
        assert message.usage is not None
        assert message.usage["input_tokens"] == 3
        assert message.usage["cache_read_input_tokens"] == 20012
        assert message.usage["output_tokens"] == 1
        assert message.stop_reason == "end_turn"
        assert message.stop_sequence is None
        assert message.session_id == "fdf2d90a-fd9e-4736-ae35-806edd13643f"
        assert message.uuid == "0dbd2453-1209-4fe9-bd51-4102f64e33df"

    def test_parse_assistant_message_optional_fields_default_to_none(self):
        """Test that new optional fields default to None when not present."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello"}],
                "model": "claude-opus-4-1-20250805",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.id is None
        assert message.usage is None
        assert message.stop_reason is None
        assert message.stop_sequence is None
        assert message.session_id is None
        assert message.uuid is None

    def test_parse_result_message_preserves_all_fields(self):
        """Test that ResultMessage preserves model_usage, stop_reason, permission_denials, uuid.

        These fields are present in the raw CLI JSON output but were previously
        dropped during parsing. See issue #562.
        """
        data = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 2995,
            "duration_api_ms": 2190,
            "num_turns": 1,
            "result": "Hi! I'm ready to help.",
            "stop_reason": None,
            "session_id": "fdf2d90a-fd9e-4736-ae35-806edd13643f",
            "total_cost_usd": 0.010620999999999998,
            "usage": {
                "input_tokens": 3,
                "output_tokens": 24,
            },
            "modelUsage": {
                "claude-sonnet-4-5-20250929": {
                    "inputTokens": 3,
                    "outputTokens": 24,
                    "cacheReadInputTokens": 20012,
                    "cacheCreationInputTokens": 0,
                    "costUSD": 0.010620999999999998,
                    "contextWindow": 200000,
                    "maxOutputTokens": 64000,
                }
            },
            "permission_denials": [],
            "uuid": "d379c496-f33a-4ea4-b920-3c5483baa6f7",
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.subtype == "success"
        assert message.total_cost_usd == 0.010620999999999998
        # New fields
        assert message.model_usage is not None
        assert "claude-sonnet-4-5-20250929" in message.model_usage
        model_data = message.model_usage["claude-sonnet-4-5-20250929"]
        assert model_data["inputTokens"] == 3
        assert model_data["outputTokens"] == 24
        assert model_data["cacheReadInputTokens"] == 20012
        assert model_data["costUSD"] == 0.010620999999999998
        assert message.stop_reason is None
        assert message.permission_denials == []
        assert message.uuid == "d379c496-f33a-4ea4-b920-3c5483baa6f7"

    def test_parse_result_message_optional_fields_default_to_none(self):
        """Test that new optional fields default to None when not present."""
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
        assert message.model_usage is None
        assert message.stop_reason is None
        assert message.permission_denials is None
        assert message.uuid is None

    def test_parse_result_message_model_usage_multiple_models(self):
        """Test ResultMessage with modelUsage containing multiple models.

        When subagents use different models, modelUsage has multiple entries.
        """
        data = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 16025,
            "duration_api_ms": 24144,
            "num_turns": 9,
            "result": "Done.",
            "session_id": "session_456",
            "total_cost_usd": 0.25,
            "modelUsage": {
                "claude-sonnet-4-5-20250929": {
                    "inputTokens": 100,
                    "outputTokens": 200,
                    "costUSD": 0.15,
                },
                "claude-haiku-4-5-20251001": {
                    "inputTokens": 50,
                    "outputTokens": 100,
                    "costUSD": 0.10,
                },
            },
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.model_usage is not None
        assert len(message.model_usage) == 2
        assert "claude-sonnet-4-5-20250929" in message.model_usage
        assert "claude-haiku-4-5-20251001" in message.model_usage
        assert message.model_usage["claude-sonnet-4-5-20250929"]["costUSD"] == 0.15
        assert message.model_usage["claude-haiku-4-5-20251001"]["costUSD"] == 0.10
