# End-to-End Tests for Claude Code SDK

This directory contains end-to-end tests that run against the actual Claude API to verify real-world functionality.

## Requirements

### API Key (REQUIRED)

These tests require a valid Anthropic API key. The tests will **fail** if `ANTHROPIC_API_KEY` is not set.

Set your API key before running tests:

```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

### Dependencies

Install the development dependencies including telemetry support:

```bash
pip install -e ".[dev,telemetry]"
```

> **Note**: Telemetry tests (`test_telemetry.py`) require the `telemetry` extra. Without it, these tests will be silently skipped via `pytest.importorskip()`.

## Running the Tests

### Run all e2e tests:

```bash
python -m pytest e2e-tests/ -v
```

### Run with e2e marker only:

```bash
python -m pytest e2e-tests/ -v -m e2e
```

### Run a specific test:

```bash
python -m pytest e2e-tests/test_mcp_calculator.py::test_basic_addition -v
```

## Cost Considerations

⚠️ **Important**: These tests make actual API calls to Claude, which incur costs based on your Anthropic pricing plan.

- Each test typically uses 1-3 API calls
- Tests use simple prompts to minimize token usage
- The complete test suite should cost less than $0.10 to run

## Test Coverage

### Telemetry Tests (`test_telemetry.py`)

Tests OpenTelemetry tracing and metrics integration:

- **test_telemetry_tracing_spans_emitted**: Verifies core spans are emitted during SDK session
- **test_telemetry_metrics_emitted**: Verifies core metrics (messages, tokens, cost) are recorded
- **test_telemetry_token_metrics_detailed**: Tests prompt/completion token split metrics
- **test_telemetry_tool_use**: Validates tool use spans for both CLI and SDK MCP tools
- **test_telemetry_disabled_no_crash**: Ensures SDK works when telemetry is disabled
- **test_telemetry_not_provided_no_crash**: Ensures SDK works without telemetry config
- **test_telemetry_enabled_without_tracer_or_meter**: Tests fallback to default tracer/meter
- **test_telemetry_options_invalid_tracer/meter**: Validates type checking on TelemetryOptions
- **test_telemetry_hook_spans**: Verifies hook callback spans are emitted
- **test_telemetry_permission_callback_spans**: Verifies permission callback spans
- **test_telemetry_error_recording_invalid_cwd**: Tests error recording on spans
- **test_telemetry_duration_metrics**: Validates duration-related metrics
- **test_telemetry_invocation_counter**: Verifies invocation counter increments

### MCP Calculator Tests (`test_mcp_calculator.py`)

Tests the MCP (Model Context Protocol) integration with calculator tools:

- **test_basic_addition**: Verifies the add tool executes correctly
- **test_division**: Tests division with decimal results
- **test_square_root**: Validates square root calculations
- **test_power**: Tests exponentiation
- **test_multi_step_calculation**: Verifies multiple tools can be used in sequence
- **test_tool_permissions_enforced**: Ensures permission system works correctly

Each test validates:
1. Tools are actually called (ToolUseBlock present in response)
2. Correct tool inputs are provided
3. Expected results are returned
4. Permission system is enforced

## CI/CD Integration

These tests run automatically on:
- Pushes to `main` branch (via GitHub Actions)
- Manual workflow dispatch

The workflow uses `ANTHROPIC_API_KEY` from GitHub Secrets.

## Troubleshooting

### "ANTHROPIC_API_KEY environment variable is required" error
- Set your API key: `export ANTHROPIC_API_KEY=sk-ant-...`
- The tests will not skip - they require the key to run

### Tests timing out
- Check your API key is valid and has quota available
- Ensure network connectivity to api.anthropic.com

### Permission denied errors
- Verify the `allowed_tools` parameter includes the necessary MCP tools
- Check that tool names match the expected format (e.g., `mcp__calc__add`)

## Adding New E2E Tests

When adding new e2e tests:

1. Mark tests with `@pytest.mark.e2e` decorator
2. Use the `api_key` fixture to ensure API key is available
3. Keep prompts simple to minimize costs
4. Verify actual tool execution, not just mocked responses
5. Document any special setup requirements in this README
