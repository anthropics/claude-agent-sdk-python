"""End-to-end tests for the persist_session option with real Claude API calls.

By default Claude Code writes each session's transcript to disk, where
``list_sessions()`` finds it and ``resume`` can load it.
``persist_session=False`` passes ``--no-session-persistence``, so nothing is
written for the session.
"""

from pathlib import Path

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    list_sessions,
    query,
)

PROMPT = "Reply with only the word OK."


def _options(cwd: Path, persist_session: bool) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        persist_session=persist_session,
        cwd=str(cwd),
        model="haiku",
        tools=[],
        setting_sources=[],
        extra_args={"strict-mcp-config": None},
        max_turns=1,
    )


async def _session_id_via_query(options: ClaudeAgentOptions) -> str:
    session_id = ""
    async for message in query(prompt=PROMPT, options=options):
        if isinstance(message, ResultMessage):
            assert not message.is_error, message
            session_id = message.session_id
    assert session_id
    return session_id


async def _session_id_via_client(options: ClaudeAgentOptions) -> str:
    session_id = ""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                assert not message.is_error, message
                session_id = message.session_id
    assert session_id
    return session_id


def _listed_session_ids(cwd: Path) -> list[str]:
    return [s.session_id for s in list_sessions(directory=str(cwd))]


@pytest.mark.e2e
@pytest.mark.anyio
async def test_session_is_persisted_by_default(tmp_path):
    """Control: without the option the transcript lands on disk, so the tests
    below are meaningful."""
    session_id = await _session_id_via_query(_options(tmp_path, persist_session=True))
    assert session_id in _listed_session_ids(tmp_path)


@pytest.mark.e2e
@pytest.mark.anyio
async def test_query_does_not_persist_session(tmp_path):
    session_id = await _session_id_via_query(_options(tmp_path, persist_session=False))
    assert session_id not in _listed_session_ids(tmp_path)


@pytest.mark.e2e
@pytest.mark.anyio
async def test_client_does_not_persist_session(tmp_path):
    session_id = await _session_id_via_client(_options(tmp_path, persist_session=False))
    assert session_id not in _listed_session_ids(tmp_path)
