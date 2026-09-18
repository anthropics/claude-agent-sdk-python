"""Stored transcripts and summary sidecars must not alias caller-owned objects."""

from __future__ import annotations

import uuid

import pytest

from claude_agent_sdk import (
    InMemorySessionStore,
    SessionKey,
    get_session_messages_from_store,
    project_key_for_directory,
)
from claude_agent_sdk.types import SessionStoreEntry

_KEY: SessionKey = {"project_key": "proj", "session_id": "sess"}


@pytest.mark.anyio
async def test_append_snapshots_nested_input() -> None:
    store = InMemorySessionStore()
    entries: list[SessionStoreEntry] = [
        {"uuid": "original", "message": {"content": [{"text": "saved"}]}}
    ]
    await store.append(_KEY, entries)

    entries[0]["uuid"] = "changed"
    entries[0]["message"]["content"][0]["text"] = "changed"
    entries.clear()

    assert await store.load(_KEY) == [
        {"uuid": "original", "message": {"content": [{"text": "saved"}]}}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("reader", ["load", "get_entries"])
async def test_readers_return_independent_snapshots(reader: str) -> None:
    store = InMemorySessionStore()
    await store.append(
        _KEY, [{"uuid": "original", "message": {"content": [{"text": "saved"}]}}]
    )
    loaded = await store.load(_KEY) if reader == "load" else store.get_entries(_KEY)
    assert loaded is not None
    loaded[0]["uuid"] = "changed"
    loaded[0]["message"]["content"][0]["text"] = "changed"

    assert await store.load(_KEY) == [
        {"uuid": "original", "message": {"content": [{"text": "saved"}]}}
    ]


@pytest.mark.anyio
async def test_summary_results_do_not_mutate_stored_metadata() -> None:
    store = InMemorySessionStore()
    await store.append(
        _KEY, [{"type": "user", "message": {"content": "original prompt"}}]
    )
    summaries = await store.list_session_summaries("proj")
    mtime = summaries[0]["mtime"]
    summaries[0]["session_id"] = "changed"
    summaries[0]["mtime"] = 0
    summaries[0]["data"]["first_prompt"] = "changed"

    fresh = await store.list_session_summaries("proj")
    assert fresh[0]["session_id"] == "sess"
    assert fresh[0]["mtime"] == mtime
    assert fresh[0]["data"]["first_prompt"] == "original prompt"


@pytest.mark.anyio
async def test_public_reader_does_not_alias_stored_message() -> None:
    # The store-backed getter is the path callers actually use; a later
    # shortcut in _entries_to_session_messages could reintroduce aliasing
    # even if load() itself still copies.
    directory = "/tmp/session-store-snapshots"
    sid = str(uuid.uuid4())
    key: SessionKey = {
        "project_key": project_key_for_directory(directory),
        "session_id": sid,
    }
    store = InMemorySessionStore()
    await store.append(
        key,
        [
            {
                "type": "user",
                "uuid": str(uuid.uuid4()),
                "parentUuid": None,
                "sessionId": sid,
                "timestamp": "2026-09-14T00:00:00Z",
                "message": {"role": "user", "content": "saved"},
            }
        ],
    )

    msgs = await get_session_messages_from_store(store, sid, directory=directory)
    assert msgs[0].message["content"] == "saved"
    msgs[0].message["content"] = "changed"

    again = await get_session_messages_from_store(store, sid, directory=directory)
    assert again[0].message["content"] == "saved"
