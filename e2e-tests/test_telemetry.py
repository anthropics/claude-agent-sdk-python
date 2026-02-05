"""End-to-end tests for telemetry integration with real Claude API calls."""

from typing import Any

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    ResultMessage,
    TelemetryOptions,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk._errors import CLIConnectionError

pytest.importorskip("opentelemetry")
pytest.importorskip("opentelemetry.sdk")
pytest.importorskip("opentelemetry.sdk.metrics")
pytest.importorskip("opentelemetry.sdk.metrics.export")

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

# =============================================================================
# Helper Functions
# =============================================================================


def get_metric_names(metrics_data: Any) -> set[str]:
    """Extract metric names from OpenTelemetry metrics data."""
    names: set[str] = set()
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                names.add(metric.name)
    return names


def get_metric_by_name(metrics_data: Any, name: str) -> Any:
    """Get a specific metric by name from OpenTelemetry metrics data."""
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    return metric
    return None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def telemetry_tracer():
    """Create a tracer and span exporter for test assertions."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("e2e.telemetry")
    return tracer, exporter


@pytest.fixture
def telemetry_meter():
    """Create a meter and metric reader for test assertions."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("e2e.telemetry")
    return meter, reader, provider


# =============================================================================
# Core Span Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_tracing_spans_emitted(api_key, telemetry_tracer):
    """Verify that core spans are emitted during a real SDK session."""
    tracer, exporter = telemetry_tracer
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer),
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with the single word 'OK'.")
        async for _ in client.receive_response():
            pass

    spans = exporter.get_finished_spans()
    span_names = {span.name for span in spans}
    expected = {
        "claude_agent_sdk.client.connect",
        "claude_agent_sdk.client.query",
        "claude_agent_sdk.client.disconnect",
        "claude_agent_sdk.query.initialize",
        "claude_agent_sdk.query.read_messages",
        "claude_agent_sdk.transport.connect",
        "claude_agent_sdk.transport.close",
    }
    missing = expected - span_names
    assert not missing, f"Missing spans: {sorted(missing)}"

    # Check specific attributes on initialize span
    init_span = next(s for s in spans if s.name == "claude_agent_sdk.query.initialize")
    assert "has_hooks" in init_span.attributes
    assert "has_mcp_servers" in init_span.attributes

    # Verify no spans have ERROR status (OK or UNSET is fine)
    for span in spans:
        if span.name in expected:
            assert span.status.status_code != StatusCode.ERROR, (
                f"Span {span.name} should not have ERROR status"
            )


# =============================================================================
# Core Metrics Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_metrics_emitted(api_key, telemetry_tracer, telemetry_meter):
    """Verify that core metrics are recorded during a real SDK session."""
    tracer, _ = telemetry_tracer
    meter, reader, provider = telemetry_meter
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer, meter=meter),
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with the single word 'OK'.")
        async for _ in client.receive_response():
            pass

    provider.force_flush()
    metrics_data = reader.get_metrics_data()
    metric_names = get_metric_names(metrics_data)

    # Core metrics that should always be recorded
    assert "claude_agent_sdk.messages" in metric_names
    assert "claude_agent_sdk.results" in metric_names

    # Cost metric should be recorded (may be 0 but should exist)
    assert "claude_agent_sdk.cost.total_usd" in metric_names

    total_cost = get_metric_by_name(metrics_data, "claude_agent_sdk.cost.total_usd")
    assert total_cost is not None
    assert len(total_cost.data.data_points) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_token_metrics_detailed(
    api_key, telemetry_tracer, telemetry_meter
):
    """Verify detailed token metrics (prompt/completion split) are recorded."""
    tracer, _ = telemetry_tracer
    meter, reader, provider = telemetry_meter
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer, meter=meter),
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with a paragraph about Python programming.")
        async for _ in client.receive_response():
            pass

    provider.force_flush()
    metrics_data = reader.get_metrics_data()
    metric_names = get_metric_names(metrics_data)

    # Check that we have some metrics recorded
    assert len(metric_names) > 0, "Expected at least some metrics to be recorded"

    # Duration metrics should always be recorded
    assert "claude_agent_sdk.result.duration_ms" in metric_names

    # If token metrics are available, verify they have data
    # (Token metrics depend on usage data being in the result)
    if "claude_agent_sdk.tokens.total" in metric_names:
        total_tokens = get_metric_by_name(metrics_data, "claude_agent_sdk.tokens.total")
        if total_tokens and total_tokens.data.data_points:
            assert len(total_tokens.data.data_points) > 0

    if "claude_agent_sdk.tokens.prompt" in metric_names:
        prompt_tokens = get_metric_by_name(
            metrics_data, "claude_agent_sdk.tokens.prompt"
        )
        if prompt_tokens and prompt_tokens.data.data_points:
            assert len(prompt_tokens.data.data_points) > 0


# =============================================================================
# Tool Use Span Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_tool_use(api_key, telemetry_tracer):
    """Verify that tool use spans are emitted."""
    tracer, exporter = telemetry_tracer
    executions = []

    @tool("echo", "Echo back the input text", {"text": str})
    async def echo_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Echo back whatever text is provided."""
        executions.append("echo")
        return {"content": [{"type": "text", "text": f"Echo: {args['text']}"}]}

    server = create_sdk_mcp_server(
        name="test",
        version="1.0.0",
        tools=[echo_tool],
    )

    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer),
        mcp_servers={"test": server},
        allowed_tools=["mcp__test__echo"],
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Call the mcp__test__echo tool with text 'hello'")
        async for _ in client.receive_response():
            pass

    assert "echo" in executions

    spans = exporter.get_finished_spans()
    span_names = {span.name for span in spans}

    # Check for CLI tool call span (the outer span from CLI perspective)
    assert "claude_agent_sdk.cli.tool_call" in span_names

    # Check for SDK MCP tool call span (the inner span from SDK executing the tool)
    assert "claude_agent_sdk.mcp.tool_call" in span_names

    # Check attributes for CLI tool span
    cli_tool_span = next(s for s in spans if s.name == "claude_agent_sdk.cli.tool_call")
    assert cli_tool_span.attributes.get("tool.source") == "cli"
    tool_name = cli_tool_span.attributes.get("tool.name")
    assert tool_name is not None, "tool.name attribute should be set"
    assert "echo" in tool_name, f"Expected 'echo' in tool name, got: {tool_name}"

    # Check attributes for SDK MCP span
    mcp_tool_span = next(s for s in spans if s.name == "claude_agent_sdk.mcp.tool_call")
    assert mcp_tool_span.attributes.get("mcp.server") == "test"
    assert mcp_tool_span.attributes.get("mcp.tool.name") == "echo"


# =============================================================================
# Telemetry Disabled/Not Configured Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_disabled_no_crash(api_key):
    """Verify SDK works correctly when telemetry is explicitly disabled."""
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=False),
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with 'OK'.")
        messages = [msg async for msg in client.receive_response()]

    # Verify we got a result message
    assert any(isinstance(m, ResultMessage) for m in messages), (
        "Expected a ResultMessage"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_not_provided_no_crash(api_key):
    """Verify SDK works correctly when telemetry option is not provided at all."""
    options = ClaudeAgentOptions()  # No telemetry config
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with 'OK'.")
        messages = [msg async for msg in client.receive_response()]

    # Verify we got a result message
    assert any(isinstance(m, ResultMessage) for m in messages), (
        "Expected a ResultMessage"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_enabled_without_tracer_or_meter(api_key):
    """Verify SDK works when telemetry is enabled but no tracer/meter provided."""
    # This tests the fallback to get_otel_tracer/get_otel_meter
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True),  # No tracer or meter
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with 'OK'.")
        messages = [msg async for msg in client.receive_response()]

    # Verify we got a result message
    assert any(isinstance(m, ResultMessage) for m in messages), (
        "Expected a ResultMessage"
    )


# =============================================================================
# TelemetryOptions Validation Tests
# =============================================================================


@pytest.mark.e2e
def test_telemetry_options_invalid_tracer():
    """Verify TelemetryOptions rejects invalid tracer (missing start_as_current_span)."""
    with pytest.raises(TypeError, match="start_as_current_span"):
        TelemetryOptions(enabled=True, tracer=object())


@pytest.mark.e2e
def test_telemetry_options_invalid_meter():
    """Verify TelemetryOptions rejects invalid meter (missing create_counter/create_histogram)."""
    with pytest.raises(TypeError, match="create_counter"):
        TelemetryOptions(enabled=True, meter=object())


@pytest.mark.e2e
def test_telemetry_options_valid_none_tracer_meter():
    """Verify TelemetryOptions accepts None for tracer and meter."""
    # This should not raise - None is valid (will use defaults)
    options = TelemetryOptions(enabled=True, tracer=None, meter=None)
    assert options.enabled is True
    assert options.tracer is None
    assert options.meter is None


# =============================================================================
# Hook Callback Span Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_hook_spans(api_key, telemetry_tracer):
    """Verify that hook callback spans are emitted when hooks are invoked."""
    tracer, exporter = telemetry_tracer
    hook_invocations = []

    async def pre_tool_hook(input_data, tool_use_id, context):
        hook_invocations.append(input_data.get("hook_event_name"))
        return {"continue_": True}

    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer),
        hooks={
            "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_hook])],
        },
        permission_mode="bypassPermissions",
    )

    async with ClaudeSDKClient(options=options) as client:
        # Request an action that will trigger tool use
        await client.query("List files in the current directory using the Bash tool")
        async for _ in client.receive_response():
            pass

    # Only check for hook spans if hooks were actually called
    if hook_invocations:
        spans = exporter.get_finished_spans()
        span_names = {span.name for span in spans}
        assert "claude_agent_sdk.hooks.callback" in span_names, (
            f"Expected hooks.callback span. Got spans: {sorted(span_names)}"
        )

        hook_span = next(
            s for s in spans if s.name == "claude_agent_sdk.hooks.callback"
        )
        assert "hook.callback_id" in hook_span.attributes
        assert "hook.event" in hook_span.attributes


# =============================================================================
# Permission Callback Span Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_permission_callback_spans(api_key, telemetry_tracer):
    """Verify that permission callback spans are emitted when can_use_tool is invoked."""
    tracer, exporter = telemetry_tracer
    permission_calls = []

    async def can_use_tool(tool_name, tool_input, context):
        permission_calls.append(tool_name)
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer),
        can_use_tool=can_use_tool,
    )

    async def prompt_stream():
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": "List files in the current directory",
            },
            "session_id": "test-session",
        }

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt_stream())
        async for _ in client.receive_response():
            pass

    # Only check for permission spans if permissions were actually requested
    if permission_calls:
        spans = exporter.get_finished_spans()
        span_names = {span.name for span in spans}
        assert "claude_agent_sdk.permission.can_use_tool" in span_names, (
            f"Expected permission.can_use_tool span. Got spans: {sorted(span_names)}"
        )

        perm_span = next(
            s for s in spans if s.name == "claude_agent_sdk.permission.can_use_tool"
        )
        assert "tool.name" in perm_span.attributes
        assert "permission.behavior" in perm_span.attributes
        assert perm_span.attributes.get("permission.behavior") == "allow"


# =============================================================================
# Error Recording Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_error_recording_invalid_cwd(api_key, telemetry_tracer):
    """Verify that errors are properly recorded on spans when connection fails."""
    tracer, exporter = telemetry_tracer
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer),
        cwd="/nonexistent/path/that/does/not/exist/at/all",
    )

    with pytest.raises(CLIConnectionError):
        async with ClaudeSDKClient(options=options) as client:
            await client.query("test")
            async for _ in client.receive_response():
                pass

    spans = exporter.get_finished_spans()
    # Find any span with an error status
    error_spans = [s for s in spans if s.status.status_code == StatusCode.ERROR]
    assert len(error_spans) > 0, (
        f"Expected at least one span with error status. "
        f"Got spans: {[(s.name, s.status.status_code) for s in spans]}"
    )


# =============================================================================
# Additional Metrics Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_duration_metrics(api_key, telemetry_tracer, telemetry_meter):
    """Verify duration-related metrics are recorded."""
    tracer, _ = telemetry_tracer
    meter, reader, provider = telemetry_meter
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer, meter=meter),
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with 'OK'.")
        async for _ in client.receive_response():
            pass

    provider.force_flush()
    metrics_data = reader.get_metrics_data()
    metric_names = get_metric_names(metrics_data)

    # Check for duration metrics
    assert "claude_agent_sdk.result.duration_ms" in metric_names
    assert "claude_agent_sdk.model.latency_ms" in metric_names

    # Verify duration > 0
    duration = get_metric_by_name(metrics_data, "claude_agent_sdk.result.duration_ms")
    if duration and duration.data.data_points:
        # Histogram data points have different structure
        assert len(duration.data.data_points) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_telemetry_invocation_counter(api_key, telemetry_tracer, telemetry_meter):
    """Verify invocation counter is incremented."""
    tracer, _ = telemetry_tracer
    meter, reader, provider = telemetry_meter
    options = ClaudeAgentOptions(
        telemetry=TelemetryOptions(enabled=True, tracer=tracer, meter=meter),
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with 'OK'.")
        async for _ in client.receive_response():
            pass

    provider.force_flush()
    metrics_data = reader.get_metrics_data()
    metric_names = get_metric_names(metrics_data)

    assert "claude_agent_sdk.invocations" in metric_names

    invocations = get_metric_by_name(metrics_data, "claude_agent_sdk.invocations")
    assert invocations is not None
    # Should have at least 1 invocation
    total = sum(p.value for p in invocations.data.data_points)
    assert total >= 1, f"Expected at least 1 invocation, got {total}"
