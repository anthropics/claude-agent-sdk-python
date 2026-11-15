"""End-to-end tests for when a one-shot ``query()`` closes stdin (#1190).

``query()`` with hooks, ``can_use_tool`` or SDK MCP servers holds stdin open so
the CLI can ask the SDK questions while it works. A result only ends a turn: a
background subagent that settles just before the turn's result still wakes the
parent for a follow-up turn, whose hook, permission and SDK MCP requests need
stdin. The SDK keeps stdin open until the CLI reports the session idle, which
it asks for with ``CLAUDE_CODE_SDK_READS_SESSION_STATE``; a CLI that predates
that variable sends no state and the SDK closes stdin at the first result.
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
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport


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
    the session-state frames the SDK asked for stay out of the stream."""
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


def _record_session_state_frames(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record the session_state_changed frames the CLI writes, including the
    sdk_host_only ones the SDK keeps out of the caller's stream."""
    frames: list[Any] = []
    read_messages = SubprocessCLITransport.read_messages

    async def recording(self: SubprocessCLITransport) -> Any:
        async for message in read_messages(self):
            if message.get("subtype") == "session_state_changed":
                frames.append(message)
            yield message

    monkeypatch.setattr(SubprocessCLITransport, "read_messages", recording)
    return frames


@pytest.mark.e2e
@pytest.mark.anyio
@pytest.mark.parametrize("caller_opted_in", [False, True], ids=["sdk", "caller"])
async def test_follow_up_turn_after_a_background_subagent_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller_opted_in: bool
):
    """The parent starts a background subagent and ends its turn. The
    subagent's completion wakes it for a second turn, which writes a file: the
    hook for that write must run, so stdin must still be open.

    ``sdk``: the SDK asks for the state frames itself and hides them.
    ``caller``: the caller opted in with CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS
    and sees them; the SDK reads the same frames to end the run."""
    # The variants differ only in options.env; keep the ambient environment
    # from choosing for them.
    monkeypatch.delenv("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SDK_READS_SESSION_STATE", raising=False)
    frames = _record_session_state_frames(monkeypatch)
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
        env=({"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"} if caller_opted_in else {}),
    )
    prompt = (
        "Step 1: call the Agent tool once with subagent_type `worker`, "
        "description `say done`, prompt `Say DONE.` and run_in_background true, "
        "then end your turn right away with the single word LAUNCHED. Do not wait "
        "for it. "
        "Step 2: when you are told the subagent finished, use the Write tool to "
        f"write its reply to {target}, then answer FINISHED."
    )

    messages: list[Any] = []
    error: Exception | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            messages.append(message)
    except Exception as e:  # noqa: BLE001 - re-raised below unless skipped
        error = e
    # Without state the SDK closes stdin at the first result, as documented,
    # so the follow-up turn is not expected to be served.
    if caller_opted_in and not frames:
        pytest.skip("this CLI sends no session_state_changed frames")
    if not caller_opted_in and not any(f.get("sdk_host_only") is True for f in frames):
        pytest.skip(
            "this CLI predates CLAUDE_CODE_SDK_READS_SESSION_STATE "
            "(sent no sdk_host_only session_state_changed frames)"
        )
    if error is not None:
        raise error

    states = [
        m
        for m in messages
        if isinstance(m, SystemMessage) and m.subtype == "session_state_changed"
    ]
    if caller_opted_in:
        assert states, "the caller opted in, so it sees the state frames"
        assert not any(m.data.get("sdk_host_only") for m in states), states
    else:
        assert not states, states
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
