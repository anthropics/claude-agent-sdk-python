"""OpenTelemetry tracing integration for Claude Agent SDK.

This module provides opt-in distributed tracing for the SDK's key lifecycle
events.  When ``opentelemetry-api`` is installed and tracing has been enabled
(via :func:`enable_tracing` or by passing ``trace=True`` to
:class:`~claude_agent_sdk.ClaudeAgentOptions`), the SDK emits spans for:

* **claude_agent_sdk.session** -- the top-level span covering an entire
  ``ClaudeSDKClient`` session (connect -> disconnect).
* **claude_agent_sdk.query** -- one-shot ``query()`` calls.
* **claude_agent_sdk.tool_call** -- SDK MCP tool invocations routed through
  the control protocol.
* **claude_agent_sdk.tool_permission** -- ``can_use_tool`` permission callback
  invocations.
* **claude_agent_sdk.message** -- each message received from the CLI
  subprocess (assistant, result, system, ...).

All tracing is best-effort: failures in the tracing layer are logged at
DEBUG level and never propagate to the caller.  When ``opentelemetry-api``
is not installed the public helpers are harmless no-ops.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional opentelemetry-api import
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import (
        Span,
        StatusCode,
        Tracer,
    )

    _HAS_OTEL = True
except ImportError:  # pragma: no cover – tested via mock
    _HAS_OTEL = False

    # Minimal stand-ins so the rest of this module type-checks without
    # opentelemetry-api installed.
    class Span:  # type: ignore[no-redef]
        """No-op span stub."""

    class StatusCode:  # type: ignore[no-redef]
        OK = "OK"
        ERROR = "ERROR"
        UNSET = "UNSET"

    class Tracer:  # type: ignore[no-redef]
        """No-op tracer stub."""

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_TRACER_NAME = "claude_agent_sdk"
_enabled: bool = False
_tracer: Tracer | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enable_tracing(
    *,
    tracer_name: str = "claude_agent_sdk",
) -> None:
    """Enable OpenTelemetry tracing for the Claude Agent SDK.

    Call this once during application startup, **after** you have configured
    the OpenTelemetry SDK (e.g. set up a ``TracerProvider`` with an exporter).

    Args:
        tracer_name: Name passed to ``opentelemetry.trace.get_tracer()``.
            Defaults to ``"claude_agent_sdk"``.

    Raises:
        RuntimeError: If ``opentelemetry-api`` is not installed.  Install
            the SDK with the ``[otel]`` extra to pull it in::

                pip install claude-agent-sdk[otel]
    """
    if not _HAS_OTEL:
        raise RuntimeError(
            "opentelemetry-api is not installed. "
            "Install claude-agent-sdk with the [otel] extra: "
            "pip install claude-agent-sdk[otel]"
        )
    global _enabled, _tracer, _TRACER_NAME  # noqa: PLW0603
    _TRACER_NAME = tracer_name
    _tracer = otel_trace.get_tracer(tracer_name)
    _enabled = True


def disable_tracing() -> None:
    """Disable OpenTelemetry tracing.

    Useful in tests or when you want to stop emitting spans at runtime.
    """
    global _enabled, _tracer  # noqa: PLW0603
    _enabled = False
    _tracer = None


def is_tracing_enabled() -> bool:
    """Return whether tracing is currently active."""
    return _enabled and _tracer is not None


# ---------------------------------------------------------------------------
# Internal helpers -- called from client / query code
# ---------------------------------------------------------------------------


def _get_tracer() -> Tracer | None:
    """Return the active tracer, or *None* when tracing is off."""
    if not _enabled:
        return None
    return _tracer


@contextmanager
def start_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span | None]:
    """Context manager that starts an OTel span when tracing is enabled.

    When tracing is disabled (or opentelemetry-api is absent) the context
    manager yields ``None`` and does nothing.

    The span is automatically ended when the block exits.  If the block
    raises, the span records the exception and sets ``StatusCode.ERROR``.

    Args:
        name: Span name -- conventionally ``"claude_agent_sdk.<operation>"``.
        attributes: Optional dict of span attributes set at creation time.
    """
    tracer = _get_tracer()
    if tracer is None or not _HAS_OTEL:
        yield None
        return

    try:
        span = tracer.start_span(name, attributes=attributes or {})
    except Exception:
        logger.debug("Failed to start span %r", name, exc_info=True)
        yield None
        return

    try:
        yield span
    except BaseException as exc:
        try:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
        except Exception:
            logger.debug("Failed to record exception on span", exc_info=True)
        raise
    else:
        try:
            span.set_status(StatusCode.OK)
        except Exception:
            logger.debug("Failed to set span status", exc_info=True)
    finally:
        try:
            span.end()
        except Exception:
            logger.debug("Failed to end span", exc_info=True)


def record_span_event(
    span: Span | None,
    name: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Add an event to an active span (no-op when span is None)."""
    if span is None or not _HAS_OTEL:
        return
    try:
        span.add_event(name, attributes=attributes or {})
    except Exception:
        logger.debug("Failed to add event to span", exc_info=True)


def set_span_attributes(
    span: Span | None,
    attributes: dict[str, Any],
) -> None:
    """Set attributes on an active span (no-op when span is None)."""
    if span is None or not _HAS_OTEL:
        return
    try:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    except Exception:
        logger.debug("Failed to set span attributes", exc_info=True)
