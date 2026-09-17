"""End-to-end tests for truncating resume (resume_session_at / resume_drops_turn).

Builds a two-turn session, then forks it at the end of turn 1:

- with ``resume_drops_turn`` set to turn 2's prompt UUID the CLI validates the
  discarded range and the fork succeeds with turn 2 gone;
- with ``resume_drops_turn`` set to some other UUID the CLI refuses, and the
  refusal reaches the caller as an actionable ``ProcessError``.
"""

import uuid
from pathlib import Path

import anyio
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ProcessError,
    ResultMessage,
    get_session_messages,
    query,
)

TURN_1 = "Reply with exactly: one"
TURN_2 = "Reply with exactly: two"
TURN_3 = "Reply with exactly: three"


async def _two_turn_session(cwd: str) -> tuple[str, str, str]:
    """Run two turns; return (session_id, last uuid of turn 1, prompt uuid of turn 2)."""
    last_assistant_turn_1: AssistantMessage | None = None
    results: list[ResultMessage] = []
    async with ClaudeSDKClient(ClaudeAgentOptions(cwd=cwd, max_turns=1)) as client:
        with anyio.fail_after(120):
            await client.query(TURN_1)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    last_assistant_turn_1 = message
                elif isinstance(message, ResultMessage):
                    results.append(message)
            await client.query(TURN_2)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    results.append(message)
    assert last_assistant_turn_1 is not None and last_assistant_turn_1.uuid
    assert len(results) == 2 and not any(r.is_error for r in results), results
    session_id = results[-1].session_id

    # The transcript chain is [.., turn-1 last assistant, turn-2 prompt, ..].
    chain = get_session_messages(session_id, directory=cwd)
    uuids = [m.uuid for m in chain]
    keep_at = last_assistant_turn_1.uuid
    assert keep_at in uuids, "turn-1 assistant uuid not found in transcript"
    turn_2_idx = next(
        i
        for i, m in enumerate(chain)
        if m.type == "user" and m.message.get("content") == TURN_2
    )
    assert turn_2_idx == uuids.index(keep_at) + 1, (
        "unexpected transcript entries between turn 1 and turn 2: "
        f"{[(m.type, m.uuid) for m in chain]}"
    )
    return session_id, keep_at, chain[turn_2_idx].uuid


def _prompts(session_id: str, cwd: str) -> list[str]:
    return [
        m.message["content"]
        for m in get_session_messages(session_id, directory=cwd)
        if m.type == "user" and isinstance(m.message.get("content"), str)
    ]


@pytest.mark.e2e
@pytest.mark.anyio
async def test_truncating_resume_with_matching_drops_turn_forks_cleanly(
    tmp_path: Path,
):
    cwd = str(tmp_path)
    session_id, keep_at, turn_2_prompt = await _two_turn_session(cwd)

    options = ClaudeAgentOptions(
        cwd=cwd,
        max_turns=1,
        resume=session_id,
        fork_session=True,
        resume_session_at=keep_at,
        resume_drops_turn=turn_2_prompt,
    )
    forked: ResultMessage | None = None
    with anyio.fail_after(120):
        async for message in query(prompt=TURN_3, options=options):
            if isinstance(message, ResultMessage):
                forked = message

    assert forked is not None and not forked.is_error
    assert forked.session_id != session_id
    # Turn 2 was discarded; turn 3 was appended after turn 1.
    assert _prompts(forked.session_id, cwd) == [TURN_1, TURN_3]
    # The source session is untouched.
    assert _prompts(session_id, cwd) == [TURN_1, TURN_2]


@pytest.mark.e2e
@pytest.mark.anyio
async def test_truncating_resume_with_wrong_drops_turn_is_refused(tmp_path: Path):
    cwd = str(tmp_path)
    session_id, keep_at, _ = await _two_turn_session(cwd)

    options = ClaudeAgentOptions(
        cwd=cwd,
        max_turns=1,
        resume=session_id,
        fork_session=True,
        resume_session_at=keep_at,
        # Not the prompt that actually follows `keep_at` → the CLI must refuse
        # rather than silently discard turn 2.
        resume_drops_turn=str(uuid.uuid4()),
    )
    with anyio.fail_after(120), pytest.raises(ProcessError) as exc_info:
        async for _ in query(prompt=TURN_3, options=options):
            pass

    assert "Resume rejected by --resume-drops-turn:" in str(exc_info.value)
    # Nothing was forked or appended.
    assert _prompts(session_id, cwd) == [TURN_1, TURN_2]
