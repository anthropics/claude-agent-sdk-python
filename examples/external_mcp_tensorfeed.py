#!/usr/bin/env python3
"""Example: External hosted MCP server (TensorFeed.ai cross-database CVE verification).

This example demonstrates how to wire an external HTTP MCP server into
the Claude Agent SDK. The TensorFeed MCP server at https://tensorfeed.ai/api/mcp
exposes 17 free tools across AI news, model pricing, AI service status,
security advisories (MITRE CVE / CISA KEV / FIRST.org EPSS / OSV), SEC EDGAR
filings, FDA regulatory data, and US energy indicators. No auth required.

The demo asks Claude to verify CVE-2024-3094 (the XZ backdoor) across three
independent vulnerability databases. The model autonomously sequences the
calls (MITRE for the canonical record, CISA KEV for active exploitation
status, FIRST.org EPSS for exploitation likelihood) and surfaces a
confirmed_by list so the user can audit which sources backed the answer.

The premise: the actual production failure mode for security agents is not
hallucination but acting on a single source. Cross-source corroboration is
the fix. The TensorFeed MCP server makes that one call in the agent loop
instead of N parallel API integrations.

Other endpoints worth trying (free, no auth):
  - "What's the latest AI news about Anthropic?" -> get_news_articles
  - "Is Claude up right now?" -> get_status_summary
  - "Compare Opus and GPT-5 pricing" -> get_models
  - "Find recent FDA Class I drug recalls" -> query_fda_drug_recalls
  - "Search SEC EDGAR for Anthropic filings" -> search_sec_edgar
"""

import anyio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)


async def main() -> None:
    options = ClaudeAgentOptions(
        mcp_servers={
            "tensorfeed": {
                "type": "http",
                "url": "https://tensorfeed.ai/api/mcp",
            }
        },
        # Restrict to the security tools for this demo. Drop allowed_tools
        # to expose all 17 TensorFeed tools to the model.
        allowed_tools=[
            "mcp__tensorfeed__get_cve_record",
            "mcp__tensorfeed__get_kev_catalog",
            "mcp__tensorfeed__get_epss_score",
        ],
        max_turns=8,
    )

    prompt = (
        "Verify CVE-2024-3094 across multiple databases via the tensorfeed MCP server. "
        "Call get_cve_record for the MITRE record, get_kev_catalog to check for active "
        "exploitation, and get_epss_score for the FIRST.org probability. Then summarize: "
        "severity_band, exploited_in_wild boolean (true if KEV has the CVE), "
        "epss_probability, a confirmed_by list of databases that returned data, and a "
        "one-sentence triage recommendation."
    )

    print(f"Asking Claude: {prompt}\n")
    print("=" * 70)

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            if message.total_cost_usd:
                print("=" * 70)
                print(f"\nCost: ${message.total_cost_usd:.4f}")


if __name__ == "__main__":
    anyio.run(main)
