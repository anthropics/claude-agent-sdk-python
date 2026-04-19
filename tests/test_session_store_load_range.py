"""Tests for the optional :meth:`SessionStore.load_range` Protocol method.

Covers ``InMemorySessionStore.load_range`` directly and verifies that
``list_sessions_from_store`` / ``get_session_info_from_store`` prefer
``load_range`` over full ``load()`` when the adapter implements it.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

import pytest

from claude_agent_sdk import (
    InMemorySessionStore,
    get_session_info_from_store,
    get_session_messages_from_store,
    list_sessions_from_store,
    project_key_for_directory,
)
from claude_agent_sdk.types import (
    SessionKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
)

pytestmark = pytest.mark.asyncio

DIR = "/workspace/project"
PROJECT_KEY = project_key_for_directory(DIR)


# ---------------------------------------------------------------------------
# InMemorySessionStore.load_range — head/tail/overlap/empty/unknown
# ---------------------------------------------------------------------------


def _entry(n: int) -> dict[str, Any]:
    return {"type": "x", "n": n}


class TestInMemoryLoadRange:
    async def test_head_and_tail(self) -> None:
        store = InMemorySessionStore()
        key: SessionKey = {"project_key": "p", "session_id": "s"}
        await store.append(key, [_entry(i) for i in range(10)])  # type: ignore[arg-type]

        head, tail = await store.load_range(key, head=3, tail=2)  # type: ignore[misc]
        assert head == [_entry(0), _entry(1), _entry(2)]
        assert tail == [_entry(8), _entry(9)]

    async def test_zero_head_or_tail_returns_empty_list(self) -> None:
        store = InMemorySessionStore()
        key: SessionKey = {"project_key": "p", "session_id": "s"}
        await store.append(key, [_entry(i) for i in range(5)])  # type: ignore[arg-type]

        head, tail = await store.load_range(key, head=0, tail=2)  # type: ignore[misc]
        assert head == []
        assert tail == [_entry(3), _entry(4)]

        head, tail = await store.load_range(key, head=2, tail=0)  # type: ignore[misc]
        assert head == [_entry(0), _entry(1)]
        assert tail == []

    async def test_overlap_when_short(self) -> None:
        # Fewer entries than head+tail — lists may overlap. Callers handle it.
        store = InMemorySessionStore()
        key: SessionKey = {"project_key": "p", "session_id": "s"}
        await store.append(key, [_entry(i) for i in range(3)])  # type: ignore[arg-type]

        head, tail = await store.load_range(key, head=5, tail=5)  # type: ignore[misc]
        assert head == [_entry(0), _entry(1), _entry(2)]
        assert tail == [_entry(0), _entry(1), _entry(2)]

    async def test_empty_session(self) -> None:
        # Appending [] is a no-op so a session is never empty-but-present in
        # InMemorySessionStore. Seed one entry then check head=0/tail=0.
        store = InMemorySessionStore()
        key: SessionKey = {"project_key": "p", "session_id": "s"}
        await store.append(key, [_entry(0)])  # type: ignore[arg-type]
        head, tail = await store.load_range(key, head=0, tail=0)  # type: ignore[misc]
        assert head == []
        assert tail == []

    async def test_unknown_key_returns_none(self) -> None:
        store = InMemorySessionStore()
        result = await store.load_range(
            {"project_key": "p", "session_id": "never"}, head=2, tail=2
        )
        assert result is None


# ---------------------------------------------------------------------------
# Spy stores — verify call routing
# ---------------------------------------------------------------------------


class _SpyStoreWithRange(InMemorySessionStore):
    """Records whether ``load`` or ``load_range`` was called."""

    def __init__(self) -> None:
        super().__init__()
        self.load_calls = 0
        self.load_range_calls = 0

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        self.load_calls += 1
        return await super().load(key)

    async def load_range(
        self, key: SessionKey, *, head: int = 0, tail: int = 0
    ) -> tuple[list[SessionStoreEntry], list[SessionStoreEntry]] | None:
        self.load_range_calls += 1
        return await super().load_range(key, head=head, tail=tail)


class _SpyStoreWithoutRange(SessionStore):
    """Implements ``append``/``load``/``list_sessions`` but NOT ``load_range``.

    Delegates storage to an inner ``InMemorySessionStore`` while inheriting
    the Protocol's default ``load_range`` (raises ``NotImplementedError``),
    so ``_store_implements(store, "load_range")`` is ``False``.
    """

    def __init__(self) -> None:
        self._inner = InMemorySessionStore()
        self.load_calls = 0

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        await self._inner.append(key, entries)

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        self.load_calls += 1
        return await self._inner.load(key)

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        return await self._inner.list_sessions(project_key)


def _user(text: str, uid: str, parent: str | None, sid: str, ts: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uid,
        "parentUuid": parent,
        "sessionId": sid,
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def _assistant(text: str, uid: str, parent: str, sid: str, ts: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uid,
        "parentUuid": parent,
        "sessionId": sid,
        "timestamp": ts,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


async def _seed_chain(store: SessionStore, sid: str, n_pairs: int = 2) -> None:
    key: SessionKey = {"project_key": PROJECT_KEY, "session_id": sid}
    parent: str | None = None
    entries: list[dict[str, Any]] = []
    for i in range(n_pairs):
        u = str(uuid_mod.uuid4())
        a = str(uuid_mod.uuid4())
        entries.append(
            _user(f"prompt {i}", u, parent, sid, f"2024-01-01T00:00:{i:02d}Z")
        )
        entries.append(
            _assistant(f"reply {i}", a, u, sid, f"2024-01-01T00:01:{i:02d}Z")
        )
        parent = a
    await store.append(key, entries)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# list_sessions_from_store — routing
# ---------------------------------------------------------------------------


class TestListSessionsRouting:
    async def test_uses_load_range_when_implemented(self) -> None:
        store = _SpyStoreWithRange()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)

        sessions = await list_sessions_from_store(store, directory=DIR)

        assert [s.session_id for s in sessions] == [sid]
        assert sessions[0].first_prompt == "prompt 0"
        assert store.load_range_calls == 1
        assert store.load_calls == 0

    async def test_falls_back_to_load_when_absent(self) -> None:
        store = _SpyStoreWithoutRange()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)

        sessions = await list_sessions_from_store(store, directory=DIR)

        assert [s.session_id for s in sessions] == [sid]
        assert sessions[0].first_prompt == "prompt 0"
        assert store.load_calls == 1


# ---------------------------------------------------------------------------
# get_session_info_from_store — routing
# ---------------------------------------------------------------------------


class TestGetSessionInfoRouting:
    async def test_uses_load_range_when_implemented(self) -> None:
        store = _SpyStoreWithRange()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)

        info = await get_session_info_from_store(store, sid, directory=DIR)

        assert info is not None
        assert info.first_prompt == "prompt 0"
        assert store.load_range_calls == 1
        assert store.load_calls == 0

    async def test_falls_back_to_load_when_absent(self) -> None:
        store = _SpyStoreWithoutRange()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid)

        info = await get_session_info_from_store(store, sid, directory=DIR)

        assert info is not None
        assert info.first_prompt == "prompt 0"
        assert store.load_calls == 1


# ---------------------------------------------------------------------------
# get_session_messages_from_store still uses full load()
# ---------------------------------------------------------------------------


class TestGetSessionMessagesStillFullLoad:
    async def test_calls_load_not_load_range(self) -> None:
        store = _SpyStoreWithRange()
        sid = str(uuid_mod.uuid4())
        await _seed_chain(store, sid, n_pairs=3)

        msgs = await get_session_messages_from_store(store, sid, directory=DIR)

        assert len(msgs) == 6
        assert store.load_calls == 1
        assert store.load_range_calls == 0


# ---------------------------------------------------------------------------
# End-to-end: 100-entry session with first_prompt at start, title at end
# ---------------------------------------------------------------------------


class TestLargeSessionSummaryViaLoadRange:
    async def test_first_prompt_and_title_derived_from_head_tail(self) -> None:
        store = _SpyStoreWithRange()
        sid = str(uuid_mod.uuid4())
        key: SessionKey = {"project_key": PROJECT_KEY, "session_id": sid}

        # 100 entries: user/assistant chain (98) + customTitle + tag at the end.
        entries: list[dict[str, Any]] = []
        parent: str | None = None
        for i in range(49):
            u = str(uuid_mod.uuid4())
            a = str(uuid_mod.uuid4())
            ts = f"2024-01-01T00:{i:02d}:00Z"
            entries.append(_user(f"prompt {i}", u, parent, sid, ts))
            entries.append(_assistant(f"reply {i}", a, u, sid, ts))
            parent = a
        entries.append({"type": "custom-title", "customTitle": "My Big Session"})
        entries.append({"type": "tag", "tag": "important"})
        assert len(entries) == 100

        await store.append(key, entries)  # type: ignore[arg-type]

        info = await get_session_info_from_store(store, sid, directory=DIR)

        assert info is not None
        # first_prompt comes from entry[0] (within head=10 slice).
        assert info.first_prompt == "prompt 0"
        # custom_title + tag come from entries[98:100] (within tail=20 slice).
        assert info.custom_title == "My Big Session"
        assert info.summary == "My Big Session"
        assert info.tag == "important"
        # created_at parsed from first entry's timestamp.
        assert info.created_at is not None
        # Only load_range was used — full transcript never fetched.
        assert store.load_range_calls == 1
        assert store.load_calls == 0

        # And list_sessions_from_store derives the same summary via the
        # same range path.
        store.load_range_calls = 0
        sessions = await list_sessions_from_store(store, directory=DIR)
        assert len(sessions) == 1
        assert sessions[0].summary == "My Big Session"
        assert sessions[0].first_prompt == "prompt 0"
        assert store.load_range_calls == 1
        assert store.load_calls == 0
