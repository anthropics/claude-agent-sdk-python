"""Tests for message parser error handling."""

import pytest

from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import (
    AssistantMessage,
    DeferredToolUse,
    HookEventMessage,
    RateLimitEvent,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
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

    def test_parse_assistant_message_with_server_tool_use(self):
        """server_tool_use blocks (e.g. advisor, web_search) are preserved.

        Previously these were dropped, leaving an empty content list on
        messages that only contained a server tool call.
        """
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srvtoolu_01ABC",
                        "name": "advisor",
                        "input": {},
                    },
                ],
                "model": "claude-sonnet-4-5",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert len(message.content) == 1
        assert isinstance(message.content[0], ServerToolUseBlock)
        assert message.content[0].id == "srvtoolu_01ABC"
        assert message.content[0].name == "advisor"
        assert message.content[0].input == {}

    def test_parse_assistant_message_with_server_tool_result(self):
        """Server-side tool result blocks (e.g. advisor) surface with their raw content dict.

        `content` is passed through as a dict since its shape is tool-specific
        (advisor emits advisor_result / advisor_redacted_result /
        advisor_tool_result_error; other server tools use different shapes).
        """
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "advisor_tool_result",
                        "tool_use_id": "srvtoolu_01ABC",
                        "content": {
                            "type": "advisor_result",
                            "text": "Consider edge cases around empty input.",
                        },
                    },
                ],
                "model": "claude-sonnet-4-5",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert len(message.content) == 1
        result_block = message.content[0]
        assert isinstance(result_block, ServerToolResultBlock)
        assert result_block.tool_use_id == "srvtoolu_01ABC"
        assert result_block.content == {
            "type": "advisor_result",
            "text": "Consider edge cases around empty input.",
        }

    def test_parse_assistant_message_with_redacted_advisor_result(self):
        """External API users get advisor output as an encrypted blob in the content dict."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "advisor_tool_result",
                        "tool_use_id": "srvtoolu_01ABC",
                        "content": {
                            "type": "advisor_redacted_result",
                            "encrypted_content": "EuYDCioIDhgC...",
                        },
                    },
                ],
                "model": "claude-sonnet-4-5",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        result_block = message.content[0]
        assert isinstance(result_block, ServerToolResultBlock)
        assert result_block.content["type"] == "advisor_redacted_result"
        assert result_block.content["encrypted_content"] == "EuYDCioIDhgC..."

    def test_parse_assistant_message_with_usage(self):
        """Per-turn usage is preserved on AssistantMessage.

        The CLI emits the API's full usage dict (including cache token
        breakdown) on every assistant message. Previously this was dropped
        by the parser, forcing consumers to wait for the aggregate in
        ResultMessage. See issue #673.
        """
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hi"}],
                "model": "claude-opus-4-5",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 2000,
                    "cache_creation_input_tokens": 500,
                },
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.usage == {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 2000,
            "cache_creation_input_tokens": 500,
        }

    def test_parse_assistant_message_without_usage(self):
        """usage defaults to None when absent (e.g. synthetic messages)."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hi"}],
                "model": "claude-opus-4-5",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.usage is None

    def test_parse_valid_system_message(self):
        """Test parsing a valid system message."""
        data = {"type": "system", "subtype": "start"}
        message = parse_message(data)
        assert isinstance(message, SystemMessage)
        assert message.subtype == "start"

    def test_parse_task_started_message(self):
        """Test parsing a task_started system message yields a TaskStartedMessage."""
        data = {
            "type": "system",
            "subtype": "task_started",
            "task_id": "task-abc",
            "tool_use_id": "toolu_01",
            "description": "Reticulating splines",
            "task_type": "background",
            "uuid": "uuid-1",
            "session_id": "session-1",
        }
        message = parse_message(data)
        assert isinstance(message, TaskStartedMessage)
        assert message.task_id == "task-abc"
        assert message.description == "Reticulating splines"
        assert message.uuid == "uuid-1"
        assert message.session_id == "session-1"
        assert message.tool_use_id == "toolu_01"
        assert message.task_type == "background"

    def test_parse_task_started_message_optional_fields_absent(self):
        """task_started with no optional fields should still parse, optionals set to None."""
        data = {
            "type": "system",
            "subtype": "task_started",
            "task_id": "task-abc",
            "description": "Working",
            "uuid": "uuid-1",
            "session_id": "session-1",
        }
        message = parse_message(data)
        assert isinstance(message, TaskStartedMessage)
        assert message.tool_use_id is None
        assert message.task_type is None

    def test_parse_task_progress_message(self):
        """Test parsing a task_progress system message yields a TaskProgressMessage."""
        data = {
            "type": "system",
            "subtype": "task_progress",
            "task_id": "task-abc",
            "tool_use_id": "toolu_01",
            "description": "Halfway there",
            "usage": {
                "total_tokens": 1234,
                "tool_uses": 5,
                "duration_ms": 9876,
            },
            "last_tool_name": "Read",
            "uuid": "uuid-2",
            "session_id": "session-1",
        }
        message = parse_message(data)
        assert isinstance(message, TaskProgressMessage)
        assert message.task_id == "task-abc"
        assert message.description == "Halfway there"
        assert message.usage == {
            "total_tokens": 1234,
            "tool_uses": 5,
            "duration_ms": 9876,
        }
        assert message.last_tool_name == "Read"
        assert message.tool_use_id == "toolu_01"
        assert message.uuid == "uuid-2"
        assert message.session_id == "session-1"

    def test_parse_task_notification_message(self):
        """Test parsing a task_notification system message yields a TaskNotificationMessage."""
        data = {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "task-abc",
            "tool_use_id": "toolu_01",
            "status": "completed",
            "output_file": "/tmp/out.md",
            "summary": "All done",
            "usage": {
                "total_tokens": 2000,
                "tool_uses": 7,
                "duration_ms": 12345,
            },
            "uuid": "uuid-3",
            "session_id": "session-1",
        }
        message = parse_message(data)
        assert isinstance(message, TaskNotificationMessage)
        assert message.task_id == "task-abc"
        assert message.status == "completed"
        assert message.output_file == "/tmp/out.md"
        assert message.summary == "All done"
        assert message.usage == {
            "total_tokens": 2000,
            "tool_uses": 7,
            "duration_ms": 12345,
        }
        assert message.tool_use_id == "toolu_01"
        assert message.uuid == "uuid-3"
        assert message.session_id == "session-1"

    def test_parse_task_notification_message_optional_fields_absent(self):
        """task_notification with no optional fields (usage, tool_use_id) still parses."""
        data = {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "task-abc",
            "status": "failed",
            "output_file": "/tmp/out.md",
            "summary": "Boom",
            "uuid": "uuid-3",
            "session_id": "session-1",
        }
        message = parse_message(data)
        assert isinstance(message, TaskNotificationMessage)
        assert message.status == "failed"
        assert message.usage is None
        assert message.tool_use_id is None

    def test_task_message_backward_compat_isinstance(self):
        """Backward-compat: typed task messages are still SystemMessage instances."""
        started_data = {
            "type": "system",
            "subtype": "task_started",
            "task_id": "t1",
            "description": "desc",
            "uuid": "u1",
            "session_id": "s1",
        }
        progress_data = {
            "type": "system",
            "subtype": "task_progress",
            "task_id": "t1",
            "description": "desc",
            "usage": {"total_tokens": 1, "tool_uses": 0, "duration_ms": 10},
            "uuid": "u2",
            "session_id": "s1",
        }
        notif_data = {
            "type": "system",
            "subtype": "task_notification",
            "task_id": "t1",
            "status": "stopped",
            "output_file": "/o",
            "summary": "s",
            "uuid": "u3",
            "session_id": "s1",
        }
        started = parse_message(started_data)
        progress = parse_message(progress_data)
        notif = parse_message(notif_data)
        # isinstance checks against the base class still work
        assert isinstance(started, SystemMessage)
        assert isinstance(progress, SystemMessage)
        assert isinstance(notif, SystemMessage)
        # match-case against SystemMessage still works
        matched = False
        match started:
            case SystemMessage():
                matched = True
        assert matched

    def test_task_message_backward_compat_base_fields(self):
        """Backward-compat: subtype and data fields on typed task messages are populated."""
        data = {
            "type": "system",
            "subtype": "task_started",
            "task_id": "t1",
            "description": "desc",
            "uuid": "u1",
            "session_id": "s1",
        }
        message = parse_message(data)
        assert isinstance(message, TaskStartedMessage)
        # Base class fields still populated for legacy code paths
        assert message.subtype == "task_started"
        assert message.data == data
        assert message.data["task_id"] == "t1"

    def test_unknown_system_subtype_yields_generic(self):
        """Unknown system subtypes fall through to generic SystemMessage (not a subclass)."""
        data = {"type": "system", "subtype": "some_future_subtype", "foo": "bar"}
        message = parse_message(data)
        assert isinstance(message, SystemMessage)
        # Ensure it's exactly SystemMessage, not one of the typed subclasses
        assert type(message) is SystemMessage
        assert not isinstance(message, TaskStartedMessage)
        assert not isinstance(message, TaskProgressMessage)
        assert not isinstance(message, TaskNotificationMessage)
        assert message.subtype == "some_future_subtype"
        assert message.data == data

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
        assert message.stop_reason is None

    def test_parse_result_message_with_stop_reason(self):
        """Test parsing a result message with stop_reason field.

        The stop_reason field mirrors the Anthropic API's stop_reason on the
        final assistant turn (e.g., "end_turn", "max_tokens", "tool_use").
        """
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 500,
            "is_error": False,
            "num_turns": 2,
            "session_id": "session_123",
            "stop_reason": "end_turn",
            "result": "Done",
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.stop_reason == "end_turn"
        assert message.result == "Done"

    def test_parse_result_message_with_null_stop_reason(self):
        """Test parsing a result message with explicit null stop_reason."""
        data = {
            "type": "result",
            "subtype": "error_max_turns",
            "duration_ms": 1000,
            "duration_api_ms": 500,
            "is_error": True,
            "num_turns": 10,
            "session_id": "session_123",
            "stop_reason": None,
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.stop_reason is None

    def test_parse_rate_limit_event(self):
        """Test parsing a rate_limit_event into a typed RateLimitEvent."""
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed_warning",
                "resetsAt": 1700000000,
                "rateLimitType": "five_hour",
                "utilization": 0.91,
            },
            "uuid": "abc-123",
            "session_id": "session_xyz",
        }
        message = parse_message(data)
        assert isinstance(message, RateLimitEvent)
        assert message.uuid == "abc-123"
        assert message.session_id == "session_xyz"
        assert message.rate_limit_info.status == "allowed_warning"
        assert message.rate_limit_info.resets_at == 1700000000
        assert message.rate_limit_info.rate_limit_type == "five_hour"
        assert message.rate_limit_info.utilization == 0.91

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
        """Test that unknown message type returns None for forward compatibility."""
        result = parse_message({"type": "unknown_type"})
        assert result is None

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
        # Use a malformed known type (missing required fields) to trigger error
        data = {"type": "assistant"}
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

    def test_parse_assistant_message_with_all_fields(self):
        """Test that AssistantMessage preserves id, stop_reason, session_id, uuid."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello"}],
                "model": "claude-sonnet-4-5-20250929",
                "id": "msg_01HRq7YZE3apPqSHydvG77Ve",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            "session_id": "fdf2d90a-fd9e-4736-ae35-806edd13643f",
            "uuid": "0dbd2453-1209-4fe9-bd51-4102f64e33df",
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.message_id == "msg_01HRq7YZE3apPqSHydvG77Ve"
        assert message.stop_reason == "end_turn"
        assert message.session_id == "fdf2d90a-fd9e-4736-ae35-806edd13643f"
        assert message.uuid == "0dbd2453-1209-4fe9-bd51-4102f64e33df"
        assert message.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_parse_assistant_message_optional_fields_absent(self):
        """New optional fields default to None when absent."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hi"}],
                "model": "claude-opus-4-5",
            },
        }
        message = parse_message(data)
        assert isinstance(message, AssistantMessage)
        assert message.message_id is None
        assert message.stop_reason is None
        assert message.session_id is None
        assert message.uuid is None

    def test_parse_result_message_with_model_usage(self):
        """Test that ResultMessage preserves modelUsage, permission_denials, uuid."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 3000,
            "duration_api_ms": 2000,
            "is_error": False,
            "num_turns": 1,
            "session_id": "fdf2d90a-fd9e-4736-ae35-806edd13643f",
            "stop_reason": "end_turn",
            "total_cost_usd": 0.0106,
            "usage": {"input_tokens": 3, "output_tokens": 24},
            "result": "Hello",
            "modelUsage": {
                "claude-sonnet-4-5-20250929": {
                    "inputTokens": 3,
                    "outputTokens": 24,
                    "cacheReadInputTokens": 20012,
                    "costUSD": 0.0106,
                    "contextWindow": 200000,
                    "maxOutputTokens": 64000,
                }
            },
            "permission_denials": [],
            "uuid": "d379c496-f33a-4ea4-b920-3c5483baa6f7",
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.model_usage is not None
        assert "claude-sonnet-4-5-20250929" in message.model_usage
        assert message.model_usage["claude-sonnet-4-5-20250929"]["costUSD"] == 0.0106
        assert message.permission_denials == []
        assert message.uuid == "d379c496-f33a-4ea4-b920-3c5483baa6f7"

    def test_parse_result_message_optional_fields_absent(self):
        """New optional fields default to None when absent."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 500,
            "is_error": False,
            "num_turns": 1,
            "session_id": "session_123",
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.model_usage is None
        assert message.permission_denials is None
        assert message.deferred_tool_use is None
        assert message.errors is None
        assert message.api_error_status is None
        assert message.uuid is None

    def test_parse_result_message_with_deferred_tool_use(self):
        """ResultMessage parses deferred_tool_use into a DeferredToolUse."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1200,
            "duration_api_ms": 900,
            "is_error": False,
            "num_turns": 1,
            "session_id": "session_123",
            "deferred_tool_use": {
                "id": "toolu_01abc",
                "name": "Bash",
                "input": {"command": "rm -rf /tmp/scratch"},
            },
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert isinstance(message.deferred_tool_use, DeferredToolUse)
        assert message.deferred_tool_use.id == "toolu_01abc"
        assert message.deferred_tool_use.name == "Bash"
        assert message.deferred_tool_use.input == {"command": "rm -rf /tmp/scratch"}

    def test_parse_result_message_with_errors(self):
        """Test that ResultMessage preserves the errors field from error results.

        The CLI emits errors: string[] on error result messages (subtypes like
        error_during_execution, error_max_turns, etc.). Without this field,
        SDK users cannot diagnose why a non-zero exit occurred.
        """
        data = {
            "type": "result",
            "subtype": "error_during_execution",
            "duration_ms": 5000,
            "duration_api_ms": 3000,
            "is_error": True,
            "num_turns": 3,
            "session_id": "session_456",
            "errors": [
                "Tool execution failed: permission denied",
                "Unable to write to /etc/hosts",
            ],
            "uuid": "err-uuid-789",
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.errors == [
            "Tool execution failed: permission denied",
            "Unable to write to /etc/hosts",
        ]
        assert message.is_error is True
        assert message.subtype == "error_during_execution"
        assert message.uuid == "err-uuid-789"

    def test_parse_result_message_with_api_error_status(self):
        """ResultMessage surfaces api_error_status for failed API calls.

        The CLI (v2.1.110+) emits api_error_status: number | null on the final
        result message — the HTTP status of the failing API call when
        is_error=True and subtype="success". This is the only safe-to-log
        signal for classifying API failures (e.g. 429 vs 529).
        """
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 2000,
            "duration_api_ms": 1500,
            "is_error": True,
            "num_turns": 1,
            "session_id": "session_overload",
            "api_error_status": 529,
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.api_error_status == 529
        assert message.is_error is True
        assert message.subtype == "success"

    def test_parse_result_message_success_no_errors(self):
        """Test that a successful result message has no errors field."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 500,
            "is_error": False,
            "num_turns": 1,
            "session_id": "session_789",
            "result": "Task completed successfully",
        }
        message = parse_message(data)
        assert isinstance(message, ResultMessage)
        assert message.errors is None
        assert message.result == "Task completed successfully"

    def test_parse_hook_event_message(self):
        """Hook started events (system/hook_started) parse into HookEventMessage."""
        data = {
            "type": "system",
            "subtype": "hook_started",
            "hook_event": "PreToolUse",
            "hook_name": "PreToolUse",
            "session_id": "sess-123",
            "uuid": "uuid-456",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        message = parse_message(data)
        assert isinstance(message, HookEventMessage)
        assert message.subtype == "hook_started"
        assert message.hook_event_name == "PreToolUse"
        assert message.session_id == "sess-123"
        assert message.uuid == "uuid-456"
        assert message.data == data

    def test_parse_hook_event_message_response(self):
        """Hook response events (system/hook_response) parse into HookEventMessage."""
        data = {
            "type": "system",
            "subtype": "hook_response",
            "hook_event": "PostToolUse",
            "hook_name": "PostToolUse",
            "session_id": "sess-123",
            "uuid": "uuid-789",
            "output": "",
            "exit_code": 0,
            "outcome": "success",
        }
        message = parse_message(data)
        assert isinstance(message, HookEventMessage)
        assert message.subtype == "hook_response"
        assert message.hook_event_name == "PostToolUse"
        assert message.session_id == "sess-123"
        assert message.uuid == "uuid-789"
        assert message.data["output"] == ""
        assert message.data["exit_code"] == 0
        assert message.data["outcome"] == "success"

    def test_parse_hook_event_message_isinstance_system(self):
        """HookEventMessage is a SystemMessage subclass for backward compat."""
        data = {"type": "system", "subtype": "hook_started", "hook_event": "PreToolUse"}
        message = parse_message(data)
        assert isinstance(message, HookEventMessage)
        assert isinstance(message, SystemMessage)

    def test_parse_hook_event_message_minimal(self):
        """Hook events without session_id/uuid/hook_event still parse."""
        data = {"type": "system", "subtype": "hook_started", "hook_name": "Stop"}
        message = parse_message(data)
        assert isinstance(message, HookEventMessage)
        assert message.subtype == "hook_started"
        assert message.hook_event_name == "Stop"
        assert message.session_id is None
        assert message.uuid is None
