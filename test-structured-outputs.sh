#!/bin/bash
#
# Test Structured Outputs with Claude CLI via HTTP Interception
#
# This script runs Claude CLI with the HTTP interceptor loaded to test
# if structured outputs work at the API level.
#
# Usage:
#   ./test-structured-outputs.sh [mode] [prompt]
#
# Modes:
#   header-only  - Test with beta header only (no schema)
#   simple       - Test with simple email extraction schema (default)
#   product      - Test with product schema from examples
#   custom       - Use custom schema from ANTHROPIC_SCHEMA env var
#
# Examples:
#   ./test-structured-outputs.sh simple "Extract info: John (john@example.com) wants Enterprise demo"
#   ./test-structured-outputs.sh header-only "What is 2+2?"
#   ANTHROPIC_SCHEMA='{"type":"object","properties":{"answer":{"type":"string"}}}' ./test-structured-outputs.sh custom "Answer: 4"
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Configuration
MODE="${1:-simple}"
PROMPT="${2:-Extract info: Sarah Chen (sarah@company.com) wants Professional plan, requested demo}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERCEPTOR="$SCRIPT_DIR/intercept-claude.js"

# Check if Claude CLI is installed
if ! command -v claude &> /dev/null; then
    echo -e "${RED}Error: Claude CLI not found!${NC}"
    echo "Install with: npm install -g @anthropic-ai/claude-code"
    exit 1
fi

# Check if Node.js version is >= 18
NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo -e "${RED}Error: Node.js >= 18 required (current: v$NODE_VERSION)${NC}"
    exit 1
fi

echo -e "${BOLD}${CYAN}=== Claude CLI Structured Outputs Test ===${NC}\n"

# Set mode-specific environment variables
case "$MODE" in
    header-only)
        echo -e "${YELLOW}Mode: Header Only${NC} (beta header without schema)"
        unset ANTHROPIC_SCHEMA_FILE
        unset ANTHROPIC_SCHEMA
        ;;
    simple)
        echo -e "${YELLOW}Mode: Simple Schema${NC} (email extraction)"
        export ANTHROPIC_SCHEMA_FILE="$SCRIPT_DIR/test-schemas/simple.json"
        unset ANTHROPIC_SCHEMA
        ;;
    product)
        echo -e "${YELLOW}Mode: Product Schema${NC} (from examples)"
        # Create product schema from examples if needed
        if [ ! -f "$SCRIPT_DIR/test-schemas/product.json" ]; then
            echo -e "${RED}Error: test-schemas/product.json not found${NC}"
            echo "Create it from examples/structured_outputs.py first"
            exit 1
        fi
        export ANTHROPIC_SCHEMA_FILE="$SCRIPT_DIR/test-schemas/product.json"
        unset ANTHROPIC_SCHEMA
        ;;
    custom)
        echo -e "${YELLOW}Mode: Custom Schema${NC} (from ANTHROPIC_SCHEMA env var)"
        if [ -z "${ANTHROPIC_SCHEMA:-}" ]; then
            echo -e "${RED}Error: ANTHROPIC_SCHEMA environment variable not set${NC}"
            echo "Usage: ANTHROPIC_SCHEMA='{...}' $0 custom \"prompt\""
            exit 1
        fi
        unset ANTHROPIC_SCHEMA_FILE
        ;;
    *)
        echo -e "${RED}Error: Unknown mode '$MODE'${NC}"
        echo "Valid modes: header-only, simple, product, custom"
        exit 1
        ;;
esac

echo -e "${BLUE}Prompt:${NC} \"$PROMPT\""
echo

# Enable debug logging
export INTERCEPT_DEBUG=1

# Show what we're testing
echo -e "${MAGENTA}Testing Configuration:${NC}"
echo "  - Interceptor: $INTERCEPTOR"
if [ -n "${ANTHROPIC_SCHEMA_FILE:-}" ]; then
    echo "  - Schema File: $ANTHROPIC_SCHEMA_FILE"
fi
if [ -n "${ANTHROPIC_SCHEMA:-}" ]; then
    echo "  - Inline Schema: ${ANTHROPIC_SCHEMA:0:80}..."
fi
echo

# Separator
echo -e "${BOLD}${CYAN}--- Running Claude CLI with Interceptor ---${NC}\n"

# Run Claude CLI with the interceptor
# Use --require to load our interceptor before the CLI starts
node --require "$INTERCEPTOR" "$(which claude)" -p "$PROMPT" --permission-mode bypassPermissions --max-turns 1

EXIT_CODE=$?

echo
echo -e "${BOLD}${CYAN}--- Test Complete ---${NC}"

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ Claude CLI completed successfully${NC}"
else
    echo -e "${RED}✗ Claude CLI exited with code $EXIT_CODE${NC}"
fi

echo
echo -e "${YELLOW}Next Steps:${NC}"
echo "  1. Check the interceptor output above for [RESPONSE] logs"
echo "  2. Look for '✓ STRUCTURED OUTPUT DETECTED!' message"
echo "  3. If you see structured JSON, it works!"
echo "  4. If you see markdown, the CLI doesn't support it yet"
echo

exit $EXIT_CODE
