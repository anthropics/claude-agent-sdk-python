"""Tests for telemetry helpers."""

from __future__ import annotations

import pytest

from claude_agent_sdk._internal.telemetry import (
    get_otel_meter,
    get_otel_tracer,
    record_span_exception,
    span_kind_client,
    span_kind_internal,
    traced_span,
    traced_span_async,
)

# Import OTel test utilities - these tests will be skipped if OTel isn't installed
pytest.importorskip("opentelemetry")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import StatusCode


@pytest.fixture
def span_exporter():
    """Create an in-memory span exporter for testing."""
    return InMemorySpanExporter()


@pytest.fixture
def tracer(span_exporter):
    """Create a tracer with in-memory exporter for testing."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    return provider.get_tracer("test.tracer")


# --- No-op tests (when tracer is None) ---


def test_traced_span_no_tracer_is_noop() -> None:
    """traced_span should no-op when tracer is None."""
    with traced_span(None, "test.span") as span:
        assert span is None


@pytest.mark.asyncio
async def test_traced_span_async_no_tracer_is_noop() -> None:
    """traced_span_async should no-op when tracer is None."""
    async with traced_span_async(None, "test.span") as span:
        assert span is None


# --- Span creation tests ---


def test_traced_span_creates_span(tracer, span_exporter) -> None:
    """traced_span should create a span with correct name."""
    with traced_span(tracer, "test.operation") as span:
        assert span is not None
        span.set_attribute("custom.attr", "value")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "test.operation"
    assert spans[0].status.status_code == StatusCode.OK


@pytest.mark.asyncio
async def test_traced_span_async_creates_span(tracer, span_exporter) -> None:
    """traced_span_async should create a span with correct name."""
    async with traced_span_async(tracer, "test.async.operation") as span:
        assert span is not None
        span.set_attribute("custom.attr", "async_value")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "test.async.operation"
    assert spans[0].status.status_code == StatusCode.OK


# --- Attributes tests ---


def test_traced_span_sets_attributes(tracer, span_exporter) -> None:
    """traced_span should set initial attributes on the span."""
    attributes = {
        "string.attr": "hello",
        "int.attr": 42,
        "bool.attr": True,
        "none.attr": None,  # Should be skipped
    }

    with traced_span(tracer, "test.with.attrs", attributes=attributes):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span_attrs = dict(spans[0].attributes)
    assert span_attrs["string.attr"] == "hello"
    assert span_attrs["int.attr"] == 42
    assert span_attrs["bool.attr"] is True
    assert "none.attr" not in span_attrs  # None values should be skipped


@pytest.mark.asyncio
async def test_traced_span_async_sets_attributes(tracer, span_exporter) -> None:
    """traced_span_async should set initial attributes on the span."""
    attributes = {
        "query.type": "streaming",
        "query.timeout": 30,
    }

    async with traced_span_async(tracer, "test.async.attrs", attributes=attributes):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span_attrs = dict(spans[0].attributes)
    assert span_attrs["query.type"] == "streaming"
    assert span_attrs["query.timeout"] == 30


# --- SpanKind tests ---


def test_traced_span_with_client_kind(tracer, span_exporter) -> None:
    """traced_span should accept SpanKind."""
    with traced_span(tracer, "test.client.span", kind=span_kind_client()):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].kind == trace.SpanKind.CLIENT


def test_traced_span_defaults_to_internal_kind(tracer, span_exporter) -> None:
    """traced_span should default to INTERNAL SpanKind."""
    with traced_span(tracer, "test.internal.span"):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].kind == trace.SpanKind.INTERNAL


@pytest.mark.asyncio
async def test_traced_span_async_with_internal_kind(tracer, span_exporter) -> None:
    """traced_span_async should accept SpanKind."""
    async with traced_span_async(
        tracer, "test.async.internal", kind=span_kind_internal()
    ):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].kind == trace.SpanKind.INTERNAL


# --- Error handling tests ---


def test_traced_span_records_exception(tracer, span_exporter) -> None:
    """traced_span should record exceptions and set error status."""
    with pytest.raises(ValueError, match="test error"):
        with traced_span(tracer, "test.error.span"):
            raise ValueError("test error")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert "test error" in spans[0].status.description

    # Check exception was recorded (may be recorded multiple times by OTel internals)
    events = spans[0].events
    assert len(events) >= 1
    exception_events = [e for e in events if e.name == "exception"]
    assert len(exception_events) >= 1


@pytest.mark.asyncio
async def test_traced_span_async_records_exception(tracer, span_exporter) -> None:
    """traced_span_async should record exceptions and set error status."""
    with pytest.raises(RuntimeError, match="async error"):
        async with traced_span_async(tracer, "test.async.error"):
            raise RuntimeError("async error")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert "async error" in spans[0].status.description

    # Check exception was recorded (may be recorded multiple times by OTel internals)
    events = spans[0].events
    assert len(events) >= 1
    exception_events = [e for e in events if e.name == "exception"]
    assert len(exception_events) >= 1


# --- record_span_exception tests ---


def test_record_span_exception_on_active_span(tracer, span_exporter) -> None:
    """record_span_exception should record exception on span."""
    with tracer.start_as_current_span("test.manual.span") as span:
        exc = ValueError("manual exception")
        record_span_exception(span, exc)

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert "manual exception" in spans[0].status.description


def test_record_span_exception_noop_on_none() -> None:
    """record_span_exception should no-op when span is None."""
    # Should not raise
    record_span_exception(None, ValueError("ignored"))


# --- SpanKind helper tests ---


def test_span_kind_client_returns_client() -> None:
    """span_kind_client should return SpanKind.CLIENT."""
    kind = span_kind_client()
    assert kind == trace.SpanKind.CLIENT


def test_span_kind_internal_returns_internal() -> None:
    """span_kind_internal should return SpanKind.INTERNAL."""
    kind = span_kind_internal()
    assert kind == trace.SpanKind.INTERNAL


# --- get_otel_tracer / get_otel_meter tests ---


def test_get_otel_tracer_returns_tracer() -> None:
    """get_otel_tracer should return a tracer when OTel is available."""
    tracer = get_otel_tracer("test.module")
    assert tracer is not None
    # Should have start_as_current_span method
    assert hasattr(tracer, "start_as_current_span")


def test_get_otel_meter_returns_meter() -> None:
    """get_otel_meter should return a meter when OTel is available."""
    meter = get_otel_meter("test.module")
    assert meter is not None
    # Should have create_counter method
    assert hasattr(meter, "create_counter")


# --- Nested spans test ---


def test_nested_traced_spans(tracer, span_exporter) -> None:
    """Nested traced_span calls should create parent-child relationship."""
    with traced_span(tracer, "parent.span") as parent:
        parent.set_attribute("level", "parent")
        with traced_span(tracer, "child.span") as child:
            child.set_attribute("level", "child")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 2

    # Spans are exported in order of completion (child first)
    child_span = spans[0]
    parent_span = spans[1]

    assert child_span.name == "child.span"
    assert parent_span.name == "parent.span"

    # Child should have parent as parent
    assert child_span.parent.span_id == parent_span.context.span_id


@pytest.mark.asyncio
async def test_nested_traced_spans_async(tracer, span_exporter) -> None:
    """Nested traced_span_async calls should create parent-child relationship."""
    async with traced_span_async(tracer, "async.parent") as parent:
        parent.set_attribute("level", "parent")
        async with traced_span_async(tracer, "async.child") as child:
            child.set_attribute("level", "child")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 2

    child_span = spans[0]
    parent_span = spans[1]

    assert child_span.name == "async.child"
    assert parent_span.name == "async.parent"
    assert child_span.parent.span_id == parent_span.context.span_id
