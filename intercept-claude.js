#!/usr/bin/env node
/**
 * HTTP Request Interceptor for Claude Code CLI
 *
 * This script monkey-patches global.fetch to intercept Anthropic API requests
 * and inject the structured outputs beta header along with a JSON schema.
 *
 * Usage:
 *   node --require ./intercept-claude.js $(which claude) -p "Your prompt"
 *
 * Environment Variables:
 *   ANTHROPIC_SCHEMA_FILE - Path to JSON schema file (default: test-schemas/simple.json)
 *   ANTHROPIC_SCHEMA - Inline JSON schema as string
 *   INTERCEPT_DEBUG - Enable verbose debug logging (1 or true)
 */

const fs = require('fs');
const path = require('path');

// Configuration
const DEBUG = process.env.INTERCEPT_DEBUG === '1' || process.env.INTERCEPT_DEBUG === 'true';
const SCHEMA_FILE = process.env.ANTHROPIC_SCHEMA_FILE || path.join(__dirname, 'test-schemas', 'simple.json');
const INLINE_SCHEMA = process.env.ANTHROPIC_SCHEMA;

// ANSI color codes for pretty logging
const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
  red: '\x1b[31m',
};

function log(category, message, data) {
  const timestamp = new Date().toISOString();
  const categoryColors = {
    INIT: colors.cyan,
    INTERCEPT: colors.yellow,
    REQUEST: colors.blue,
    RESPONSE: colors.green,
    ERROR: colors.red,
    DEBUG: colors.magenta,
  };

  const color = categoryColors[category] || colors.reset;
  console.error(`${color}[${category}]${colors.reset} ${message}`);

  if (data && DEBUG) {
    console.error(JSON.stringify(data, null, 2));
  }
}

// Load schema from file or environment
function loadSchema() {
  try {
    if (INLINE_SCHEMA) {
      log('INIT', 'Loading schema from ANTHROPIC_SCHEMA environment variable');
      return JSON.parse(INLINE_SCHEMA);
    }

    if (fs.existsSync(SCHEMA_FILE)) {
      log('INIT', `Loading schema from file: ${SCHEMA_FILE}`);
      const schemaContent = fs.readFileSync(SCHEMA_FILE, 'utf8');
      return JSON.parse(schemaContent);
    }

    log('INIT', 'No schema found, interception will only add beta header');
    return null;
  } catch (error) {
    log('ERROR', `Failed to load schema: ${error.message}`);
    return null;
  }
}

const schema = loadSchema();

// Store the original fetch function
const originalFetch = global.fetch;

if (!originalFetch) {
  log('ERROR', 'global.fetch is not available! Node.js version might be too old (requires 18+)');
  process.exit(1);
}

log('INIT', 'Interceptor initialized successfully');
log('INIT', `Debug mode: ${DEBUG ? 'ENABLED' : 'DISABLED'}`);
log('INIT', `Schema loaded: ${schema ? 'YES' : 'NO'}`);

// Monkey-patch global.fetch
global.fetch = async function (url, options) {
  const urlString = url.toString();

  // Only intercept Anthropic API requests
  if (!urlString.includes('api.anthropic.com')) {
    return originalFetch(url, options);
  }

  log('INTERCEPT', `Caught request to: ${urlString}`);

  // Clone options to avoid modifying the original
  options = options || {};
  const modifiedOptions = { ...options };

  // Properly handle headers (could be Headers object or plain object)
  const originalHeaders = options.headers || {};
  const headersObj = {};

  if (originalHeaders instanceof Headers) {
    // Headers object - iterate using entries()
    for (const [key, value] of originalHeaders.entries()) {
      headersObj[key] = value;
    }
  } else {
    // Plain object - just copy
    Object.assign(headersObj, originalHeaders);
  }

  modifiedOptions.headers = headersObj;

  // Add the structured outputs beta header
  modifiedOptions.headers['anthropic-beta'] = 'structured-outputs-2025-11-13';
  log('REQUEST', 'Added beta header: structured-outputs-2025-11-13');

  if (DEBUG) {
    log('DEBUG', 'All headers being sent:', modifiedOptions.headers);
  }

  // Inject schema into request body if available
  // Only inject for /messages endpoint, not for count_tokens or other endpoints
  const isMessagesEndpoint = urlString.includes('/v1/messages') &&
                              !urlString.includes('/count_tokens');

  if (schema && options.body && isMessagesEndpoint) {
    try {
      const originalBody = JSON.parse(options.body);
      log('REQUEST', 'Original request body:', originalBody);

      // Add output_format to the request
      const modifiedBody = {
        ...originalBody,
        output_format: {
          type: 'json_schema',
          schema: schema,
        },
      };

      modifiedOptions.body = JSON.stringify(modifiedBody);
      log('REQUEST', 'Injected schema into output_format field');
      log('DEBUG', 'Modified request body:', modifiedBody);
    } catch (error) {
      log('ERROR', `Failed to modify request body: ${error.message}`);
    }
  } else if (schema && !isMessagesEndpoint) {
    log('DEBUG', 'Skipping schema injection for non-messages endpoint');
  }

  // Make the actual request
  log('REQUEST', 'Sending modified request to Anthropic API...');
  const response = await originalFetch(url, modifiedOptions);

  // Log response details
  log('RESPONSE', `Status: ${response.status} ${response.statusText}`);

  // Clone the response so we can read it
  const clonedResponse = response.clone();

  try {
    const responseText = await clonedResponse.text();

    // Try to parse as JSON
    try {
      const responseJson = JSON.parse(responseText);
      log('RESPONSE', 'Response body (JSON):', responseJson);

      // Check if we got structured output
      if (responseJson.content && responseJson.content[0] && responseJson.content[0].text) {
        const contentText = responseJson.content[0].text;
        try {
          const parsedContent = JSON.parse(contentText);
          log('RESPONSE', `${colors.green}${colors.bright}✓ STRUCTURED OUTPUT DETECTED!${colors.reset}`);
          log('RESPONSE', 'Parsed structured content:', parsedContent);
        } catch {
          log('RESPONSE', `${colors.yellow}⚠ Response is not structured JSON (likely markdown)${colors.reset}`);
          if (DEBUG) {
            log('DEBUG', 'Response text:', contentText.substring(0, 200) + '...');
          }
        }
      }
    } catch {
      log('RESPONSE', 'Response body (text):', responseText.substring(0, 500));
    }
  } catch (error) {
    log('ERROR', `Failed to read response: ${error.message}`);
  }

  return response;
};

log('INIT', `${colors.bright}${colors.green}✓ Interceptor ready! Waiting for Claude CLI to make API requests...${colors.reset}`);
