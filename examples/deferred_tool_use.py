#!/usr/bin/env python3
"""Deferred Tool Use example.

When a PreToolUse hook returns permissionDecision="defer", the agent
PAUSES and the ResultMessage carries a DeferredToolUse object.
The caller can then inspect the tool call, decide to allow or deny,
and resume the session — or abort entirely.

Flow:
  Claude wants to run Bash  ->  hook returns "defer"
       |
  Session pauses, ResultMessage.deferred_tool_use is set
       |
  Caller inspects: tool name + input
       |
  [allow] resume=session_id + permission_mode="bypassPermissions"
  [deny]  open a fresh plan-mode session to suggest alternatives

Usage:
./examples/deferred_tool_use.py
"""

import asyncio
import json

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk.types import (
    DeferredToolUse,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    Message,
)


async def defer_for_approval(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> HookJSONOutput:
    """Pause the agent and hand the tool call back to the caller."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "defer",
            "permissionDecisionReason": "Requires human approval before execution",
        }
    }


def display(msg: Message) -> None:
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
    elif isinstance(msg, ResultMessage):
        if msg.is_error:
            errors = msg.errors or [msg.subtype or "unknown error"]
            print(f"\nSession ERROR: {'; '.join(errors)}")
        elif msg.deferred_tool_use:
            deferred: DeferredToolUse = msg.deferred_tool_use
            print(f"\nSession PAUSED -- deferred tool call:")
            print(f"   Tool  : {deferred.name}")
            print(f"   Input : {deferred.input}")
        else:
            cost = f"${msg.total_cost_usd:.4f}" if msg.total_cost_usd else "n/a"
            print(f"\nSession ended | cost={cost} turns={msg.num_turns}")


async def main() -> None:
    print("=" * 60)
    print("Deferred Tool Use -- Human-in-the-Loop Demo")
    print("=" * 60)

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        permission_mode="default",
        hooks={
            "PreToolUse": [HookMatcher(matcher="Bash", hooks=[defer_for_approval])],
        },
    )

    print("\n[Turn 1] Asking Claude to run a bash command...\n")
    deferred: DeferredToolUse | None = None
    session_id: str | None = None

    async with ClaudeSDKClient(options) as client:
        await client.query("Run: echo 'hello from deferred tool use!'")
        async for msg in client.receive_response():
            display(msg)
            if isinstance(msg, ResultMessage):
                deferred = msg.deferred_tool_use
                session_id = msg.session_id

    if not deferred:
        print("\n(No tool was deferred -- Claude answered without using Bash)")
        return

    if session_id is None:
        print("\nError: no session ID captured; cannot resume.")
        return

    print(f"\nHuman review required:")
    print(f"   Tool  : {deferred.name}")
    print(f"   Input : {deferred.input}")
    decision = input("\n   Allow this command? (y/N): ").strip().lower()

    if decision in ("y", "yes"):
        print("\n[Turn 2] Resuming session with bypassPermissions...\n")
        resume_options = ClaudeAgentOptions(
            allowed_tools=["Bash"],
            permission_mode="bypassPermissions",
            resume=session_id,
        )
        async with ClaudeSDKClient(resume_options) as client:
            await client.query("Approved. Go ahead.")
            async for msg in client.receive_response():
                display(msg)

    else:
        print("\n[Turn 2] Starting fresh session to suggest alternatives...\n")
        deny_options = ClaudeAgentOptions(
            permission_mode="plan",
        )
        cmd = json.dumps(deferred.input)
        async with ClaudeSDKClient(deny_options) as client:
            await client.query(
                f"A user asked an AI assistant to run this bash command, "
                f"but the user denied it:\n\n  {cmd}\n\n"
                "Please suggest an alternative approach that achieves the "
                "same goal without running shell commands."
            )
            async for msg in client.receive_response():
                display(msg)


if __name__ == "__main__":
    asyncio.run(main())
