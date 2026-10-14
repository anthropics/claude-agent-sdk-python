#!/usr/bin/env python3
"""Request a structured completion receipt for an agent run.

Final agent output is a summary, not proof that every claim is supported. This
example asks Claude to return a compact receipt that separates claims, evidence,
next owner, and any human approval boundary.
"""

import asyncio
import json
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query


COMPLETION_RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verification_status": {
            "type": "string",
            "enum": ["completed", "missing_evidence", "needs_human_review"],
        },
        "summary": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "support_status": {
                        "type": "string",
                        "enum": ["supported", "unverified", "unsupported"],
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "required_fix": {"type": ["string", "null"]},
                },
                "required": [
                    "claim",
                    "support_status",
                    "evidence",
                    "required_fix",
                ],
            },
        },
        "next_owner": {"type": "string"},
        "human_decision_required": {"type": "boolean"},
    },
    "required": [
        "verification_status",
        "summary",
        "claims",
        "next_owner",
        "human_decision_required",
    ],
}


AUDIT_PROMPT = """
You are auditing a final agent answer before a workflow treats it as complete.

Final answer:
"I updated README.md and all tests passed. The branch is ready to merge."

Available evidence:
- git diff -- README.md showed documentation-only changes.
- No test command output was captured.
- No reviewer or maintainer approval was captured.

Return a completion receipt. Do not mark a claim as supported unless the
available evidence directly supports it.
"""


async def main() -> None:
    """Run the receipt example and print the structured output."""
    options = ClaudeAgentOptions(
        output_format={
            "type": "json_schema",
            "schema": COMPLETION_RECEIPT_SCHEMA,
        },
        max_turns=1,
    )

    async for message in query(prompt=AUDIT_PROMPT, options=options):
        if isinstance(message, ResultMessage):
            print(json.dumps(message.structured_output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
