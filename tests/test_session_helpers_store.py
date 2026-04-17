"""Tests for the ``*_from_store`` / ``*_via_store`` async session helpers.

Exercises the SessionStore-backed code paths in
``_internal/sessions.py`` and ``_internal/session_mutations.py`` using
``InMemorySessionStore``.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

import pytest

from claude_agent_sdk import (
    InMemorySessionStore,
    delete_session_via_store,
    fork_session_via_store,
    get_session_info_from_store,
    get_session_messages_from_store,
    get_subagent_messages_from_store,
    list_sessions_from_store,
    list_subagents_from_store,
    project_key_for_directory,
    rename_session_via_store,
    tag_session_via_store,
)
from claude_agent_sdk.types import SessionKey, SessionStore

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

DIR = "/workspace/project"
PROJECT_KEY = project_key_for_directory(DIR)


def _user(text: str, uid: str, parent: str | None, sid: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uid,
        "parentUuid": parent,
        "sessionId": sid,
        "timestamp": "2024-01-01T00:00:00.000Z",
        "message": {"role": "user", "content": text},
    }


def _assistant(text: str, uid: str, parent: str, sid: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uid,
        "parentUuid": parent,
        "sessionId": sid,
        "timestamp": "2024-01-01T00:00:01.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


async def _seed_chain(store: InMemorySessionStore, sid: str, n: int = 2) -> list[str]:
    """Append ``n`` user/assistant pairs and return their UUIDs in order."""
    key: SessionKey = {"project_key": PROJECT_KEY, "session_id": sid}
    uuids: list[str] = []
    parent: str | None = None
    entries: list[dict[str, Any]] = []
    for i in range(n):
        u = str(uuid_mod.uuid4())
        a = str(uuid_mod.uuid4())
        entries.append(_user(f"prompt {i}", u, parent, sid))
        entries.append(_assistant(f"reply {i}", a, u, sid))
        uuids.extend([u, a])
        parent = a
    await store.append(key, entries)  # type: ignore[arg-type]
    return uuids


class _MinimalStore(SessionStore):
    """Duck-typed store implementing only the required ``append``/``load``."""

    def __init__(self) -> None:
        self._data: dict[str, list] = {}

    async def append(self, key: SessionKey, entries: list) -> None:
        k = f"{key['project_key']}/{key['session_id']}/{key.get('subpath', '')}"
        self._data.setdefault(k, []).extend(entries)

    async def load(self, key: SessionKey) -> list | None:
        k = f"{key['project_key']}/{key['session_id']}/{key.get('subpath', '')}"
        return self._data.get(k)


# ---------------------------------------------------------------------------
# Read helpers — list_sessions / get_session_info / get_session_messages
# ---------------------------------------------------------------------------


class TestListSessionsFromStore:
    async def test_lists_seeded_sessions_sorted_by_mtime(self) -> None:
        store = InMemorySessionStore()
        sid_a = str(uuid_mod.uuid4())
        sid_b = str(uuid_mod.uuid4())
        await _seed_chain(store, sid_a)
        await _seed_chain(store, sid_b)

        sessions = await list_sessions_from_store(store, directory=DIR)
        ids = {s.session_id for s in sessions}
        assert ids == {sid_a, sid_b}
        # Summary derived from first prompt via the same lite-parse as disk.
        for s in sessions:
            assert s.summary == "prompt 0"
            assert s.first_prompt == "prompt 0"
        # Sorted by mtime descending (non-increasing).
        mtimes = [s.last_modified for s in sessions]
        assert mtimes == sorted(mtimes, reverse=True)

    async def test_limit_and_offset(self) -> None:
        store = InMemorySessionStore()
        for _ in range(3):
            await _seed_chain(store, str(uuid_mod.uuid4()))
        page = await list_sessions_from_store(store, directory=DIR, limit=2, offset=1)
        assert len(page) == 2

    async def test_raises_when_store_lacks_list_sessions(self) -> None:
        store = _MinimalStore()
        with pytest.raises(ValueError, match="does not implement list_sessions"):
            await list_sessions_from_store(store, directory=DIR)

    async def test_adapter_load_error_degrades_row(self) -> None:
        """One failing load() degrades that row instead of failing the list."""

        class FlakeyStore(InMemorySessionStore):
            async def load(self, key):
                if key["session_id"] == bad_sid:
                    raise RuntimeError("backend down")
                return await super().load(key)

        store = FlakeyStore()
        good_sid = str(uuid_mod.uuid4())
        bad_sid = str(uuid_mod.uuid4())
        await _seed_chain(store, good_sid)
        await _seed_chain(store, bad_sid)

        sessions = await list_sessions_from_store(store, directory=DIR)
        by_id = {s.session_id: s for s in sessions}
        assert by_id[good_sid].summary == "prompt 0"
        # Degraded row: empty summary, mtime preserved.
        assert by_id[bad_sid].summary == ""


class TestGetSessionInfoFromStore:
    async def test_returns_info_for_seeded_session(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)

        info = await get_session_info_from_store(store, sid, directory=DIR)
        assert info is not None
        assert info.session_id == sid
        assert info.summary == "prompt 0"
        # created_at parsed from first entry's timestamp.
        assert info.created_at is not None

    async def test_returns_none_for_unknown(self) -> None:
        store = InMemorySessionStore()
        info = await get_session_info_from_store(
            store, str(uuid_mod.uuid4()), directory=DIR
        )
        assert info is None

    async def test_reflects_custom_title(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)
        await rename_session_via_store(store, sid, "My Title", directory=DIR)

        info = await get_session_info_from_store(store, sid, directory=DIR)
        assert info is not None
        assert info.custom_title == "My Title"
        assert info.summary == "My Title"


class TestGetSessionMessagesFromStore:
    async def test_returns_chain_in_order(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        uuids = await _seed_chain(store, sid, n=2)

        msgs = await get_session_messages_from_store(store, sid, directory=DIR)
        assert len(msgs) == 4
        assert [m.uuid for m in msgs] == uuids
        assert msgs[0].type == "user"
        assert msgs[1].type == "assistant"

    async def test_ignores_metadata_entries(self) -> None:
        """custom-title/tag entries from rename/tag don't appear as messages."""
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid, n=1)
        await rename_session_via_store(store, sid, "Title", directory=DIR)
        await tag_session_via_store(store, sid, "exp", directory=DIR)

        msgs = await get_session_messages_from_store(store, sid, directory=DIR)
        assert len(msgs) == 2

    async def test_limit_offset(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid, n=3)
        msgs = await get_session_messages_from_store(
            store, sid, directory=DIR, limit=2, offset=2
        )
        assert len(msgs) == 2

    async def test_unknown_session_empty(self) -> None:
        store = InMemorySessionStore()
        msgs = await get_session_messages_from_store(
            store, str(uuid_mod.uuid4()), directory=DIR
        )
        assert msgs == []


# ---------------------------------------------------------------------------
# Subagent helpers — list_subagents / get_subagent_messages
# ---------------------------------------------------------------------------


class TestSubagentsFromStore:
    async def test_list_and_get_subagent_messages(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)
        # Seed a subagent transcript at the standard subpath.
        sub_key: SessionKey = {
            "project_key": PROJECT_KEY,
            "session_id": sid,
            "subpath": "subagents/agent-abc123",
        }
        u = str(uuid_mod.uuid4())
        a = str(uuid_mod.uuid4())
        await store.append(
            sub_key,
            [
                _user("sub prompt", u, None, sid),
                _assistant("sub reply", a, u, sid),
            ],  # type: ignore[arg-type]
        )

        ids = await list_subagents_from_store(store, sid, directory=DIR)
        assert ids == ["abc123"]

        msgs = await get_subagent_messages_from_store(
            store, sid, "abc123", directory=DIR
        )
        assert len(msgs) == 2
        assert msgs[0].type == "user"
        assert msgs[1].type == "assistant"

    async def test_nested_workflow_subpath(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)
        sub_key: SessionKey = {
            "project_key": PROJECT_KEY,
            "session_id": sid,
            "subpath": "subagents/workflows/run-1/agent-nested",
        }
        u = str(uuid_mod.uuid4())
        await store.append(sub_key, [_user("hi", u, None, sid)])  # type: ignore[arg-type]

        ids = await list_subagents_from_store(store, sid, directory=DIR)
        assert ids == ["nested"]

        msgs = await get_subagent_messages_from_store(
            store, sid, "nested", directory=DIR
        )
        assert len(msgs) == 1

    async def test_filters_agent_metadata_entries(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        sub_key: SessionKey = {
            "project_key": PROJECT_KEY,
            "session_id": sid,
            "subpath": "subagents/agent-x",
        }
        u = str(uuid_mod.uuid4())
        await store.append(
            sub_key,
            [
                {"type": "agent_metadata", "name": "x"},
                _user("hi", u, None, sid),
            ],  # type: ignore[arg-type]
        )
        msgs = await get_subagent_messages_from_store(store, sid, "x", directory=DIR)
        assert len(msgs) == 1

    async def test_list_subagents_raises_when_store_lacks_list_subkeys(self) -> None:
        store = _MinimalStore()
        sid = str(uuid_mod.uuid4())
        with pytest.raises(ValueError, match="does not implement list_subkeys"):
            await list_subagents_from_store(store, sid, directory=DIR)

    async def test_get_subagent_messages_direct_path_without_list_subkeys(self) -> None:
        """Without list_subkeys, falls back to the direct subagents/agent-<id> path."""
        store = _MinimalStore()
        sid = str(uuid_mod.uuid4())
        u = str(uuid_mod.uuid4())
        await store.append(
            {
                "project_key": PROJECT_KEY,
                "session_id": sid,
                "subpath": "subagents/agent-direct",
            },
            [_user("hi", u, None, sid)],
        )
        msgs = await get_subagent_messages_from_store(
            store, sid, "direct", directory=DIR
        )
        assert len(msgs) == 1


# ---------------------------------------------------------------------------
# Mutation helpers — rename / tag / delete / fork
# ---------------------------------------------------------------------------


class TestRenameSessionViaStore:
    async def test_appends_custom_title_entry(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)

        await rename_session_via_store(store, sid, "  New Title  ", directory=DIR)

        entries = store.get_entries({"project_key": PROJECT_KEY, "session_id": sid})
        last = entries[-1]
        assert last["type"] == "custom-title"
        assert last["customTitle"] == "New Title"
        assert last["sessionId"] == sid
        assert isinstance(last["uuid"], str)
        assert isinstance(last["timestamp"], str)

    async def test_invalid_inputs_raise(self) -> None:
        store = InMemorySessionStore()
        with pytest.raises(ValueError):
            await rename_session_via_store(store, "not-a-uuid", "t")
        with pytest.raises(ValueError):
            await rename_session_via_store(store, str(uuid_mod.uuid4()), "  ")


class TestTagSessionViaStore:
    async def test_appends_tag_entry(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)

        await tag_session_via_store(store, sid, "experiment", directory=DIR)

        last = store.get_entries({"project_key": PROJECT_KEY, "session_id": sid})[-1]
        assert last["type"] == "tag"
        assert last["tag"] == "experiment"
        assert last["sessionId"] == sid

    async def test_none_clears_tag(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)
        await tag_session_via_store(store, sid, None, directory=DIR)

        last = store.get_entries({"project_key": PROJECT_KEY, "session_id": sid})[-1]
        assert last["type"] == "tag"
        assert last["tag"] == ""

    async def test_tag_reflected_in_session_info(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)
        await tag_session_via_store(store, sid, "exp", directory=DIR)

        info = await get_session_info_from_store(store, sid, directory=DIR)
        assert info is not None
        assert info.tag == "exp"


class TestDeleteSessionViaStore:
    async def test_removes_session(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)
        assert store.size == 1

        await delete_session_via_store(store, sid, directory=DIR)
        assert store.size == 0
        assert await store.load({"project_key": PROJECT_KEY, "session_id": sid}) is None

    async def test_noop_when_store_lacks_delete(self) -> None:
        """Per the SessionStore contract, missing delete() is a no-op."""
        store = _MinimalStore()
        sid = str(uuid_mod.uuid4())
        # Should not raise.
        await delete_session_via_store(store, sid, directory=DIR)


class TestForkSessionViaStore:
    async def test_round_trips_with_new_uuids(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        src_uuids = await _seed_chain(store, sid, n=2)

        result = await fork_session_via_store(store, sid, directory=DIR)
        assert result.session_id != sid

        forked = store.get_entries(
            {"project_key": PROJECT_KEY, "session_id": result.session_id}
        )
        # 4 messages + custom-title trailer
        msg_entries = [e for e in forked if e["type"] in ("user", "assistant")]
        assert len(msg_entries) == 4
        for e in msg_entries:
            assert e["sessionId"] == result.session_id
            assert e["uuid"] not in src_uuids
            assert e["forkedFrom"]["sessionId"] == sid
        # Chain integrity: each message's parentUuid is the previous uuid.
        assert msg_entries[0]["parentUuid"] is None
        for prev, cur in zip(msg_entries, msg_entries[1:], strict=False):
            assert cur["parentUuid"] == prev["uuid"]
        # Trailing custom-title entry present.
        assert forked[-1]["type"] == "custom-title"

    async def test_fork_readable_via_get_session_messages(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid, n=2)

        result = await fork_session_via_store(store, sid, directory=DIR, title="Forked")
        msgs = await get_session_messages_from_store(
            store, result.session_id, directory=DIR
        )
        assert len(msgs) == 4

    async def test_up_to_message_id(self) -> None:
        store = InMemorySessionStore()
        sid = str(uuid_mod.uuid4())
        uuids = await _seed_chain(store, sid, n=3)

        result = await fork_session_via_store(
            store, sid, directory=DIR, up_to_message_id=uuids[1]
        )
        forked = store.get_entries(
            {"project_key": PROJECT_KEY, "session_id": result.session_id}
        )
        msg_entries = [e for e in forked if e["type"] in ("user", "assistant")]
        assert len(msg_entries) == 2

    async def test_not_found_raises(self) -> None:
        store = InMemorySessionStore()
        with pytest.raises(FileNotFoundError):
            await fork_session_via_store(store, str(uuid_mod.uuid4()), directory=DIR)
