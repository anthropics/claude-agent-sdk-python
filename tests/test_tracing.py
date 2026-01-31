"""Tests for OpenTelemetry tracing support."""

import pytest
from unittest.mock import MagicMock, patch

from claude_agent_sdk._internal.tracing import (
    SPAN_CONTROL,
    SPAN_HOOK,
    SPAN_MCP,
    SPAN_PERMISSION,
    SPAN_QUERY,
    SPAN_SESSION,
    TracingContext,
    create_tracing_context,
    end_span,
    record_exception,
    set_span_attributes,
)
from claude_agent_sdk.types import ClaudeAgentOptions


class TestTracingContext:
    """Tests for TracingContext class."""

    def test_tracing_disabled_when_no_tracer(self):
        """Test that tracing is disabled when no tracer is provided."""
        ctx = TracingContext(None)
        assert ctx.enabled is False

    def test_tracing_enabled_when_tracer_provided(self):
        """Test that tracing is enabled when a tracer is provided."""
        mock_tracer = MagicMock()
        ctx = TracingContext(mock_tracer)
        assert ctx.enabled is True

    def test_span_context_manager_no_tracer(self):
        """Test span context manager yields None when no tracer."""
        ctx = TracingContext(None)
        with ctx.span("test_span") as span:
            assert span is None

    def test_span_context_manager_with_tracer(self):
        """Test span context manager creates and ends span when tracer provided."""
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        ctx = TracingContext(mock_tracer)
        with ctx.span("test_span", attributes={"key": "value"}) as span:
            assert span is mock_span

        mock_tracer.start_span.assert_called_once_with(
            "test_span", attributes={"key": "value"}
        )
        mock_span.end.assert_called_once()

    def test_span_context_manager_records_exception(self):
        """Test span context manager records exception on error."""
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        ctx = TracingContext(mock_tracer)
        with pytest.raises(ValueError):
            with ctx.span("test_span") as span:
                raise ValueError("test error")

        mock_span.record_exception.assert_called_once()
        mock_span.end.assert_called_once()

    def test_start_span_no_tracer(self):
        """Test start_span returns None when no tracer."""
        ctx = TracingContext(None)
        span = ctx.start_span("test_span")
        assert span is None

    def test_start_span_with_tracer(self):
        """Test start_span creates span when tracer provided."""
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        ctx = TracingContext(mock_tracer)
        span = ctx.start_span("test_span", attributes={"key": "value"})

        assert span is mock_span
        mock_tracer.start_span.assert_called_once_with(
            "test_span", attributes={"key": "value"}
        )


class TestTracingHelpers:
    """Tests for tracing helper functions."""

    def test_set_span_attributes_with_none_span(self):
        """Test set_span_attributes handles None span gracefully."""
        # Should not raise
        set_span_attributes(None, {"key": "value"})

    def test_set_span_attributes_with_span(self):
        """Test set_span_attributes sets attributes on span."""
        mock_span = MagicMock()
        set_span_attributes(mock_span, {
            "str_key": "value",
            "int_key": 42,
            "float_key": 3.14,
            "bool_key": True,
            "list_key": [1, 2, 3],
            "none_key": None,
            "dict_key": {"nested": "value"},
        })

        # None values should be skipped
        calls = mock_span.set_attribute.call_args_list
        assert len(calls) == 6  # All except none_key

    def test_record_exception_with_none_span(self):
        """Test record_exception handles None span gracefully."""
        # Should not raise
        record_exception(None, ValueError("test"))

    def test_record_exception_with_span(self):
        """Test record_exception records exception on span."""
        mock_span = MagicMock()
        exc = ValueError("test error")

        record_exception(mock_span, exc)

        mock_span.record_exception.assert_called_once_with(exc)

    def test_end_span_with_none_span(self):
        """Test end_span handles None span gracefully."""
        # Should not raise
        end_span(None)

    def test_end_span_with_span(self):
        """Test end_span ends the span."""
        mock_span = MagicMock()
        end_span(mock_span)
        mock_span.end.assert_called_once()

    def test_create_tracing_context(self):
        """Test create_tracing_context creates TracingContext."""
        mock_tracer = MagicMock()
        ctx = create_tracing_context(mock_tracer)
        assert isinstance(ctx, TracingContext)
        assert ctx.enabled is True


class TestSpanNames:
    """Tests for span name constants."""

    def test_span_names_are_strings(self):
        """Test that all span names are strings."""
        assert isinstance(SPAN_QUERY, str)
        assert isinstance(SPAN_SESSION, str)
        assert isinstance(SPAN_HOOK, str)
        assert isinstance(SPAN_MCP, str)
        assert isinstance(SPAN_PERMISSION, str)
        assert isinstance(SPAN_CONTROL, str)

    def test_span_names_follow_convention(self):
        """Test that span names follow the naming convention."""
        assert SPAN_QUERY.startswith("claude_agent_sdk.")
        assert SPAN_SESSION.startswith("claude_agent_sdk.")
        assert SPAN_HOOK.startswith("claude_agent_sdk.")
        assert SPAN_MCP.startswith("claude_agent_sdk.")
        assert SPAN_PERMISSION.startswith("claude_agent_sdk.")
        assert SPAN_CONTROL.startswith("claude_agent_sdk.")


class TestClaudeAgentOptionsTracer:
    """Tests for tracer option in ClaudeAgentOptions."""

    def test_tracer_defaults_to_none(self):
        """Test that tracer defaults to None."""
        options = ClaudeAgentOptions()
        assert options.tracer is None

    def test_tracer_can_be_set(self):
        """Test that tracer can be set."""
        mock_tracer = MagicMock()
        options = ClaudeAgentOptions(tracer=mock_tracer)
        assert options.tracer is mock_tracer
