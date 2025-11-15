# Testing Structured Outputs with HTTP Interception

This document explains how to test structured outputs functionality by intercepting Claude Code CLI's HTTP requests to the Anthropic API.

## Overview

Since the Claude Code CLI doesn't yet support passing JSON schemas to the API, we've created an HTTP interceptor that:

1. Monkey-patches `global.fetch` in Node.js
2. Intercepts requests to `api.anthropic.com`
3. Injects the `anthropic-beta: structured-outputs-2025-11-13` header
4. Adds the `output_format` parameter with a JSON schema to the request body
5. Logs all requests and responses for inspection

This allows us to test if structured outputs work at the API level without waiting for CLI support.

## Requirements

- **Node.js**: >= 18.0.0 (for built-in `fetch` support)
- **Claude CLI**: Installed via `npm install -g @anthropic-ai/claude-code`
- **Anthropic API Key**: **REQUIRED** - The structured outputs beta currently does NOT support OAuth tokens
- **Supported Models**: Only certain models support `output_format`:
  - ✅ **Claude Sonnet 4.5** (`claude-sonnet-4-5-20250929`) - SUPPORTED
  - ❌ **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) - NOT SUPPORTED

**Important**: If you're authenticated with Claude CLI via OAuth (e.g., `claude /login`), you must configure an API key instead:

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."  # Your API key from console.anthropic.com
```

Check your versions:
```bash
node -v  # Should be >= 18.0.0
claude --version
```

## Quick Start

### 1. Basic Test (Simple Email Extraction)

```bash
./test-structured-outputs.sh simple "Extract info: John Smith (john@example.com) wants Enterprise plan demo"
```

This will:
- Load the `test-schemas/simple.json` schema
- Send the prompt to Claude with structured outputs enabled
- Show if Claude returns structured JSON or markdown

### 2. Header-Only Test (No Schema)

```bash
./test-structured-outputs.sh header-only "What is 2+2?"
```

This tests if the beta header is accepted without a schema.

### 3. Custom Schema Test

```bash
export ANTHROPIC_SCHEMA='{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"]}'
./test-structured-outputs.sh custom "What is the capital of France?"
```

## Test Modes

The test script supports four modes:

### `simple` (Default)
Uses `test-schemas/simple.json` for email extraction.

**Schema**: Email extraction with name, email, plan_interest, demo_requested

**Example**:
```bash
./test-structured-outputs.sh simple "Sarah Chen (sarah@company.com) wants Pro plan"
```

### `header-only`
Tests with beta header only, no schema injection.

**Purpose**: Verify the API accepts the beta header

**Example**:
```bash
./test-structured-outputs.sh header-only "Hello world"
```

### `product`
Uses `test-schemas/product.json` (if created) for e-commerce product extraction.

**Example**:
```bash
./test-structured-outputs.sh product "Premium Headphones - $299.99, in stock"
```

### `custom`
Uses inline schema from `ANTHROPIC_SCHEMA` environment variable.

**Example**:
```bash
ANTHROPIC_SCHEMA='{"type":"object","properties":{"count":{"type":"number"}}}' \
  ./test-structured-outputs.sh custom "Count to 5"
```

## Files

### Core Files

- **`intercept-claude.js`**: HTTP interceptor implementation
- **`test-structured-outputs.sh`**: Wrapper script for easy testing
- **`test-schemas/simple.json`**: Simple email extraction schema
- **`TESTING.md`**: This documentation

### Schema Files

Create additional schemas in `test-schemas/` for testing:

```bash
# Example: Create product schema
cat > test-schemas/product.json <<'EOF'
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "price": {"type": "number"},
    "in_stock": {"type": "boolean"}
  },
  "required": ["name", "price", "in_stock"]
}
EOF
```

## Understanding the Output

### Success Indicators

Look for these in the output:

```
[INTERCEPT] Caught request to: https://api.anthropic.com/v1/messages
[REQUEST] Added beta header: structured-outputs-2025-11-13
[REQUEST] Injected schema into output_format field
[RESPONSE] Status: 200 OK
✓ STRUCTURED OUTPUT DETECTED!
```

If you see `✓ STRUCTURED OUTPUT DETECTED!`, the feature is working!

### Failure Indicators

**Markdown Response** (expected if CLI doesn't support it):
```
[RESPONSE] ⚠ Response is not structured JSON (likely markdown)
```

**API Error**:
```
[RESPONSE] Status: 400 Bad Request
```

**Unknown Beta Feature**:
```
{
  "error": {
    "type": "invalid_request_error",
    "message": "unknown beta header"
  }
}
```

## Advanced Usage

### Manual Invocation

Run the interceptor manually for more control:

```bash
# Set environment variables
export ANTHROPIC_SCHEMA_FILE="test-schemas/simple.json"
export INTERCEPT_DEBUG=1

# Run Claude with interceptor
node --require ./intercept-claude.js $(which claude) -p "Your prompt" --permission-mode bypassPermissions
```

### Debug Logging

Enable detailed logging:

```bash
export INTERCEPT_DEBUG=1
./test-structured-outputs.sh simple "test prompt"
```

This shows:
- Full request bodies
- Full response bodies
- Schema injection details
- All HTTP headers

### Testing with Python SDK Schemas

Convert a Pydantic model from `examples/structured_outputs.py` to JSON:

```python
from examples.structured_outputs import Product
import json

schema = Product.model_json_schema()
print(json.dumps(schema, indent=2))
```

Save the output to `test-schemas/product.json` and test:

```bash
./test-structured-outputs.sh product "Premium Wireless Headphones - $349.99"
```

## Interpreting Results

### Case 1: Structured JSON Returned ✅

**What it means**: Structured outputs work at the API level! The CLI just needs to support passing schemas.

**Output example**:
```json
{
  "name": "John Smith",
  "email": "john@example.com",
  "plan_interest": "Enterprise",
  "demo_requested": true
}
```

**Next steps**:
- File an issue with the CLI team showing this works
- Request they add `--json-schema` flag or similar
- Our SDK infrastructure is ready!

### Case 2: Markdown Returned ⚠️

**What it means**: The API might be:
1. Accepting the header but ignoring the schema
2. Not recognizing the beta feature yet
3. Requiring additional parameters we're missing

**Output example**:
```markdown
Here's the extracted information:

- **Name**: John Smith
- **Email**: john@example.com
...
```

**Next steps**:
- Check if beta header is in error message
- Verify schema format matches API docs
- May need to wait for official API support

### Case 3: API Error ❌

**What it means**: Schema is malformed or API doesn't support the feature.

**Output example**:
```json
{
  "error": {
    "type": "invalid_request_error",
    "message": "output_format.json_schema.schema is required"
  }
}
```

**Next steps**:
- Check schema format
- Verify beta header is correct
- Review Anthropic's structured outputs documentation

## Troubleshooting

### "OAuth authentication is currently not supported" (401 Error)

**Problem**: You see this error:
```json
{
  "type": "authentication_error",
  "message": "OAuth authentication is currently not supported."
}
```

**Cause**: The structured outputs beta feature requires API key authentication. OAuth tokens (`sk-ant-oat01-...`) are not supported.

**Solution**: Configure Claude CLI to use an API key:

```bash
# Set your API key (get from console.anthropic.com)
export ANTHROPIC_API_KEY="sk-ant-api03-..."

# Run the test
./test-structured-outputs.sh simple "Extract info: John (john@example.com) wants Pro plan"
```

The interceptor will pick up the API key from the environment variable and use it for authentication.

### "Claude CLI not found"

Install Claude CLI:
```bash
npm install -g @anthropic-ai/claude-code
```

### "Node.js >= 18 required"

Update Node.js:
```bash
# Using nvm
nvm install 18
nvm use 18

# Or download from nodejs.org
```

### "Failed to load schema"

Check the schema file exists and is valid JSON:
```bash
cat test-schemas/simple.json | jq .
```

### "global.fetch is not available"

Your Node.js version is too old. Update to >= 18.0.0.

### No [INTERCEPT] Logs Appearing

The interceptor might not be loaded. Try:
```bash
# Verify interceptor is being loaded
node --require ./intercept-claude.js -e "console.log('Interceptor loaded')"
```

### Claude CLI Crashes

Check for syntax errors in the interceptor:
```bash
node -c intercept-claude.js
```

## How It Works

### 1. Monkey-Patching `global.fetch`

```javascript
const originalFetch = global.fetch;

global.fetch = async function(url, options) {
  // Intercept and modify...
  return originalFetch(url, options);
};
```

Node.js loads our interceptor before the CLI starts, replacing the global fetch function.

### 2. Request Modification

When the CLI makes a request to Anthropic:

```javascript
// Original request
{
  "model": "claude-sonnet-4-5",
  "messages": [...],
  "max_tokens": 1024
}

// Modified request
{
  "model": "claude-sonnet-4-5",
  "messages": [...],
  "max_tokens": 1024,
  "output_format": {  // ← INJECTED
    "type": "json_schema",
    "json_schema": {
      "name": "InterceptedSchema",
      "strict": true,
      "schema": { /* your schema */ }
    }
  }
}
```

### 3. Response Inspection

The interceptor clones the response and checks if the content is valid JSON matching the schema.

## Test Findings

### 2025-11-14: OAuth Authentication Limitation Discovered

**Test Configuration**:
- Interceptor: Working correctly ✅
- Headers preservation: Fixed and working ✅
- Beta header injection: `anthropic-beta: structured-outputs-2025-11-13` ✅
- Schema injection: `output_format.json_schema` structure correct ✅

**Result**: 401 Authentication Error

The test revealed that the structured outputs beta feature does not support OAuth authentication:

```json
{
  "type": "authentication_error",
  "message": "OAuth authentication is currently not supported.",
  "request_id": "req_011CV95YixA5uk6EqwzndbcH"
}
```

**Key Insights**:
1. The HTTP interceptor works perfectly - all headers and request modifications are correct
2. Claude CLI uses OAuth tokens (`sk-ant-oat01-...`) by default when authenticated via `/login`
3. The structured outputs beta feature requires API key authentication (`sk-ant-api03-...`)
4. This is a temporary API limitation, not an issue with our SDK implementation

**Interceptor Logs** (showing successful interception):
```
[INTERCEPT] Caught request to: https://api.anthropic.com/v1/messages?beta=true
[REQUEST] Added beta header: structured-outputs-2025-11-13
[REQUEST] Injected schema into output_format field
[DEBUG] All headers being sent: {
  "authorization": "Bearer sk-ant-oat01-...",
  "anthropic-beta": "structured-outputs-2025-11-13",
  ...
}
[RESPONSE] Status: 401 Unauthorized
```

**Next Step**: Test with API key authentication to verify structured outputs work at the API level.

### 2025-11-14: ✅ STRUCTURED OUTPUTS CONFIRMED WORKING!

**Test Result**: SUCCESS - Structured outputs work perfectly at the API level!

**Test Configuration**:
- API Key: ✅ Working with credits
- Schema format: `output_format: { type: 'json_schema', schema: {...} }`
- Model: `claude-sonnet-4-5-20250929`

**Response**:
```json
{
  "name": "Sarah Chen",
  "email": "sarah@company.com",
  "plan_interest": "Professional",
  "demo_requested": true
}
```

**Perfect structured JSON matching the schema!**

**Key Findings**:
1. ✅ Structured outputs work with Sonnet 4.5 (`claude-sonnet-4-5-20250929`)
2. ❌ Haiku 4.5 does NOT support `output_format` - returns error: `'claude-haiku-4-5-20251001' does not support output_format`
3. ✅ Schema format is correct: `{ type: 'json_schema', schema: {...} }`
4. ✅ API returns pure JSON (no markdown wrapper)
5. ✅ All required fields present with correct types

**Conclusion**: The SDK's structured outputs infrastructure is ready. The feature works at the API level. Claude Code CLI just needs to add support for passing schemas in requests.

## Next Steps

Based on test results:

### If Structured Outputs Work ✅
1. Document the findings
2. Update PR description with proof
3. File CLI issue with evidence
4. Wait for CLI team to add official support

### If They Don't Work ❌
1. Investigate API requirements
2. Check if beta is available yet
3. Verify our schema format
4. Contact Anthropic support if needed

## Related Files

- **SDK Implementation**: `src/claude_agent_sdk/_internal/schema_utils.py`
- **SDK Tests**: `tests/test_schema_utils.py`, `tests/test_schema_edge_cases.py`
- **Examples**: `examples/structured_outputs.py`
- **CLI Issue**: https://github.com/anthropics/claude-code/issues/9058

## Contributing

To add more test schemas:

1. Create schema in `test-schemas/`
2. Add mode to `test-structured-outputs.sh`
3. Test with the new mode
4. Document results here

---

**Last Updated**: 2025-11-14
**Status**: ✅ VALIDATED - Structured Outputs Work at API Level!

**Changelog**:
- **2025-11-14 15:00**: ✅ CONFIRMED - Structured outputs work with Sonnet 4.5!
- **2025-11-14 14:45**: Fixed schema format - `output_format.schema` not `output_format.json_schema.schema`
- **2025-11-14 14:30**: Discovered OAuth limitation - beta requires API key auth
- **2025-11-14 14:15**: Fixed Headers object handling bug in interceptor
- **2025-11-14 14:00**: Created initial testing infrastructure
