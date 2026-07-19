"""Helper utilities for OpenTelemetry trace context propagation."""

from typing import Any


def inject_trace_into_message(message: dict[str, Any]) -> None:
    """Inject ambient OTel trace context into a message dict for the CLI.

    Best-effort: no-op if opentelemetry-api is not installed or there's no
    active span. The CLI reads these optional fields to stamp outbound MCP/tool
    spans under the caller's current trace rather than the spawn-time trace.
    """
    try:
        from opentelemetry import propagate

        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        if "traceparent" in carrier:
            message["traceparent"] = carrier["traceparent"]
            if "tracestate" in carrier:
                message["tracestate"] = carrier["tracestate"]
    except Exception:
        pass
