"""Tests for OpenTelemetry tracing integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_agent_sdk._internal import tracing as tracing_mod
from claude_agent_sdk._internal.tracing import (
    disable_tracing,
    enable_tracing,
    is_tracing_enabled,
    record_span_event,
    set_span_attributes,
    start_span,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_tracing_state() -> Any:
    """Ensure each test starts with tracing disabled."""
    disable_tracing()
    yield
    disable_tracing()


def _make_mock_tracer() -> MagicMock:
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_span.return_value = mock_span
    return mock_tracer


def _force_enable_with_mock_tracer(mock_tracer: MagicMock) -> None:
    """Directly inject a mock tracer into the tracing module state.

    This bypasses the ``enable_tracing()`` path (which tries to call
    ``otel_trace.get_tracer()``), so tests do not need opentelemetry-api
    installed or patched.
    """
    tracing_mod._enabled = True
    tracing_mod._tracer = mock_tracer


# ---------------------------------------------------------------------------
# enable_tracing / disable_tracing
# ---------------------------------------------------------------------------


class TestEnableDisable:
    def test_enable_tracing_without_otel_raises(self) -> None:
        with (
            patch.object(tracing_mod, "_HAS_OTEL", False),
            pytest.raises(RuntimeError, match="opentelemetry-api is not installed"),
        ):
            enable_tracing()

    def test_enable_tracing_with_otel(self) -> None:
        mock_tracer = _make_mock_tracer()
        mock_otel = MagicMock()
        mock_otel.get_tracer.return_value = mock_tracer
        with (
            patch.object(tracing_mod, "_HAS_OTEL", True),
            patch.object(tracing_mod, "otel_trace", mock_otel, create=True),
        ):
            enable_tracing(tracer_name="test-sdk")

            assert is_tracing_enabled()
            mock_otel.get_tracer.assert_called_once_with("test-sdk")

    def test_disable_tracing(self) -> None:
        mock_tracer = _make_mock_tracer()
        _force_enable_with_mock_tracer(mock_tracer)
        assert is_tracing_enabled()

        disable_tracing()
        assert not is_tracing_enabled()

    def test_enable_tracing_default_name(self) -> None:
        mock_tracer = _make_mock_tracer()
        mock_otel = MagicMock()
        mock_otel.get_tracer.return_value = mock_tracer
        with (
            patch.object(tracing_mod, "_HAS_OTEL", True),
            patch.object(tracing_mod, "otel_trace", mock_otel, create=True),
        ):
            enable_tracing()
            mock_otel.get_tracer.assert_called_once_with("claude_agent_sdk")


# ---------------------------------------------------------------------------
# start_span
# ---------------------------------------------------------------------------


class TestStartSpan:
    def test_noop_when_disabled(self) -> None:
        """start_span yields None when tracing is not enabled."""
        with start_span("test.span") as span:
            assert span is None

    def test_creates_span_when_enabled(self) -> None:
        mock_tracer = _make_mock_tracer()
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            _force_enable_with_mock_tracer(mock_tracer)

            with start_span("test.span", attributes={"key": "val"}) as span:
                assert span is not None
                assert span is mock_tracer.start_span.return_value

            mock_tracer.start_span.assert_called_once_with(
                "test.span", attributes={"key": "val"}
            )
            span.set_status.assert_called()  # type: ignore[union-attr]
            span.end.assert_called_once()  # type: ignore[union-attr]

    def test_records_exception_on_error(self) -> None:
        mock_tracer = _make_mock_tracer()
        mock_span = mock_tracer.start_span.return_value
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            _force_enable_with_mock_tracer(mock_tracer)

            with (
                pytest.raises(ValueError, match="boom"),
                start_span("test.error"),
            ):
                raise ValueError("boom")

            mock_span.record_exception.assert_called_once()
            mock_span.end.assert_called_once()

    def test_span_attributes_default_empty(self) -> None:
        mock_tracer = _make_mock_tracer()
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            _force_enable_with_mock_tracer(mock_tracer)

            with start_span("test.default_attrs"):
                pass

            mock_tracer.start_span.assert_called_once_with(
                "test.default_attrs", attributes={}
            )

    def test_tracer_start_span_failure_yields_none(self) -> None:
        """If tracer.start_span raises, we yield None and don't crash."""
        mock_tracer = _make_mock_tracer()
        mock_tracer.start_span.side_effect = RuntimeError("tracer broken")
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            _force_enable_with_mock_tracer(mock_tracer)

            with start_span("test.broken") as span:
                assert span is None


# ---------------------------------------------------------------------------
# record_span_event / set_span_attributes
# ---------------------------------------------------------------------------


class TestSpanHelpers:
    def test_record_event_noop_on_none(self) -> None:
        # Should not raise
        record_span_event(None, "test_event", {"key": "val"})

    def test_record_event_on_span(self) -> None:
        mock_span = MagicMock()
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            record_span_event(mock_span, "test_event", {"k": "v"})
            mock_span.add_event.assert_called_once_with(
                "test_event", attributes={"k": "v"}
            )

    def test_record_event_default_attrs(self) -> None:
        mock_span = MagicMock()
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            record_span_event(mock_span, "evt")
            mock_span.add_event.assert_called_once_with("evt", attributes={})

    def test_set_attributes_noop_on_none(self) -> None:
        set_span_attributes(None, {"key": "val"})

    def test_set_attributes_on_span(self) -> None:
        mock_span = MagicMock()
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            set_span_attributes(mock_span, {"a": 1, "b": "two"})
            mock_span.set_attribute.assert_any_call("a", 1)
            mock_span.set_attribute.assert_any_call("b", "two")

    def test_record_event_tolerates_span_error(self) -> None:
        mock_span = MagicMock()
        mock_span.add_event.side_effect = RuntimeError("span broken")
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            record_span_event(mock_span, "test_event")  # should not raise

    def test_set_attributes_tolerates_span_error(self) -> None:
        mock_span = MagicMock()
        mock_span.set_attribute.side_effect = RuntimeError("span broken")
        with patch.object(tracing_mod, "_HAS_OTEL", True):
            set_span_attributes(mock_span, {"k": "v"})  # should not raise


# ---------------------------------------------------------------------------
# Integration: spans in query path
# ---------------------------------------------------------------------------


class TestQueryTracing:
    """Verify that the internal client emits spans when tracing is on."""

    def test_query_creates_span(self) -> None:
        """process_query wraps execution in a claude_agent_sdk.query span."""
        from claude_agent_sdk import query
        from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

        mock_tracer = _make_mock_tracer()

        async def _test() -> None:
            with patch.object(tracing_mod, "_HAS_OTEL", True):
                _force_enable_with_mock_tracer(mock_tracer)

                with patch(
                    "claude_agent_sdk._internal.client.InternalClient.process_query"
                ) as mock_pq:

                    async def mock_gen() -> Any:
                        yield AssistantMessage(
                            content=[TextBlock(text="12")],
                            model="claude-opus-4-1-20250805",
                        )
                        yield ResultMessage(
                            subtype="success",
                            duration_ms=100,
                            duration_api_ms=80,
                            is_error=False,
                            num_turns=1,
                            session_id="sess-1",
                            total_cost_usd=0.01,
                        )

                    mock_pq.return_value = mock_gen()

                    messages = []
                    async for msg in query(prompt="test"):
                        messages.append(msg)

                    assert len(messages) == 2
                    assert isinstance(messages[0], AssistantMessage)
                    assert isinstance(messages[1], ResultMessage)

        anyio.run(_test)

    def test_client_session_span_lifecycle(self) -> None:
        """ClaudeSDKClient creates a session span on connect, ends on disconnect."""
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.client import ClaudeSDKClient

        mock_tracer = _make_mock_tracer()

        async def _test() -> None:
            with patch.object(tracing_mod, "_HAS_OTEL", True):
                _force_enable_with_mock_tracer(mock_tracer)

                client = ClaudeSDKClient(options=ClaudeAgentOptions(model="test-model"))

                # Patch connect_inner and disconnect internals
                with patch.object(client, "_connect_inner", new_callable=AsyncMock):
                    await client.connect("test prompt")

                    # Session span should have been created
                    assert client._session_span is not None
                    mock_tracer.start_span.assert_called()

                    # Check span was started with session name
                    call_args = mock_tracer.start_span.call_args
                    assert call_args[0][0] == "claude_agent_sdk.session"
                    assert (
                        call_args[1]["attributes"]["claude_agent_sdk.model"]
                        == "test-model"
                    )

                    # Now disconnect
                    await client.disconnect()
                    assert client._session_span is None

        anyio.run(_test)

    def test_session_span_ends_on_failed_connect(self) -> None:
        """If connect fails, disconnect still cleans up the span."""
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.client import ClaudeSDKClient

        mock_tracer = _make_mock_tracer()

        async def _test() -> None:
            with patch.object(tracing_mod, "_HAS_OTEL", True):
                _force_enable_with_mock_tracer(mock_tracer)

                client = ClaudeSDKClient(options=ClaudeAgentOptions(model="test-model"))

                async def fail_connect(*a: Any, **kw: Any) -> None:
                    raise RuntimeError("connect failed")

                with (
                    patch.object(client, "_connect_inner", side_effect=fail_connect),
                    pytest.raises(RuntimeError, match="connect failed"),
                ):
                    await client.connect("test")

                # After the failed connect + disconnect, span should be cleaned up
                assert client._session_span is None
                assert client._session_span_ctx is None

        anyio.run(_test)
