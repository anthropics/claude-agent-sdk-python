"""Tests for ToolContext and get_tool_context()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from claude_agent_sdk import ToolContext, get_tool_context
from claude_agent_sdk._internal._tool_context import _current_tool_context
from claude_agent_sdk.types import SessionMessage


class TestToolContextDataclass:
    """Test ToolContext creation and field access."""

    def test_required_fields(self) -> None:
        ctx = ToolContext(
            session_id="abc-123",
            transcript_path="/home/user/.claude/projects/proj/abc-123.jsonl",
            cwd="/home/user/project",
        )
        assert ctx.session_id == "abc-123"
        assert ctx.transcript_path == "/home/user/.claude/projects/proj/abc-123.jsonl"
        assert ctx.cwd == "/home/user/project"

    def test_optional_fields_default_none(self) -> None:
        ctx = ToolContext(
            session_id="abc-123",
            transcript_path="/path/to/transcript.jsonl",
            cwd="/cwd",
        )
        assert ctx.agent_id is None
        assert ctx.agent_type is None
        assert ctx.tool_use_id is None

    def test_all_fields(self) -> None:
        ctx = ToolContext(
            session_id="abc-123",
            transcript_path="/path/to/transcript.jsonl",
            cwd="/cwd",
            agent_id="agent-456",
            agent_type="general-purpose",
            tool_use_id="toolu_01ABC",
        )
        assert ctx.agent_id == "agent-456"
        assert ctx.agent_type == "general-purpose"
        assert ctx.tool_use_id == "toolu_01ABC"


class TestGetToolContext:
    """Test get_tool_context() function."""

    def test_returns_none_by_default(self) -> None:
        assert get_tool_context() is None

    def test_returns_context_when_set(self) -> None:
        ctx = ToolContext(
            session_id="sess-1",
            transcript_path="/p/t.jsonl",
            cwd="/cwd",
        )
        token = _current_tool_context.set(ctx)
        try:
            result = get_tool_context()
            assert result is ctx
            assert result is not None
            assert result.session_id == "sess-1"
        finally:
            _current_tool_context.reset(token)

    def test_returns_none_after_reset(self) -> None:
        ctx = ToolContext(
            session_id="sess-2",
            transcript_path="/p/t.jsonl",
            cwd="/cwd",
        )
        token = _current_tool_context.set(ctx)
        _current_tool_context.reset(token)
        assert get_tool_context() is None


class TestContextVarIsolation:
    """Test that the contextvar is properly scoped."""

    def test_set_and_reset(self) -> None:
        ctx = ToolContext(
            session_id="s1",
            transcript_path="/t.jsonl",
            cwd="/c",
        )
        # Should start as None
        assert _current_tool_context.get() is None

        token = _current_tool_context.set(ctx)
        assert _current_tool_context.get() is ctx

        _current_tool_context.reset(token)
        assert _current_tool_context.get() is None

    def test_nested_set_reset(self) -> None:
        ctx1 = ToolContext(session_id="s1", transcript_path="/t1.jsonl", cwd="/c1")
        ctx2 = ToolContext(session_id="s2", transcript_path="/t2.jsonl", cwd="/c2")

        token1 = _current_tool_context.set(ctx1)
        assert _current_tool_context.get() is ctx1

        token2 = _current_tool_context.set(ctx2)
        assert _current_tool_context.get() is ctx2

        _current_tool_context.reset(token2)
        assert _current_tool_context.get() is ctx1

        _current_tool_context.reset(token1)
        assert _current_tool_context.get() is None


class TestGetConversationHistory:
    """Test ToolContext.get_conversation_history()."""

    def test_calls_get_session_messages_with_correct_args(self) -> None:
        ctx = ToolContext(
            session_id="550e8400-e29b-41d4-a716-446655440000",
            transcript_path="/home/user/.claude/projects/proj/sessions/550e8400.jsonl",
            cwd="/home/user/project",
        )
        expected_dir = str(
            Path("/home/user/.claude/projects/proj/sessions/550e8400.jsonl")
            .parent.parent
        )

        mock_messages = [
            SessionMessage(
                type="user",
                uuid="msg-1",
                session_id="550e8400-e29b-41d4-a716-446655440000",
                message={"role": "user", "content": "hello"},
            ),
        ]

        with patch(
            "claude_agent_sdk._internal.sessions.get_session_messages",
            return_value=mock_messages,
        ) as mock_fn:
            result = ctx.get_conversation_history()

        mock_fn.assert_called_once_with(
            "550e8400-e29b-41d4-a716-446655440000",
            directory=expected_dir,
        )
        assert result == mock_messages

    def test_returns_empty_list_on_missing_session(self) -> None:
        ctx = ToolContext(
            session_id="nonexistent",
            transcript_path="/fake/path/sessions/nonexistent.jsonl",
            cwd="/cwd",
        )
        with patch(
            "claude_agent_sdk._internal.sessions.get_session_messages",
            return_value=[],
        ):
            result = ctx.get_conversation_history()
        assert result == []


class TestCaptureSessionContext:
    """Test that Query._capture_session_context works correctly."""

    def test_capture_from_hook_input(self) -> None:
        from claude_agent_sdk._internal.query import Query

        # Create a minimal Query without going through __init__
        q = object.__new__(Query)
        q._session_id = None
        q._transcript_path = None
        q._cwd = None
        q._agent_id = None
        q._agent_type = None

        q._capture_session_context({
            "session_id": "s-100",
            "transcript_path": "/t/s-100.jsonl",
            "cwd": "/project",
            "agent_id": "a-1",
            "agent_type": "code-reviewer",
        })

        assert q._session_id == "s-100"
        assert q._transcript_path == "/t/s-100.jsonl"
        assert q._cwd == "/project"
        assert q._agent_id == "a-1"
        assert q._agent_type == "code-reviewer"

    def test_capture_ignores_non_dict(self) -> None:
        from claude_agent_sdk._internal.query import Query

        q = object.__new__(Query)
        q._session_id = None
        q._transcript_path = None
        q._cwd = None
        q._agent_id = None
        q._agent_type = None

        q._capture_session_context(None)
        q._capture_session_context("not a dict")

        assert q._session_id is None

    def test_capture_preserves_agent_fields(self) -> None:
        """agent_id/agent_type should not be overwritten with None."""
        from claude_agent_sdk._internal.query import Query

        q = object.__new__(Query)
        q._session_id = None
        q._transcript_path = None
        q._cwd = None
        q._agent_id = "existing-agent"
        q._agent_type = "general-purpose"

        # Input without agent fields should preserve existing values
        q._capture_session_context({
            "session_id": "s-200",
            "transcript_path": "/t.jsonl",
            "cwd": "/c",
        })

        assert q._agent_id == "existing-agent"
        assert q._agent_type == "general-purpose"


class TestExports:
    """Test that ToolContext and get_tool_context are properly exported."""

    def test_tool_context_in_all(self) -> None:
        import claude_agent_sdk

        assert "ToolContext" in claude_agent_sdk.__all__

    def test_get_tool_context_in_all(self) -> None:
        import claude_agent_sdk

        assert "get_tool_context" in claude_agent_sdk.__all__

    def test_importable(self) -> None:
        from claude_agent_sdk import ToolContext, get_tool_context

        assert ToolContext is not None
        assert callable(get_tool_context)
