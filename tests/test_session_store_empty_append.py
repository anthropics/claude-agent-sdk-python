"""Regression: ``append([])`` must not create or touch a session."""

import pytest

from claude_agent_sdk import InMemorySessionStore
from claude_agent_sdk.types import SessionKey, SessionStoreEntry

KEY: SessionKey = {"project_key": "proj", "session_id": "session-1"}


@pytest.mark.anyio
async def test_empty_append_is_noop() -> None:
    store = InMemorySessionStore()

    await store.append(KEY, [])

    assert await store.load(KEY) is None
    assert await store.list_sessions("proj") == []
    assert await store.list_session_summaries("proj") == []


@pytest.mark.anyio
async def test_empty_append_does_not_bump_mtime() -> None:
    store = InMemorySessionStore()
    await store.append(KEY, [SessionStoreEntry(type="user")])
    before = (await store.list_sessions("proj"))[0]["mtime"]

    await store.append(KEY, [])

    after = (await store.list_sessions("proj"))[0]["mtime"]
    assert after == before
