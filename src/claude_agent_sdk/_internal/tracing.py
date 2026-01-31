"""OpenTelemetry tracing utilities for Claude SDK.

This module provides helper functions for creating spans when tracing is enabled.
Tracing is opt-in via the `tracer` parameter in ClaudeAgentOptions.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.trace import Span, Tracer


# Span name constants
SPAN_QUERY = "claude_agent_sdk.query"
SPAN_SESSION = "claude_agent_sdk.session"
SPAN_TOOL = "claude_agent_sdk.tool"
SPAN_HOOK = "claude_agent_sdk.hook"
SPAN_MCP = "claude_agent_sdk.mcp"
SPAN_PERMISSION = "claude_agent_sdk.permission"
SPAN_CONTROL = "claude_agent_sdk.control"


class TracingContext:
    """Context manager for creating spans when a tracer is provided.

    This class provides a unified interface for tracing that gracefully
    handles the case when no tracer is configured (tracing disabled).
    """

    def __init__(self, tracer: "Tracer | None") -> None:
        """Initialize tracing context.

        Args:
            tracer: OpenTelemetry tracer, or None if tracing is disabled
        """
        self._tracer = tracer

    @property
    def enabled(self) -> bool:
        """Check if tracing is enabled."""
        return self._tracer is not None

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator["Span | None"]:
        """Create a span if tracing is enabled.

        Args:
            name: Span name (e.g., "claude_agent_sdk.query")
            attributes: Optional span attributes

        Yields:
            Span object if tracing is enabled, None otherwise
        """
        if self._tracer is None:
            yield None
            return

        span = self._tracer.start_span(name, attributes=attributes)
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(
                _get_error_status(str(e))
            )
            raise
        finally:
            span.end()

    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> "Span | None":
        """Start a span without automatic ending (caller must end it).

        Args:
            name: Span name
            attributes: Optional span attributes

        Returns:
            Span object if tracing is enabled, None otherwise
        """
        if self._tracer is None:
            return None
        return self._tracer.start_span(name, attributes=attributes)


def _get_error_status(description: str) -> Any:
    """Get an error status for a span.

    Args:
        description: Error description

    Returns:
        Status object with ERROR status
    """
    try:
        from opentelemetry.trace import Status, StatusCode
        return Status(StatusCode.ERROR, description)
    except ImportError:
        return None


def create_tracing_context(tracer: "Tracer | None") -> TracingContext:
    """Create a tracing context from a tracer.

    Args:
        tracer: OpenTelemetry tracer, or None

    Returns:
        TracingContext instance
    """
    return TracingContext(tracer)


def set_span_attributes(span: "Span | None", attributes: dict[str, Any]) -> None:
    """Set attributes on a span if it exists.

    Args:
        span: Span to set attributes on, or None
        attributes: Attributes to set
    """
    if span is None:
        return
    for key, value in attributes.items():
        if value is not None:
            # Convert non-primitive types to strings for OTel compatibility
            if isinstance(value, (str, int, float, bool)):
                span.set_attribute(key, value)
            elif isinstance(value, (list, tuple)):
                # OTel supports arrays of primitives
                span.set_attribute(key, list(value))
            else:
                span.set_attribute(key, str(value))


def record_exception(span: "Span | None", exception: Exception) -> None:
    """Record an exception on a span if it exists.

    Args:
        span: Span to record exception on, or None
        exception: Exception to record
    """
    if span is None:
        return
    span.record_exception(exception)
    try:
        from opentelemetry.trace import Status, StatusCode
        span.set_status(Status(StatusCode.ERROR, str(exception)))
    except ImportError:
        pass


def end_span(span: "Span | None") -> None:
    """End a span if it exists.

    Args:
        span: Span to end, or None
    """
    if span is not None:
        span.end()
