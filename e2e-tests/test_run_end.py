"""End-to-end tests for when a one-shot ``query()`` closes stdin (#1190).

``query()`` with hooks, ``can_use_tool`` or SDK MCP servers holds stdin open so
the CLI can ask the SDK questions while it works. A result only ends a turn: a
background subagent that settles just before the turn's result still wakes the
parent for a follow-up turn, whose hook, permission and SDK MCP requests need
stdin. The SDK now keeps stdin open until the CLI reports the session idle.
"""

from pathlib import Path
from typing import Any

import pytest

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
    query,
)


def _options(cwd: Path, **overrides: Any) -> ClaudeAgentOptions:
    settings: dict[str, Any] = {
        "cwd": str(cwd),
        "model": "haiku",
        "setting_sources": [],
        "extra_args": {"strict-mcp-config": None},
        "system_prompt": "Be terse. Follow the user's steps exactly.",
    }
    settings.update(overrides)
    return ClaudeAgentOptions(**settings)


def _record_hook(asked: list[str]) -> Any:
    """A PreToolUse hook that records the tool and allows it, so the call only
    goes through if the SDK was still there to answer the hook."""

    async def hook(input_data, tool_use_id, context):
        asked.append(input_data["tool_name"])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }

    return hook


@pytest.mark.e2e
@pytest.mark.anyio
async def test_hook_run_ends_with_one_result(tmp_path: Path):
    """A plain string prompt with a hook still completes with one result, and
    the session-state frames the SDK turned on stay out of the stream."""
    asked: list[str] = []
    options = _options(
        tmp_path,
        tools=[],
        hooks={"PreToolUse": [HookMatcher(hooks=[_record_hook(asked)])]},
    )

    messages = [m async for m in query(prompt="Reply with OK.", options=options)]

    results = [m for m in messages if isinstance(m, ResultMessage)]
    assert len(results) == 1
    assert not results[0].is_error
    assert not [
        m
        for m in messages
        if isinstance(m, SystemMessage) and m.subtype == "session_state_changed"
    ]


@pytest.mark.e2e
@pytest.mark.anyio
async def test_follow_up_turn_after_a_background_subagent_is_served(
    tmp_path: Path,
):
    """The parent starts a background subagent and ends its turn. The
    subagent's completion wakes it for a second turn, which writes a file: the
    hook for that write must run, so stdin must still be open."""
    asked: list[str] = []
    target = tmp_path / "out.txt"
    options = _options(
        tmp_path,
        tools=["Agent", "Write"],
        hooks={
            "PreToolUse": [HookMatcher(matcher="Write", hooks=[_record_hook(asked)])]
        },
        agents={
            "worker": AgentDefinition(
                description="Answers with one word.",
                prompt="Reply with the single word DONE and nothing else.",
                tools=[],
                model="haiku",
            )
        },
    )
    prompt = (
        "Step 1: call the Agent tool once with subagent_type `worker`, "
        "description `say done`, prompt `Say DONE.` and run_in_background true, "
        "then end your turn right away with the single word LAUNCHED. Do not wait "
        "for it. "
        "Step 2: when you are told the subagent finished, use the Write tool to "
        f"write its reply to {target}, then answer FINISHED."
    )

    messages = [m async for m in query(prompt=prompt, options=options)]

    results = [m for m in messages if isinstance(m, ResultMessage)]
    assert len(results) >= 2, [type(m).__name__ for m in messages]
    assert not any(r.is_error for r in results), results
    # The Write must come from the follow-up turn, after the first result.
    first_result = next(
        i for i, m in enumerate(messages) if isinstance(m, ResultMessage)
    )
    writes = [
        i
        for i, m in enumerate(messages)
        if isinstance(m, AssistantMessage)
        and any(isinstance(b, ToolUseBlock) and b.name == "Write" for b in m.content)
    ]
    assert writes and writes[0] > first_result, (first_result, writes)
    # The hook grants the write, so the file only lands if it was served. The
    # model may retry a write, so the hook can run more than once.
    assert "Write" in asked, asked
    assert target.exists()
