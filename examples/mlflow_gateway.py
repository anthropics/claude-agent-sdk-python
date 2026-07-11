#!/usr/bin/env python3
"""Example of using Claude Agent SDK with MLflow AI Gateway.

MLflow AI Gateway (MLflow >= 3.0) is a database-backed LLM proxy that routes
requests to multiple providers through a unified API. It provides an
Anthropic-compatible passthrough at /gateway/anthropic/v1, so the Claude
Agent SDK can use it as a drop-in replacement for the Anthropic API.

Setup:
    1. pip install mlflow[genai]
    2. mlflow server --host 127.0.0.1 --port 5000
    3. Create a gateway endpoint in the MLflow UI at http://localhost:5000
       (AI Gateway → Create Endpoint, select Anthropic as provider)
    4. Set environment variables:
       export ANTHROPIC_BASE_URL="http://localhost:5000/gateway/anthropic"
       export ANTHROPIC_API_KEY="unused"  # provider keys are on the server

Usage:
    python examples/mlflow_gateway.py
"""

import anyio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)


async def gateway_example():
    """Use Claude Agent SDK through MLflow AI Gateway."""
    print("=== MLflow AI Gateway Example ===")

    # Point the SDK to MLflow AI Gateway's Anthropic-compatible passthrough.
    # The gateway manages provider API keys — ANTHROPIC_API_KEY is not
    # validated but must be set to a non-empty value.
    options = ClaudeAgentOptions(
        system_prompt="You are a helpful assistant. Keep answers concise.",
        max_turns=1,
        env={
            "ANTHROPIC_BASE_URL": "http://localhost:5000/gateway/anthropic",
            "ANTHROPIC_API_KEY": "unused",
        },
    )

    async for message in query(
        prompt="What is MLflow AI Gateway? Answer in one sentence.",
        options=options,
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")
        elif isinstance(message, ResultMessage) and message.total_cost_usd > 0:
            print(f"\nCost: ${message.total_cost_usd:.4f}")

    print()


async def main():
    await gateway_example()


if __name__ == "__main__":
    anyio.run(main)
