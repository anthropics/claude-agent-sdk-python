#!/usr/bin/env python3
"""Use HOL Guard to classify Bash commands in a Claude Agent SDK hook.

Install the side-effect-free classifier before running this example::

    pipx install hol-guard
    python examples/hol_guard.py

``hol-guard command test`` classifies a command without executing it. The
hook allows only results that Guard explicitly marks as benign. Missing
Guard, timeouts, malformed output, and review-required results are denied so
that a failed safety check cannot turn into an allowed Bash execution.
"""

import asyncio
import json
from asyncio.subprocess import PIPE
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
)

HOL_GUARD_TIMEOUT_SECONDS = 5.0


def _deny(reason: str) -> HookJSONOutput:
    """Return a fail-closed PreToolUse denial."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


async def _classify_with_hol_guard(command: str) -> dict[str, Any]:
    """Classify a shell command with HOL Guard's JSON interface."""
    process = await asyncio.create_subprocess_exec(
        "hol-guard",
        "command",
        "test",
        command,
        "--json",
        stdout=PIPE,
        stderr=PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=HOL_GUARD_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("HOL Guard timed out") from None

    if process.returncode != 0:
        raise RuntimeError(f"HOL Guard exited with status {process.returncode}")

    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("HOL Guard returned malformed JSON") from exc

    if not isinstance(result, dict):
        raise RuntimeError("HOL Guard returned a JSON value that was not an object")
    return result


async def check_bash_command(
    input_data: HookInput, tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    """Allow Bash only when HOL Guard explicitly classifies it as benign."""
    del tool_use_id, context
    if input_data.get("tool_name") != "Bash":
        return {}

    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return _deny("HOL Guard requires structured Bash tool input")

    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command:
        return _deny("HOL Guard requires a non-empty Bash command")

    try:
        result = await _classify_with_hol_guard(command)
    except (OSError, RuntimeError) as exc:
        return _deny(f"HOL Guard could not classify this command: {exc}")

    classification = result.get("classification")
    if (
        result.get("minimum_action") == "allow"
        and isinstance(classification, dict)
        and classification.get("explicitly_benign") is True
    ):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "HOL Guard classified the command as benign",
            }
        }

    return _deny("HOL Guard requires review for this command")


async def main() -> None:
    """Run one interactive client with the HOL Guard Bash hook installed."""
    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[check_bash_command]),
            ]
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Run `git status` and tell me whether the working tree is clean."
        )
        async for message in client.receive_response():
            print(message)


if __name__ == "__main__":
    asyncio.run(main())
