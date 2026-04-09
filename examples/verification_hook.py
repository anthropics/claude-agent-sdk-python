#!/usr/bin/env python3
"""
Example: Using VeroQ Shield as a post-message verification hook.

Verifies Claude's responses against external evidence before they reach
the user.  Flags or blocks responses that contain contradicted claims.

This demonstrates two hook patterns:

1. **Stop hook** -- runs when Claude finishes responding.  Reads the
   transcript, extracts the final assistant message, and verifies it
   with `veroq.verify_output`.  If any claim is contradicted the hook
   injects a warning into the conversation via `additionalContext` and
   can optionally halt the session.

2. **PostToolUse hook** -- runs after every tool call.  Useful when you
   want to verify tool *outputs* (e.g. a Bash command that returns data)
   rather than the final assistant message.

Requirements:
    pip install veroq claude-agent-sdk

Usage:
    export VEROQ_API_KEY="your-key"   # or omit for demo mode (5 checks)
    python examples/verification_hook.py
    python examples/verification_hook.py stop          # Stop-hook demo only
    python examples/verification_hook.py post_tool_use # PostToolUse demo only
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    StopHookInput,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(claims: list[dict[str, Any]]) -> str:
    """Format contradicted claims into a readable warning string."""
    lines: list[str] = []
    for claim in claims:
        lines.append(f"  - {claim.get('text', '(unknown claim)')}")
        correction = claim.get("correction")
        if correction:
            lines.append(f"    Correction: {correction}")
    return "\n".join(lines)


def _verify(text: str, max_claims: int = 5) -> dict[str, Any]:
    """Call VeroQ verify_output and return the result dict.

    Falls back gracefully if the veroq package is not installed or the
    API key is missing -- the example still illustrates the hook wiring.
    """
    try:
        from veroq import verify_output  # type: ignore[import-untyped]

        return verify_output(text, max_claims=max_claims)
    except ImportError:
        logger.warning(
            "veroq package not installed -- returning a simulated result. "
            "Install with: pip install veroq"
        )
        return {
            "trust_score": 0.85,
            "claims": [
                {
                    "text": "(simulated claim)",
                    "verdict": "supported",
                    "confidence": 0.9,
                },
            ],
        }
    except Exception as exc:
        logger.error("verify_output call failed: %s", exc)
        return {"trust_score": 0, "claims": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Hook 1 -- Stop hook (fires when Claude finishes its response)
# ---------------------------------------------------------------------------

async def verify_on_stop(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> HookJSONOutput:
    """Verify the final assistant response when the agent stops.

    Reads the session transcript to extract the last assistant message,
    then runs VeroQ verify_output against it.  If any claim is
    contradicted, injects a warning as additional context and
    optionally halts the session.
    """
    # The Stop hook receives a StopHookInput with a transcript_path.
    # We read the transcript to get the last assistant text.
    stop_input: StopHookInput = input_data  # type: ignore[assignment]
    transcript_path = stop_input.get("transcript_path", "")

    # Extract the last assistant text from the JSONL transcript.
    last_text = ""
    if transcript_path:
        try:
            with open(transcript_path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") == "assistant":
                        msg = entry.get("message", {})
                        for block in msg.get("content", []):
                            if block.get("type") == "text":
                                last_text = block["text"]
        except FileNotFoundError:
            logger.warning("Transcript not found at %s", transcript_path)

    if not last_text:
        logger.info("No assistant text found to verify.")
        return {}

    logger.info("Verifying assistant response (%d chars)...", len(last_text))
    result = _verify(last_text)

    trust_score = result.get("trust_score", 0)
    claims = result.get("claims", [])
    contradicted = [c for c in claims if c.get("verdict") == "contradicted"]

    summary = (
        f"Verification complete: trust_score={trust_score:.2f}, "
        f"{len(claims)} claim(s) checked, "
        f"{len(contradicted)} contradicted"
    )
    logger.info(summary)

    if contradicted:
        warning = (
            f"VeroQ Shield: {len(contradicted)} claim(s) contradicted "
            f"(trust score {trust_score:.0%}):\n"
            + _extract_text(contradicted)
        )
        print(f"\n{'=' * 60}")
        print(warning)
        print(f"{'=' * 60}\n")

        # Option A: inject a warning but let the session continue.
        return {
            "reason": warning,
            "systemMessage": warning,
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": (
                    f"[VeroQ Shield] {len(contradicted)} claim(s) were "
                    "contradicted by external evidence. The response may "
                    "contain factual errors."
                ),
            },
        }

        # Option B (uncomment to halt the session on contradictions):
        # return {
        #     "continue_": False,
        #     "stopReason": warning,
        #     "systemMessage": warning,
        # }

    print(f"\nVerification passed (trust score {trust_score:.0%}, "
          f"{len(claims)} claim(s) checked).\n")
    return {}


# ---------------------------------------------------------------------------
# Hook 2 -- PostToolUse hook (fires after every tool execution)
# ---------------------------------------------------------------------------

async def verify_tool_output(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> HookJSONOutput:
    """Verify tool output after a Bash command executes.

    Useful for catching factual errors in data returned by external
    tools before Claude incorporates them into its reasoning.
    """
    tool_name = input_data.get("tool_name", "")
    tool_response = str(input_data.get("tool_response", ""))

    if tool_name != "Bash":
        return {}

    # Only verify non-trivial output.
    if len(tool_response) < 100:
        return {}

    logger.info(
        "Verifying Bash output (%d chars) via VeroQ Shield...",
        len(tool_response),
    )
    result = _verify(tool_response, max_claims=3)

    trust_score = result.get("trust_score", 0)
    claims = result.get("claims", [])
    contradicted = [c for c in claims if c.get("verdict") == "contradicted"]

    if contradicted:
        warning = (
            f"VeroQ Shield flagged {len(contradicted)} contradicted claim(s) "
            f"in the tool output (trust score {trust_score:.0%})."
        )
        logger.warning(warning)
        return {
            "reason": warning,
            "systemMessage": warning,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"[VeroQ Shield] Tool output has {len(contradicted)} "
                    "contradicted claim(s). Cross-check before relying on "
                    "this data."
                ),
            },
        }

    return {}


# ---------------------------------------------------------------------------
# Demo runners
# ---------------------------------------------------------------------------

def display_message(msg: Any) -> None:
    """Print assistant text or result summary."""
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
    elif isinstance(msg, ResultMessage):
        cost = f"${msg.total_cost_usd:.4f}" if msg.total_cost_usd else "n/a"
        print(f"\n[Done] duration={msg.duration_ms}ms  cost={cost}")


async def demo_stop_hook() -> None:
    """Run the Stop-hook verification demo."""
    print("=" * 60)
    print("Demo: Verify Claude's final response with a Stop hook")
    print("=" * 60)
    print()

    options = ClaudeAgentOptions(
        max_turns=1,
        hooks={
            "Stop": [
                HookMatcher(matcher=None, hooks=[verify_on_stop]),
            ],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        prompt = (
            "What is the population of Tokyo as of 2024? "
            "Include the year it was founded."
        )
        print(f"User: {prompt}\n")
        await client.query(prompt)

        async for msg in client.receive_response():
            display_message(msg)

    print()


async def demo_post_tool_use_hook() -> None:
    """Run the PostToolUse verification demo."""
    print("=" * 60)
    print("Demo: Verify Bash tool output with a PostToolUse hook")
    print("=" * 60)
    print()

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        max_turns=2,
        hooks={
            "PostToolUse": [
                HookMatcher(matcher="Bash", hooks=[verify_tool_output]),
            ],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        prompt = (
            "Run a bash command that prints the current date and the "
            "hostname of this machine."
        )
        print(f"User: {prompt}\n")
        await client.query(prompt)

        async for msg in client.receive_response():
            display_message(msg)

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    demos = {
        "stop": demo_stop_hook,
        "post_tool_use": demo_post_tool_use_hook,
    }

    if len(sys.argv) < 2:
        print("Usage: python verification_hook.py [stop|post_tool_use|all]")
        print()
        print("Demos:")
        print("  stop           Verify the final assistant response (Stop hook)")
        print("  post_tool_use  Verify Bash tool output (PostToolUse hook)")
        print("  all            Run both demos")
        sys.exit(0)

    choice = sys.argv[1].lower()

    if choice == "all":
        for demo_fn in demos.values():
            await demo_fn()
            print("-" * 60)
            print()
    elif choice in demos:
        await demos[choice]()
    else:
        print(f"Unknown demo: {choice}")
        print("Available: " + ", ".join(demos) + ", all")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
