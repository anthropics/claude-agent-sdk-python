"""Example: TrueAgenticLoop — O₂ agentic loop with Frobenius verification.

Requires: pip install claude-agent-sdk

Demonstrates the structural promotion from O₀ (thin subprocess wrapper)
to O₂ (self-verifying agentic framework) via the Imscribing Grammar.

Run:
    python examples/true_agentic_loop.py
"""

import asyncio

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.agentic import (
    DualToolResult,
    PhiCriticalityGate,
    ToolContract,
    TrueAgenticLoop,
)


def simple_verify(tool_input: dict, tool_output: str) -> tuple[str, bool]:
    """Simple verification: check that output is non-empty."""
    closed = len(tool_output.strip()) > 0
    return f"non_empty={closed}", closed


async def main():
    # Standard client (unchanged — backward compatible)
    client = ClaudeSDKClient()

    # Optional tool contracts for Frobenius verification
    contracts = {
        "read": ToolContract(
            tool_name="read",
            verify_fn=simple_verify,
            auto_approve=True,
        ),
    }

    # O₂ agentic loop wrapping the client
    loop = TrueAgenticLoop(
        client=client,
        max_windings=10,
        tool_contracts=contracts,
    )

    result = await loop.run(
        "Read the project README and summarize its structure."
    )

    print(f"\n=== Result ===\n{result}")
    print(f"\n=== Structural Health ===")
    health = loop.structural_health
    for k, v in health.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
