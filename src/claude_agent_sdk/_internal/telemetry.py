"""Telemetry utilities for OpenTelemetry integration."""

from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.metrics import Meter
    from opentelemetry.trace import Span, SpanKind, Tracer

try:
    from opentelemetry.trace import SpanKind as _SpanKind
except ImportError:
    _SpanKind = None


def get_otel_tracer(name: str) -> "Tracer | None":
    """Get OpenTelemetry tracer, returns None if not available.

    Args:
        name: The name of the tracer (typically the module path)

    Returns:
        OpenTelemetry Tracer if available, None otherwise
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return None


def get_otel_meter(name: str) -> "Meter | None":
    """Get OpenTelemetry meter, returns None if not available.

    Args:
        name: The name of the meter (typically the module path)

    Returns:
        OpenTelemetry Meter if available, None otherwise
    """
    try:
        from opentelemetry import metrics

        return metrics.get_meter(name)
    except ImportError:
        return None


def record_span_exception(span: "Span | None", exc: Exception) -> None:
    """Record an exception on a span and mark it as error."""
    if not span:
        return

    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        return

    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, description=str(exc)))


def span_kind_client() -> "SpanKind | None":
    return _SpanKind.CLIENT if _SpanKind is not None else None


def span_kind_internal() -> "SpanKind | None":
    return _SpanKind.INTERNAL if _SpanKind is not None else None


@contextmanager
def traced_span(
    tracer: "Tracer | None",
    span_name: str,
    kind: "SpanKind | None" = None,
    attributes: dict[str, Any] | None = None,
):
    """Context manager for creating traced spans with proper error handling.

    Args:
        tracer: OpenTelemetry tracer or None
        span_name: Name of the span
        kind: SpanKind (defaults to INTERNAL if not specified)
        attributes: Optional dictionary of span attributes

    Yields:
        Span if tracer is available, None otherwise
    """
    if not tracer:
        yield None
        return

    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        yield None
        return

    if kind is None and _SpanKind is not None:
        kind = _SpanKind.INTERNAL

    kwargs = {"name": span_name}
    if kind is not None:
        kwargs["kind"] = kind

    with tracer.start_as_current_span(**kwargs) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:  # Skip None values
                    span.set_attribute(key, value)
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, description=str(e)))
            raise


@asynccontextmanager
async def traced_span_async(
    tracer: "Tracer | None",
    span_name: str,
    kind: "SpanKind | None" = None,
    attributes: dict[str, Any] | None = None,
):
    """Async context manager for creating traced spans with proper error handling.

    Args:
        tracer: OpenTelemetry tracer or None
        span_name: Name of the span
        kind: SpanKind (defaults to INTERNAL if not specified)
        attributes: Optional dictionary of span attributes

    Yields:
        Span if tracer is available, None otherwise
    """
    if not tracer:
        yield None
        return

    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        yield None
        return

    if kind is None and _SpanKind is not None:
        kind = _SpanKind.INTERNAL

    kwargs = {"name": span_name}
    if kind is not None:
        kwargs["kind"] = kind

    with tracer.start_as_current_span(**kwargs) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:  # Skip None values
                    span.set_attribute(key, value)
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, description=str(e)))
            raise
