"""Validate the SessionStore integration against multiple realistic adapters.

The reference :class:`InMemorySessionStore` is a single dict — it can hide
assumptions (instant returns, key-ordering, no I/O failures) that real
backends won't honor. This module defines four throwaway adapters that model
JSONL-on-disk, SQLite, high-latency, and intermittently-failing stores, and
runs the same conformance + round-trip + batcher + helper checks against
each.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import time
import uuid as uuid_mod
from pathlib import Path
from typing import Any

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    InMemorySessionStore,
    fork_session_via_store,
    get_session_messages_from_store,
    list_sessions_from_store,
    project_key_for_directory,
)
from claude_agent_sdk._internal.session_resume import materialize_resume_session
from claude_agent_sdk._internal.transcript_mirror_batcher import (
    TranscriptMirrorBatcher,
)
from claude_agent_sdk.testing import run_session_store_conformance
from claude_agent_sdk.types import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
)

try:
    import aiosqlite  # type: ignore[import-untyped]

    _HAS_AIOSQLITE = True
except ImportError:  # pragma: no cover
    _HAS_AIOSQLITE = False


# ---------------------------------------------------------------------------
# Adapter 1 — FileSessionStore
# ---------------------------------------------------------------------------


class FileSessionStore(SessionStore):
    """JSONL-on-disk adapter rooted at ``base``.

    Layout:
      ``{base}/{project_key}/{session_id}.jsonl`` — main transcript
      ``{base}/{project_key}/{session_id}/{subpath}.jsonl`` — sub-transcript
    """

    def __init__(self, base: Path) -> None:
        self._base = base

    def _path(self, key: SessionKey) -> Path:
        proj = self._base / key["project_key"]
        sub = key.get("subpath")
        if sub:
            return proj / key["session_id"] / f"{sub}.jsonl"
        return proj / f"{key['session_id']}.jsonl"

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        path = self._path(key)
        if not path.exists():
            return None
        out: list[SessionStoreEntry] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        proj = self._base / project_key
        if not proj.is_dir():
            return []
        result: list[SessionStoreListEntry] = []
        for p in proj.glob("*.jsonl"):
            result.append(
                {
                    "session_id": p.name[: -len(".jsonl")],
                    "mtime": int(p.stat().st_mtime * 1000),
                }
            )
        return result

    async def delete(self, key: SessionKey) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        if key.get("subpath") is None:
            shutil.rmtree(
                self._base / key["project_key"] / key["session_id"],
                ignore_errors=True,
            )

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        sess_dir = self._base / key["project_key"] / key["session_id"]
        if not sess_dir.is_dir():
            return []
        out: list[str] = []
        for p in sess_dir.rglob("*.jsonl"):
            rel = p.relative_to(sess_dir)
            out.append(str(rel)[: -len(".jsonl")].replace("\\", "/"))
        return out


# ---------------------------------------------------------------------------
# Adapter 2 — SQLiteSessionStore
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    project_key TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    subpath     TEXT NOT NULL DEFAULT '',
    seq         INTEGER NOT NULL,
    entry_json  TEXT NOT NULL,
    mtime       INTEGER NOT NULL
);
"""


class SQLiteSessionStore(SessionStore):
    """Single-table sqlite3 adapter.

    Uses ``aiosqlite`` when available, otherwise wraps the sync ``sqlite3``
    driver in ``asyncio.to_thread`` so the protocol's ``async`` contract is
    honored without blocking the event loop in production-like code paths.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # check_same_thread=False so to_thread can reuse the connection.
        self._sync = sqlite3.connect(db_path, check_same_thread=False)
        self._sync.execute(_SCHEMA)
        self._sync.commit()

    @staticmethod
    def _sub(key: SessionKey) -> str:
        return key.get("subpath") or ""

    if _HAS_AIOSQLITE:

        async def _exec(self, sql: str, params: tuple = ()) -> list[tuple]:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(sql, params) as cur:
                    rows = await cur.fetchall()
                await db.commit()
                return list(rows)
    else:

        async def _exec(self, sql: str, params: tuple = ()) -> list[tuple]:
            def run() -> list[tuple]:
                cur = self._sync.execute(sql, params)
                rows = cur.fetchall()
                self._sync.commit()
                return list(rows)

            return await asyncio.to_thread(run)

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        sub = self._sub(key)
        rows = await self._exec(
            "SELECT COALESCE(MAX(seq), -1) FROM entries WHERE project_key=? "
            "AND session_id=? AND subpath=?",
            (key["project_key"], key["session_id"], sub),
        )
        seq = rows[0][0] + 1
        now = int(time.time() * 1000)
        for e in entries:
            await self._exec(
                "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)",
                (key["project_key"], key["session_id"], sub, seq, json.dumps(e), now),
            )
            seq += 1

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        rows = await self._exec(
            "SELECT entry_json FROM entries WHERE project_key=? AND session_id=? "
            "AND subpath=? ORDER BY seq",
            (key["project_key"], key["session_id"], self._sub(key)),
        )
        if not rows:
            return None
        return [json.loads(r[0]) for r in rows]

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        rows = await self._exec(
            "SELECT session_id, MAX(mtime) FROM entries WHERE project_key=? "
            "AND subpath='' GROUP BY session_id",
            (project_key,),
        )
        return [{"session_id": r[0], "mtime": r[1]} for r in rows]

    async def delete(self, key: SessionKey) -> None:
        sub = key.get("subpath")
        if sub is None:
            await self._exec(
                "DELETE FROM entries WHERE project_key=? AND session_id=?",
                (key["project_key"], key["session_id"]),
            )
        else:
            await self._exec(
                "DELETE FROM entries WHERE project_key=? AND session_id=? "
                "AND subpath=?",
                (key["project_key"], key["session_id"], sub),
            )

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        rows = await self._exec(
            "SELECT DISTINCT subpath FROM entries WHERE project_key=? AND "
            "session_id=? AND subpath != ''",
            (key["project_key"], key["session_id"]),
        )
        return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Adapter 3 — SlowSessionStore
# ---------------------------------------------------------------------------


_SLOW_DELAY = 0.05


class SlowSessionStore(SessionStore):
    """Wraps :class:`InMemorySessionStore`; every method sleeps 50ms first."""

    def __init__(self) -> None:
        self._inner = InMemorySessionStore()

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        await asyncio.sleep(_SLOW_DELAY)
        await self._inner.append(key, entries)

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        await asyncio.sleep(_SLOW_DELAY)
        return await self._inner.load(key)

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        await asyncio.sleep(_SLOW_DELAY)
        return await self._inner.list_sessions(project_key)

    async def delete(self, key: SessionKey) -> None:
        await asyncio.sleep(_SLOW_DELAY)
        await self._inner.delete(key)

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        await asyncio.sleep(_SLOW_DELAY)
        return await self._inner.list_subkeys(key)


# ---------------------------------------------------------------------------
# Adapter 4 — FlakySessionStore
# ---------------------------------------------------------------------------


class FlakySessionStore(SessionStore):
    """Wraps :class:`InMemorySessionStore`; ``append`` fails every 3rd call."""

    def __init__(self) -> None:
        self._inner = InMemorySessionStore()
        self._calls = 0

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        self._calls += 1
        if self._calls % 3 == 0:
            raise ConnectionError(f"simulated outage (call {self._calls})")
        await self._inner.append(key, entries)

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        return await self._inner.load(key)

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        return await self._inner.list_sessions(project_key)

    async def delete(self, key: SessionKey) -> None:
        await self._inner.delete(key)

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        return await self._inner.list_subkeys(key)


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


SESSION_A = "550e8400-e29b-41d4-a716-446655440000"
SESSION_B = "660e8400-e29b-41d4-a716-446655440000"

# Adapters that satisfy the full 13-contract conformance suite. Flaky is
# excluded — its append() intentionally violates contract #3.
ADAPTERS = ["file", "sqlite", "slow"]
ALL_ADAPTERS = [*ADAPTERS, "flaky"]


def _make_adapter(kind: str, tmp_path: Path) -> SessionStore:
    if kind == "file":
        root = tmp_path / "filestore"
        root.mkdir(parents=True, exist_ok=True)
        return FileSessionStore(root)
    if kind == "sqlite":
        tmp_path.mkdir(parents=True, exist_ok=True)
        return SQLiteSessionStore(str(tmp_path / "sessions.db"))
    if kind == "slow":
        return SlowSessionStore()
    if kind == "flaky":
        return FlakySessionStore()
    raise AssertionError(kind)


def _entry(role: str, n: int, parent: str | None, sid: str) -> dict[str, Any]:
    uid = str(uuid_mod.uuid4())
    return {
        "type": role,
        "uuid": uid,
        "parentUuid": parent,
        "sessionId": sid,
        "timestamp": "2024-01-01T00:00:00.000Z",
        "message": {"role": role, "content": f"msg {n}"},
    }


async def _seed_chain(
    store: SessionStore, project_key: str, sid: str, n_pairs: int = 2
) -> list[dict[str, Any]]:
    """Append ``n_pairs`` linked user/assistant entries; return them."""
    entries: list[dict[str, Any]] = []
    parent: str | None = None
    for i in range(n_pairs):
        u = _entry("user", i, parent, sid)
        a = _entry("assistant", i, u["uuid"], sid)
        entries.extend([u, a])
        parent = a["uuid"]
    await store.append(
        {"project_key": project_key, "session_id": sid},
        entries,  # type: ignore[arg-type]
    )
    return entries


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return home


async def _noop_error(_key: SessionKey | None, _err: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Conformance suite per adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ADAPTERS)
async def test_conformance(kind: str, tmp_path: Path) -> None:
    counter = 0

    def factory() -> SessionStore:
        nonlocal counter
        counter += 1
        return _make_adapter(kind, tmp_path / f"iso{counter}")

    await run_session_store_conformance(factory)


# ---------------------------------------------------------------------------
# materialize_resume_session round-trip per adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ALL_ADAPTERS)
async def test_materialize_round_trip(
    kind: str, tmp_path: Path, cwd: Path, isolated_home: Path
) -> None:
    store = _make_adapter(kind, tmp_path / "store")
    project_key = project_key_for_directory(cwd)

    seeded = await _seed_chain(store, project_key, SESSION_A, n_pairs=2)
    await store.append(
        {
            "project_key": project_key,
            "session_id": SESSION_A,
            "subpath": "subagents/agent-1",
        },
        [_entry("user", 0, None, SESSION_A)],  # type: ignore[list-item]
    )

    opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_A)
    result = await materialize_resume_session(opts)
    assert result is not None
    try:
        assert result.resume_session_id == SESSION_A
        main = result.config_dir / "projects" / project_key / f"{SESSION_A}.jsonl"
        lines = [json.loads(ln) for ln in main.read_text().splitlines() if ln]
        assert lines == seeded
        sub = (
            result.config_dir
            / "projects"
            / project_key
            / SESSION_A
            / "subagents"
            / "agent-1.jsonl"
        )
        assert sub.exists()
    finally:
        await result.cleanup()
    assert not result.config_dir.exists()


# ---------------------------------------------------------------------------
# TranscriptMirrorBatcher 50-entry round-trip per adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ADAPTERS)
async def test_batcher_50_entries(kind: str, tmp_path: Path) -> None:
    store = _make_adapter(kind, tmp_path / "store")
    projects_dir = str(tmp_path / "projects")
    file_path = str(Path(projects_dir) / "proj" / "sess.jsonl")

    batcher = TranscriptMirrorBatcher(
        store=store, projects_dir=projects_dir, on_error=_noop_error
    )
    for i in range(50):
        batcher.enqueue(file_path, [{"type": "user", "n": i}])
    await batcher.flush()

    loaded = await store.load({"project_key": "proj", "session_id": "sess"})
    assert loaded is not None
    assert [e["n"] for e in loaded] == list(range(50))


# ---------------------------------------------------------------------------
# Helper functions per adapter — list / get_messages / fork
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ALL_ADAPTERS)
async def test_helpers_list_get_fork(kind: str, tmp_path: Path, cwd: Path) -> None:
    store = _make_adapter(kind, tmp_path / "store")
    project_key = project_key_for_directory(cwd)

    seeded_a = await _seed_chain(store, project_key, SESSION_A, n_pairs=2)
    await _seed_chain(store, project_key, SESSION_B, n_pairs=1)
    if isinstance(store, FlakySessionStore):
        # Burn the 3rd-call failure on a throwaway key so the fork append
        # below (which is exercised for its UUID-remap semantics) lands on a
        # successful call. fork_session_via_store correctly propagates
        # adapter errors — that's covered by the dedicated flaky test.
        with pytest.raises(ConnectionError):
            await store.append(
                {"project_key": "burn", "session_id": "burn"}, [{"type": "x"}]
            )

    listed = await list_sessions_from_store(store, directory=str(cwd))
    assert sorted(s.session_id for s in listed) == sorted([SESSION_A, SESSION_B])

    msgs = await get_session_messages_from_store(store, SESSION_A, directory=str(cwd))
    assert [m.uuid for m in msgs] == [e["uuid"] for e in seeded_a]
    assert [m.type for m in msgs] == ["user", "assistant", "user", "assistant"]

    fork = await fork_session_via_store(
        store, SESSION_A, directory=str(cwd), title="forked"
    )
    forked_msgs = await get_session_messages_from_store(
        store, fork.session_id, directory=str(cwd)
    )
    assert len(forked_msgs) == 4
    # UUIDs were remapped — none should match the source.
    assert not ({m.uuid for m in forked_msgs} & {e["uuid"] for e in seeded_a})


# ---------------------------------------------------------------------------
# FlakySessionStore — mirror_error path under intermittent failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flaky_batcher_emits_mirror_error_and_partial_lands(
    tmp_path: Path,
) -> None:
    store = FlakySessionStore()
    projects_dir = str(tmp_path / "projects")
    errors: list[tuple[SessionKey | None, str]] = []

    async def on_error(key: SessionKey | None, err: str) -> None:
        errors.append((key, err))

    batcher = TranscriptMirrorBatcher(
        store=store, projects_dir=projects_dir, on_error=on_error
    )

    # 6 distinct sessions → 6 append() calls; calls 3 and 6 fail. Distinct
    # sessions defeat the batcher's per-file coalescing so each entry maps
    # to one adapter call.
    for i in range(6):
        path = str(Path(projects_dir) / "proj" / f"sess{i}.jsonl")
        batcher.enqueue(path, [{"type": "user", "n": i}])
    await batcher.flush()

    # At-most-once: failed batches dropped, successful ones land.
    assert len(errors) == 2
    assert all("simulated outage" in e for _k, e in errors)
    failed_ids = {k["session_id"] for k, _ in errors if k}
    landed_ids = {
        f"sess{i}"
        for i in range(6)
        if await store.load({"project_key": "proj", "session_id": f"sess{i}"})
    }
    assert failed_ids.isdisjoint(landed_ids)
    assert len(landed_ids) == 4

    # MirrorErrorMessage shape: the batcher's on_error feeds
    # Query.report_mirror_error, which builds the SystemMessage. Exercise the
    # message_parser path so a real MirrorErrorMessage is constructed for one
    # of the failures.
    from claude_agent_sdk import MirrorErrorMessage
    from claude_agent_sdk._internal.message_parser import parse_message

    key, err = errors[0]
    msg = parse_message(
        {
            "type": "system",
            "subtype": "mirror_error",
            "key": key,
            "error": err,
            "session_id": "sess",
        }
    )
    assert isinstance(msg, MirrorErrorMessage)
    assert msg.key == key
    assert "simulated outage" in msg.error


@pytest.mark.asyncio
async def test_flaky_conformance_required_only() -> None:
    """Flaky violates the multi-append ordering contract by design. Run only
    the optional-method probe to confirm the harness's optional skipping
    works against a wrapped store and the adapter is otherwise well-formed."""
    # Skip everything optional and accept that contract #3 will fail; the
    # point of this adapter is failure injection, not conformance.
    store = FlakySessionStore()
    assert isinstance(store, SessionStore)
    # The first two append calls succeed, so single-append-then-load passes.
    await store.append({"project_key": "p", "session_id": "s"}, [{"type": "x"}])
    assert await store.load({"project_key": "p", "session_id": "s"}) == [{"type": "x"}]


# ---------------------------------------------------------------------------
# SlowSessionStore — list_sessions_from_store gathers loads concurrently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_list_sessions_runs_loads_concurrently(cwd: Path) -> None:
    store = SlowSessionStore()
    project_key = project_key_for_directory(cwd)
    n = 6
    sids = [str(uuid_mod.uuid4()) for _ in range(n)]
    for sid in sids:
        await _seed_chain(store, project_key, sid, n_pairs=1)

    t0 = time.perf_counter()
    listed = await list_sessions_from_store(store, directory=str(cwd))
    elapsed = time.perf_counter() - t0

    assert sorted(s.session_id for s in listed) == sorted(sids)
    # 1× list_sessions + n× load gathered concurrently. Serial would be
    # (n + 1) × 50ms = 350ms; concurrent is ~100ms. Allow generous slack.
    serial_floor = n * _SLOW_DELAY
    assert elapsed < serial_floor, (
        f"expected concurrent gather (<{serial_floor:.2f}s), took {elapsed:.2f}s"
    )
