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

Install the development dependencies:

```bash
pip install -e ".[dev]"
```

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