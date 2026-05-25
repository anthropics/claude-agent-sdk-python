"""Backend-parametrized tests for the ``session_store`` code path.

The batcher, resume helpers, and store-backed listing are exercised under
both asyncio and trio so neither backend can regress the other. The
existing per-module tests (``test_transcript_mirror.py``,
``test_session_resume.py``, ``test_session_helpers_store.py``) cover
behavior in depth under pytest-asyncio; these cover the cross-backend
surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from claude_agent_sdk._internal import session_resume
from claude_agent_sdk._internal.sessions import list_sessions_from_store
from claude_agent_sdk._internal.transcript_mirror_batcher import (
    TranscriptMirrorBatcher,
)
from claude_agent_sdk.types import SessionKey, SessionStore, SessionStoreEntry

BACKENDS = ["asyncio", "trio"]


class _RecordingStore:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[tuple[SessionKey, list[SessionStoreEntry]]] = []
        self._delay = delay

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if self._delay:
            await anyio.sleep(self._delay)
        self.calls.append((key, list(entries)))


def _batcher(store: _RecordingStore, **kw: Any) -> TranscriptMirrorBatcher:
    async def _on_error(_key: SessionKey | None, _msg: str) -> None:
        return None

    return TranscriptMirrorBatcher(
        store=cast(SessionStore, store),
        projects_dir="/tmp/p",
        on_error=_on_error,
        **kw,
    )


def _entry(uid: str) -> SessionStoreEntry:
    return cast(SessionStoreEntry, {"uuid": uid, "type": "user"})


# ---------------------------------------------------------------------------
# TranscriptMirrorBatcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_batcher_eager_flush_via_spawn_detached(backend: str) -> None:
    store = _RecordingStore()

    async def _body() -> None:
        b = _batcher(store, max_pending_entries=0)
        # Threshold 0 triggers the spawn_detached eager-flush path from a
        # sync call site — this is where the asyncio-under-trio crash lived.
        b.enqueue("/tmp/p/proj/sid.jsonl", [_entry("a")])
        await anyio.sleep(0.05)

    anyio.run(_body, backend=backend)
    assert len(store.calls) == 1


@pytest.mark.parametrize("backend", BACKENDS)
def test_batcher_eager_flush_preserves_order(backend: str) -> None:
    store = _RecordingStore(delay=0.02)

    async def _body() -> None:
        b = _batcher(store, max_pending_entries=0)
        b.enqueue("/tmp/p/proj/sid.jsonl", [_entry("a")])
        await anyio.sleep(0)
        b.enqueue("/tmp/p/proj/sid.jsonl", [_entry("b")])
        await anyio.sleep(0)
        b.enqueue("/tmp/p/proj/sid.jsonl", [_entry("c")])
        await b.flush()
        await anyio.sleep(0.1)

    anyio.run(_body, backend=backend)
    seen = [e.get("uuid") for _k, entries in store.calls for e in entries]
    assert seen == ["a", "b", "c"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_batcher_timeout_reports_via_on_error(backend: str) -> None:
    store = _RecordingStore(delay=10.0)
    errors: list[str] = []

    async def _on_error(_key: SessionKey | None, msg: str) -> None:
        errors.append(msg)

    async def _body() -> None:
        b = TranscriptMirrorBatcher(
            store=cast(SessionStore, store),
            projects_dir="/tmp/p",
            on_error=_on_error,
            send_timeout=0.05,
        )
        b.enqueue("/tmp/p/proj/sid.jsonl", [_entry("a")])
        await b.flush()

    anyio.run(_body, backend=backend)
    assert len(errors) == 1


@pytest.mark.parametrize("backend", BACKENDS)
def test_batcher_close_flushes_under_cancelled_scope(backend: str) -> None:
    """``close()`` shields its final flush so the last batch reaches the
    store even when teardown runs under a cancelled scope.
    """
    store = _RecordingStore()

    async def _body() -> None:
        b = _batcher(store)
        b.enqueue("/tmp/p/proj/sid.jsonl", [_entry("a")])
        with anyio.CancelScope() as scope:
            scope.cancel()
            await b.close()

    anyio.run(_body, backend=backend)
    assert len(store.calls) == 1


# ---------------------------------------------------------------------------
# session_resume helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_timeout_times_out(backend: str) -> None:
    async def _slow() -> None:
        await anyio.sleep(10.0)

    async def _body() -> None:
        with pytest.raises(RuntimeError, match="timed out"):
            await session_resume._with_timeout(_slow(), 0.05, "test.load()")

    anyio.run(_body, backend=backend)


@pytest.mark.parametrize("backend", BACKENDS)
def test_rmtree_with_retry_under_cancelled_scope(backend: str, tmp_path: Path) -> None:
    """Cleanup runs at ``__aexit__``, often under a cancelled scope (client
    disconnect mid-turn). The happy-path rmtree must run synchronously
    before any checkpoint, or the materialized-transcript tempdir leaks.
    """
    d = tmp_path / "leak"
    d.mkdir()
    (d / "f").write_text("x")

    async def _body() -> None:
        with anyio.CancelScope() as scope:
            scope.cancel()
            await session_resume._rmtree_with_retry(d)

    anyio.run(_body, backend=backend)
    assert not d.exists()


# ---------------------------------------------------------------------------
# sessions.list_sessions_from_store
# ---------------------------------------------------------------------------


class _ListStore:
    """Minimal SessionStore for ``list_sessions_from_store``.

    ``fail_on`` maps session_id → exception to raise from ``load()`` so the
    per-row error-degrade path is exercised.
    """

    def __init__(
        self,
        sessions: dict[str, list[SessionStoreEntry]],
        fail_on: dict[str, Exception] | None = None,
    ) -> None:
        self._sessions = sessions
        self._fail_on = fail_on or {}

    async def list_sessions(self, project_key: str) -> list[dict[str, Any]]:
        return [
            {"session_id": sid, "mtime": 1000 + i}
            for i, sid in enumerate(self._sessions)
        ]

    async def load(self, key: SessionKey) -> list[SessionStoreEntry]:
        sid = key["session_id"]
        if sid in self._fail_on:
            raise self._fail_on[sid]
        return self._sessions.get(sid, [])


_SID_A = "00000000-0000-0000-0000-00000000000a"
_SID_B = "00000000-0000-0000-0000-00000000000b"


def _user_entry(text: str) -> SessionStoreEntry:
    return cast(
        SessionStoreEntry,
        {
            "uuid": "u",
            "type": "user",
            "message": {"content": text},
            "timestamp": "2024-01-01T00:00:00Z",
        },
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_list_sessions_from_store_one_load_fails(backend: str, tmp_path: Path) -> None:
    """One adapter failure degrades that row; the listing still succeeds."""
    store = _ListStore(
        {_SID_A: [_user_entry("hello")], _SID_B: [_user_entry("world")]},
        fail_on={_SID_B: RuntimeError("backend down")},
    )

    async def _body() -> list[Any]:
        return await list_sessions_from_store(
            cast(SessionStore, store), directory=str(tmp_path)
        )

    result = anyio.run(_body, backend=backend)
    sids = {r.session_id for r in result}
    assert sids == {_SID_A, _SID_B}
    by_sid = {r.session_id: r for r in result}
    assert by_sid[_SID_A].summary == "hello"
    assert by_sid[_SID_B].summary == ""
