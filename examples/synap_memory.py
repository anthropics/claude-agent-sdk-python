#!/usr/bin/env python3
"""Example: Synap memory integration.

Demonstrates how to give a Claude Agent persistent, cross-session memory using
Synap (https://maximem.ai) — a managed memory layer for AI agents.

Two plug points:
  1. `create_synap_hooks` — installs a `UserPromptSubmit` hook that fetches
     relevant Synap context for each prompt and injects it via
     `additionalContext`. Optionally records the user prompt for future recall.
  2. `create_synap_mcp_server` — exposes `synap_search` and `synap_remember`
     as MCP tools the model can call explicitly.

Install:
    pip install synap-claude-agent maximem-synap

Set `SYNAP_API_KEY` in your environment. Get a free key at
https://synap.maximem.ai.

Open source: https://github.com/maximem-ai/maximem_synap_sdk
"""

import asyncio
import os

from claude_agent_sdk import ClaudeAgentOptions, query
from maximem_synap import MaximemSynapSDK
from synap_claude_agent import create_synap_hooks, create_synap_mcp_server


async def main() -> None:
    sdk = MaximemSynapSDK(api_key=os.environ["SYNAP_API_KEY"])
    await sdk.initialize()

    user_id = "demo-user-001"
    customer_id = "demo-customer"

    # Pattern 1 — automatic context injection via hook
    print("=== Hook-based context injection ===")
    async for message in query(
        prompt="What did I tell you about my dietary preferences?",
        options=ClaudeAgentOptions(
            hooks=create_synap_hooks(sdk, user_id=user_id, customer_id=customer_id),
        ),
    ):
        print(message)

    # Pattern 2 — explicit search / remember via MCP tools
    print("\n=== MCP tool-based memory access ===")
    options = ClaudeAgentOptions(
        mcp_servers={
            "synap": create_synap_mcp_server(
                sdk, user_id=user_id, customer_id=customer_id
            ),
        },
        allowed_tools=["mcp__synap__synap_search", "mcp__synap__synap_remember"],
    )
    async for message in query(
        prompt="Remember that I prefer concise answers, then search my memory.",
        options=options,
    ):
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
