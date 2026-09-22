"""End-to-end tests for ending a one-shot ``query()`` run at the CLI's word (#1190).

``query()`` with hooks, ``can_use_tool`` or SDK MCP servers holds stdin open so
the CLI can ask the SDK questions while it works, and used to close it at the
first result with no tracked subagent in flight. A subagent that settles just
before the turn's result leaves nothing tracked, yet its completion still wakes
the parent for a follow-up turn whose hook, permission and SDK MCP requests then
fail with "Stream closed".

The SDK now tells the CLI the prompt is the only user message
(``end_user_input``). A CLI that honours it ends the run itself, so the SDK keeps
stdin open until stdout closes and folds the per-turn results into the one
``ResultMessage`` a string prompt yields. A CLI that does not honour it keeps the
old behaviour, so the tests that need the new one skip on such a CLI.
"""

from pathlib import Path
from typing import Any

import pytest

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
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


async def _cli_honours_end_user_input(cwd: Path) -> bool:
    """Whether the CLI this SDK would spawn answers ``end_user_input`` with success."""
    async with ClaudeSDKClient(options=_options(cwd, tools=[])) as client:
        assert client._query is not None
        try:
            await client._query._send_control_request(
                {"subtype": "end_user_input"}, timeout=30
            )
        except Exception:
            return False
    return True


@pytest.mark.e2e
@pytest.mark.anyio
async def test_hooks_run_ends_with_one_result(tmp_path: Path):
    """A string prompt with a hook configured completes with one result, whether
    the CLI ends the run (new) or the SDK closes stdin at the result (old)."""

    async def hook(input_data, tool_use_id, context):
        return {}

    options = _options(
        tmp_path,
        tools=[],
        hooks={"PreToolUse": [HookMatcher(matcher="Write", hooks=[hook])]},
    )

    messages = [m async for m in query(prompt="Reply with OK.", options=options)]

    results = [m for m in messages if isinstance(m, ResultMessage)]
    assert len(results) == 1
    assert not results[0].is_error


@pytest.mark.e2e
@pytest.mark.anyio
async def test_continuation_turn_after_a_background_subagent_is_served(
    tmp_path: Path,
):
    """The parent starts a background subagent and ends its turn. The subagent's
    completion wakes it for a second turn, which writes a file: the hook for that
    write must run (stdin still open), and ``query()`` yields one result for the
    whole run, with each turn's result in ``turn_results``."""
    if not await _cli_honours_end_user_input(tmp_path):
        pytest.skip("this CLI does not honour end_user_input")

    asked: list[str] = []

    async def hook(input_data, tool_use_id, context):
        asked.append(input_data["tool_name"])
        return {}

    target = tmp_path / "out.txt"
    options = _options(
        tmp_path,
        tools=["Agent", "Write"],
        hooks={"PreToolUse": [HookMatcher(matcher="Write", hooks=[hook])]},
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
    assert len(results) == 1, [type(m).__name__ for m in messages]
    (final,) = results
    assert not final.is_error, final
    assert asked == ["Write"], asked
    assert target.exists()
    assert final.turn_results is not None and len(final.turn_results) >= 2
    assert final.num_turns == sum(t.num_turns for t in final.turn_results)
