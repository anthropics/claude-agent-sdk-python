#!/usr/bin/env python3
"""Example: OpenTelemetry tracing with the Claude Agent SDK.

This example shows how to wire up distributed tracing so every SDK
call -- session start, message, tool invocation -- appears as a span
in your observability backend (Jaeger, Zipkin, OTLP-compatible, ...).

Prerequisites
-------------
Install the SDK with the ``[otel]`` extra and a span exporter::

    pip install claude-agent-sdk[otel] \
        opentelemetry-sdk \
        opentelemetry-exporter-otlp-proto-grpc

Then run a local Jaeger instance (the all-in-one Docker image is the
fastest way to get a collector + UI)::

    docker run -d --name jaeger \
        -p 16686:16686 \
        -p 4317:4317 \
        jaegertracing/all-in-one:latest

Finally, run this script::

    python examples/otel_tracing.py

Open http://localhost:16686 to browse the traces in the Jaeger UI.
"""

import anyio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    enable_tracing,
    query,
)


def setup_otel() -> None:
    """Configure an OpenTelemetry TracerProvider with an OTLP exporter."""
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": "claude-agent-sdk-example"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


async def traced_query() -> None:
    """Run a simple query with tracing enabled."""
    print("Running a traced query...")

    options = ClaudeAgentOptions(max_turns=1)
    async for message in query(
        prompt="What is the square root of 144? Answer in one sentence.",
        options=options,
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")
        elif isinstance(message, ResultMessage):
            print(f"Turns: {message.num_turns}, Cost: ${message.total_cost_usd:.4f}")

    print("\nDone. Check Jaeger UI at http://localhost:16686")


def main() -> None:
    # 1. Configure the OTel SDK (provider, exporter, processor).
    setup_otel()

    # 2. Tell the Claude Agent SDK to start emitting spans.
    enable_tracing()

    # 3. Run the traced query.
    anyio.run(traced_query)


if __name__ == "__main__":
    main()
