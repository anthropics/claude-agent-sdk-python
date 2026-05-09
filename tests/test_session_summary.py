"""Tests for incremental session-summary derivation.

Covers ``fold_session_summary``, ``summary_entry_to_sdk_info``,
``InMemorySessionStore.list_session_summaries``, and the
``list_sessions_from_store`` fast path that consumes them.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

import pytest

from claude_agent_sdk import (
    InMemorySessionStore,
    SessionSummaryEntry,
    fold_session_summary,
    list_sessions_from_store,
    project_key_for_directory,
)
from claude_agent_sdk._internal.session_summary import summary_entry_to_sdk_info
from claude_agent_sdk._internal.sessions import (
    _entries_to_jsonl,
    _jsonl_to_lite,
    _parse_session_info_from_lite,
)
from claude_agent_sdk.types import SessionKey

DIR = "/workspace/project"
PROJECT_KEY = project_key_for_directory(DIR)
KEY: SessionKey = {
    "project_key": PROJECT_KEY,
    "session_id": "11111111-1111-4111-8111-111111111111",
}


def _user(
    text: str | list[dict[str, Any]], ts: str = "2024-01-01T00:00:00.000Z", **extra: Any
) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": text},
        **extra,
    }


# ---------------------------------------------------------------------------
# fold_session_summary unit tests
# ---------------------------------------------------------------------------


class TestFoldSessionSummary:
    def test_init_from_none(self) -> None:
        s = fold_session_summary(None, KEY, [])
        assert s == {"session_id": KEY["session_id"], "mtime": 0, "data": {}}

    def test_set_once_fields_freeze(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [
                {
                    "type": "x",
                    "timestamp": "2024-01-01T00:00:00.000Z",
                    "cwd": "/a",
                    "isSidechain": False,
                },
                {"type": "x", "timestamp": "2024-01-01T00:00:05.000Z", "cwd": "/b"},
            ],
        )
        assert s["data"]["created_at"] == 1704067200000
        assert s["data"]["cwd"] == "/a"
        assert s["data"]["is_sidechain"] is False
        # Second append must not overwrite set-once fields.
        s2 = fold_session_summary(
            s,
            KEY,
            [
                {
                    "type": "x",
                    "timestamp": "2024-01-02T00:00:00.000Z",
                    "cwd": "/c",
                    "isSidechain": True,
                }
            ],
        )
        assert s2["data"]["created_at"] == 1704067200000
        assert s2["data"]["cwd"] == "/a"
        assert s2["data"]["is_sidechain"] is False

    def test_last_wins_overwrite(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [
                {
                    "type": "x",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "customTitle": "t1",
                    "gitBranch": "main",
                },
                {"type": "x", "timestamp": "2024-01-01T00:00:01Z", "customTitle": "t2"},
            ],
        )
        assert s["data"]["custom_title"] == "t2"
        assert s["data"]["git_branch"] == "main"
        s2 = fold_session_summary(
            s,
            KEY,
            [
                {
                    "type": "x",
                    "aiTitle": "ai",
                    "lastPrompt": "lp",
                    "summary": "sm",
                    "gitBranch": "dev",
                }
            ],
        )
        assert s2["data"]["custom_title"] == "t2"
        assert s2["data"]["ai_title"] == "ai"
        assert s2["data"]["last_prompt"] == "lp"
        assert s2["data"]["summary_hint"] == "sm"
        assert s2["data"]["git_branch"] == "dev"

    def test_mtime_not_derived_from_entries(self) -> None:
        """The fold must not touch mtime — it is the sidecar's storage write
        time, stamped by the adapter at persist time, on the same clock as
        list_sessions().mtime. Deriving it from entry ISO timestamps would
        make every batched-write sidecar appear strictly older than the
        session's current mtime, defeating the fast-path staleness check."""
        s = fold_session_summary(
            None,
            KEY,
            [
                {"type": "x", "timestamp": "2024-01-01T00:00:05.000Z"},
                {"type": "x", "timestamp": "2024-01-01T00:00:01.000Z"},
            ],
        )
        # New session: fold returns mtime=0 placeholder, adapter must stamp.
        assert s["mtime"] == 0

        # Carry-over: prev mtime is preserved verbatim regardless of entry
        # timestamps in the new batch.
        prev: SessionSummaryEntry = {
            "session_id": KEY["session_id"],
            "mtime": 42,
            "data": {},
        }
        s2 = fold_session_summary(
            prev, KEY, [{"type": "x", "timestamp": "2024-01-01T00:00:10.000Z"}]
        )
        assert s2["mtime"] == 42

    def test_tag_set_and_clear(self) -> None:
        s = fold_session_summary(None, KEY, [{"type": "tag", "tag": "wip"}])
        assert s["data"]["tag"] == "wip"
        s2 = fold_session_summary(s, KEY, [{"type": "tag", "tag": ""}])
        assert "tag" not in s2["data"]
        # Non-tag entries with a "tag" key (e.g. tool_use input) are ignored.
        s3 = fold_session_summary(s, KEY, [{"type": "user", "tag": "ignored"}])
        assert s3["data"]["tag"] == "wip"

    def test_sidechain_from_first_entry(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [{"type": "x", "timestamp": "2024-01-01T00:00:00Z", "isSidechain": True}],
        )
        assert s["data"]["is_sidechain"] is True

    def test_sidechain_latched_when_first_entry_lacks_timestamp(self) -> None:
        """Regression: is_sidechain must latch on entry 0 even if its timestamp
        is absent/unparseable, so entry 1 cannot overwrite it to False."""
        s = fold_session_summary(
            None,
            KEY,
            [
                {"type": "user", "isSidechain": True},
                {"type": "x", "timestamp": "2024-01-01T00:00:00Z"},
            ],
        )
        assert s["data"]["is_sidechain"] is True
        # created_at still picks up the first parseable timestamp.
        assert s["data"]["created_at"] == 1704067200000

    def test_first_prompt_skips_meta_tool_result_and_compact(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [
                _user("ignored meta", isMeta=True),
                _user("ignored compact", isCompactSummary=True),
                _user([{"type": "tool_result", "tool_use_id": "x", "content": "res"}]),
                _user("real first"),
                _user("not me"),
            ],
        )
        assert s["data"]["first_prompt"] == "real first"
        assert s["data"]["first_prompt_locked"] is True

    def test_first_prompt_command_fallback(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [
                _user("<command-name>/init</command-name> stuff"),
                _user("<command-name>/second</command-name>"),
            ],
        )
        assert s["data"].get("first_prompt_locked") is not True
        assert s["data"]["command_fallback"] == "/init"
        # A later real prompt locks it.
        s2 = fold_session_summary(s, KEY, [_user("now real")])
        assert s2["data"]["first_prompt"] == "now real"
        assert s2["data"]["first_prompt_locked"] is True

    def test_first_prompt_skip_pattern(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [_user("<local-command-stdout> some output"), _user("hello")],
        )
        assert s["data"]["first_prompt"] == "hello"

    def test_first_prompt_truncated(self) -> None:
        s = fold_session_summary(None, KEY, [_user("x" * 300)])
        assert len(s["data"]["first_prompt"]) <= 201
        assert s["data"]["first_prompt"].endswith("\u2026")

    def test_prev_is_not_mutated(self) -> None:
        prev: SessionSummaryEntry = {"session_id": "a", "mtime": 5, "data": {}}
        fold_session_summary(prev, KEY, [{"type": "x", "customTitle": "t"}])
        assert prev == {"session_id": "a", "mtime": 5, "data": {}}


# ---------------------------------------------------------------------------
# summary_entry_to_sdk_info
# ---------------------------------------------------------------------------


class TestSummaryEntryToSdkInfo:
    def test_sidechain_returns_none(self) -> None:
        assert (
            summary_entry_to_sdk_info(
                {
                    "session_id": "s",
                    "mtime": 1,
                    "data": {"is_sidechain": True, "custom_title": "t"},
                },
                None,
            )
            is None
        )

    def test_empty_summary_returns_none(self) -> None:
        assert (
            summary_entry_to_sdk_info({"session_id": "s", "mtime": 1, "data": {}}, None)
            is None
        )

    def test_precedence_chain(self) -> None:
        data: dict[str, Any] = {
            "first_prompt": "fp",
            "first_prompt_locked": True,
            "command_fallback": "/cmd",
            "summary_hint": "sh",
            "last_prompt": "lp",
            "ai_title": "ai",
            "custom_title": "ct",
        }
        base: SessionSummaryEntry = {"session_id": "s", "mtime": 1, "data": data}
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "ct" and info.custom_title == "ct"

        del data["custom_title"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "ai" and info.custom_title == "ai"

        del data["ai_title"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "lp" and info.custom_title is None

        del data["last_prompt"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "sh"

        del data["summary_hint"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "fp" and info.first_prompt == "fp"

        data["first_prompt_locked"] = False
        info = summary_entry_to_sdk_info(base, None)
        assert (
            info is not None and info.summary == "/cmd" and info.first_prompt == "/cmd"
        )

    def test_cwd_fallback_to_project_path(self) -> None:
        info = summary_entry_to_sdk_info(
            {"session_id": "s", "mtime": 1, "data": {"custom_title": "t"}}, "/proj"
        )
        assert info is not None and info.cwd == "/proj"
        info2 = summary_entry_to_sdk_info(
            {
                "session_id": "s",
                "mtime": 1,
                "data": {"custom_title": "t", "cwd": "/own"},
            },
            "/proj",
        )
        assert info2 is not None and info2.cwd == "/own"

    def test_field_passthrough(self) -> None:
        info = summary_entry_to_sdk_info(
            {
                "session_id": "s",
                "mtime": 99,
                "data": {
                    "custom_title": "t",
                    "git_branch": "main",
                    "tag": "wip",
                    "created_at": 50,
                },
            },
            None,
        )
        assert info is not None
        assert info.session_id == "s"
        assert info.last_modified == 99
        assert info.git_branch == "main"
        assert info.tag == "wip"
        assert info.created_at == 50
        # file_size is local-JSONL-only; store-backed summaries always None.
        assert info.file_size is None


# ---------------------------------------------------------------------------
# InMemorySessionStore.list_session_summaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryListSessionSummaries:
    async def test_tracks_appends(self) -> None:
        store = InMemorySessionStore()
        a: SessionKey = {"project_key": PROJECT_KEY, "session_id": "a"}
        b: SessionKey = {"project_key": PROJECT_KEY, "session_id": "b"}
        await store.append(a, [_user("hello a", ts="2024-01-01T00:00:00Z")])
        await store.append(a, [{"type": "x", "customTitle": "Title A"}])
        await store.append(b, [_user("hello b", ts="2024-01-02T00:00:00Z")])
        summaries = {
            s["session_id"]: s for s in await store.list_session_summaries(PROJECT_KEY)
        }
        assert set(summaries) == {"a", "b"}
        assert summaries["a"]["data"]["custom_title"] == "Title A"
        assert summaries["a"]["data"]["first_prompt"] == "hello a"
        assert summaries["b"]["data"]["first_prompt"] == "hello b"

    async def test_subpath_appends_ignored(self) -> None:
        store = InMemorySessionStore()
        main: SessionKey = {"project_key": PROJECT_KEY, "session_id": "m"}
        sub: SessionKey = {
            "project_key": PROJECT_KEY,
            "session_id": "m",
            "subpath": "subagents/agent-1",
        }
        await store.append(main, [_user("main prompt")])
        await store.append(
            sub, [_user("sub prompt"), {"type": "x", "customTitle": "sub"}]
        )
        summaries = await store.list_session_summaries(PROJECT_KEY)
        assert len(summaries) == 1
        assert summaries[0]["data"]["first_prompt"] == "main prompt"
        assert "custom_title" not in summaries[0]["data"]

    async def test_delete_drops_summary(self) -> None:
        store = InMemorySessionStore()
        k: SessionKey = {"project_key": PROJECT_KEY, "session_id": "x"}
        await store.append(k, [_user("hi")])
        assert len(await store.list_session_summaries(PROJECT_KEY)) == 1
        await store.delete(k)
        assert await store.list_session_summaries(PROJECT_KEY) == []

    async def test_project_isolation(self) -> None:
        store = InMemorySessionStore()
        await store.append({"project_key": "A", "session_id": "s"}, [_user("a")])
        await store.append({"project_key": "B", "session_id": "s"}, [_user("b")])
        assert len(await store.list_session_summaries("A")) == 1
        assert len(await store.list_session_summaries("B")) == 1
        assert await store.list_session_summaries("C") == []


# ---------------------------------------------------------------------------
# list_sessions_from_store integration — fast path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListSessionsFromStoreFastPath:
    async def test_fast_path_skips_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With list_session_summaries() available, load() must NOT be called."""
        store = InMemorySessionStore()
        sid_a = str(uuid_mod.uuid4())
        sid_b = str(uuid_mod.uuid4())
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_a},
            [_user("first a", ts="2024-01-01T00:00:00Z", cwd=DIR)],
        )
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_b},
            [_user("first b", ts="2024-01-02T00:00:00Z", cwd=DIR)],
        )

        async def _boom(self, key):  # noqa: ANN001
            raise AssertionError("load() must not be called on the fast path")

        monkeypatch.setattr(InMemorySessionStore, "load", _boom)

        sessions = await list_sessions_from_store(store, directory=DIR)
        assert {s.session_id for s in sessions} == {sid_a, sid_b}
        # Sorted by last_modified descending — sid_b's timestamp is newer.
        assert sessions[0].session_id == sid_b
        assert sessions[0].summary == "first b"
        assert sessions[1].first_prompt == "first a"

    async def test_fast_path_filters_sidechain_and_empty(self) -> None:
        store = InMemorySessionStore()
        sid_main = str(uuid_mod.uuid4())
        sid_side = str(uuid_mod.uuid4())
        sid_empty = str(uuid_mod.uuid4())
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_main},
            [_user("hello", ts="2024-01-01T00:00:00Z")],
        )
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_side},
            [
                {
                    "type": "user",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "isSidechain": True,
                    "message": {"content": "x"},
                }
            ],
        )
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_empty},
            [{"type": "x", "timestamp": "2024-01-01T00:00:00Z"}],
        )
        sessions = await list_sessions_from_store(store, directory=DIR)
        assert {s.session_id for s in sessions} == {sid_main}

    async def test_fast_path_limit_offset(self) -> None:
        store = InMemorySessionStore()
        sids = [str(uuid_mod.uuid4()) for _ in range(5)]
        for i, sid in enumerate(sids):
            await store.append(
                {"project_key": PROJECT_KEY, "session_id": sid},
                [_user(f"p{i}", ts=f"2024-01-0{i + 1}T00:00:00Z")],
            )
        page = await list_sessions_from_store(store, directory=DIR, limit=2, offset=1)
        assert len(page) == 2
        assert page[0].session_id == sids[3]
        assert page[1].session_id == sids[2]

    async def test_not_implemented_falls_back_to_load(self) -> None:
        """A store that overrides list_session_summaries but raises
        NotImplementedError must fall back to the per-session load() path."""

        class FallbackStore(InMemorySessionStore):
            async def list_session_summaries(self, project_key: str):  # noqa: ANN201
                raise NotImplementedError

        store = FallbackStore()
        sid = str(uuid_mod.uuid4())
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid},
            [_user("hi", ts="2024-01-01T00:00:00Z")],
        )
        sessions = await list_sessions_from_store(store, directory=DIR)
        assert len(sessions) == 1
        assert sessions[0].summary == "hi"

    async def test_mixed_sessions_gap_filled(self) -> None:
        """A store with summaries for only SOME sessions (e.g. adopted the
        method mid-stream) must have the rest gap-filled via per-session
        load() so old sessions aren't silently dropped."""
        sid_with = str(uuid_mod.uuid4())
        sid_without = str(uuid_mod.uuid4())

        class PartialStore(InMemorySessionStore):
            load_calls: list[str] = []

            async def list_session_summaries(self, project_key: str):  # noqa: ANN201
                full = await super().list_session_summaries(project_key)
                return [s for s in full if s["session_id"] == sid_with]

            async def load(self, key):  # noqa: ANN001, ANN201
                self.load_calls.append(key["session_id"])
                return await super().load(key)

        store = PartialStore()
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_with},
            [_user("has sidecar", ts="2024-01-02T00:00:00Z")],
        )
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_without},
            [_user("no sidecar", ts="2024-01-01T00:00:00Z")],
        )

        sessions = await list_sessions_from_store(store, directory=DIR)
        by_id = {s.session_id: s for s in sessions}
        assert set(by_id) == {sid_with, sid_without}
        assert by_id[sid_with].summary == "has sidecar"
        assert by_id[sid_without].summary == "no sidecar"
        # Only the missing session should have been load()ed.
        assert store.load_calls == [sid_without]
        # Merged result sorts on a single clock (storage write time). In
        # InMemorySessionStore, that's a strictly monotonic counter, so
        # sid_without (appended second) sorts newest.
        assert sessions[0].session_id == sid_without

    async def test_gap_fill_load_bounded_by_limit(self) -> None:
        """Gap-fill paginates BEFORE per-session load(), so load() count is
        bounded by page size, not total missing."""

        class CountingStore(InMemorySessionStore):
            def __init__(self) -> None:
                super().__init__()
                self.load_calls: list[str] = []

            async def list_session_summaries(self, project_key: str):  # noqa: ANN201
                full = await super().list_session_summaries(project_key)
                return [s for s in full if s["session_id"] == sid_with]

            async def load(self, key):  # noqa: ANN001, ANN201
                self.load_calls.append(key["session_id"])
                return await super().load(key)

        store = CountingStore()
        sid_with = str(uuid_mod.uuid4())
        # 5 sessions without sidecars. InMemorySessionStore stamps storage
        # mtime strictly monotonically per append, so these first 5 appends
        # are all older than sid_with below.
        sids_without = [str(uuid_mod.uuid4()) for _ in range(5)]
        for i, sid in enumerate(sids_without):
            await store.append(
                {"project_key": PROJECT_KEY, "session_id": sid},
                [_user(f"without {i}", ts=f"2024-01-0{i + 1}T00:00:00Z")],
            )
        # Append sid_with last so storage mtime makes it the newest session.
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_with},
            [_user("with", ts="2024-01-10T00:00:00Z")],
        )

        page = await list_sessions_from_store(store, directory=DIR, limit=2)
        # Page = newest 2: sid_with (sidecar) + newest 1 missing.
        assert len(page) == 2
        assert page[0].session_id == sid_with
        # load() bounded by page size (≤2), not total missing (5).
        assert len(store.load_calls) <= 2
        # Specifically, only the one placeholder in the page was loaded.
        assert len(store.load_calls) == 1

    async def test_sidechain_summary_does_not_consume_page_slot(self) -> None:
        """Summary-backed sidechain/empty sessions are dropped BEFORE
        pagination (free — already determined from the sidecar) so they don't
        consume offset/limit positions. Matches the disk and slow-path
        filter-then-paginate semantics. Only gap-fill placeholders that
        resolve to None after load can short-page."""
        store = InMemorySessionStore()
        sids = [str(uuid_mod.uuid4()) for _ in range(3)]
        # Append order determines storage mtime (InMemorySessionStore
        # monotonic counter). We append the two real sessions first and the
        # sidechain LAST so the sidechain is the newest-by-storage-mtime.
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sids[2]},
            [_user("real 2", ts="2024-01-01T00:00:00Z")],
        )
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sids[1]},
            [_user("real 1", ts="2024-01-02T00:00:00Z")],
        )
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sids[0]},
            [
                {
                    "type": "user",
                    "timestamp": "2024-01-03T00:00:00Z",
                    "isSidechain": True,
                    "message": {"content": "x"},
                }
            ],
        )

        page = await list_sessions_from_store(store, directory=DIR, limit=2)
        # The sidechain summary is pre-filtered, so limit=2 returns both real
        # sessions — full page, matching the slow path. sids[1] was appended
        # after sids[2] and is therefore newer by storage mtime.
        assert len(page) == 2
        assert [s.session_id for s in page] == [sids[1], sids[2]]

    async def test_stale_sidecar_triggers_gap_fill(self) -> None:
        """A sidecar whose mtime lags the session's current mtime from
        list_sessions must be treated as missing: route it through gap-fill so
        the SDK re-folds from source entries and the result reflects fresh
        transcript state, not the stale sidecar values."""
        sid = str(uuid_mod.uuid4())
        stale_mtime = 1_704_067_260_000  # 2024-01-01T00:01:00Z
        fresh_mtime = 1_704_153_660_000  # 2024-01-02T00:01:00Z

        class StaleSidecarStore(InMemorySessionStore):
            """Reports fresh transcript state but a stale summary sidecar."""

            def __init__(self) -> None:
                super().__init__()
                self.load_calls: list[str] = []

            async def list_session_summaries(self, project_key):  # noqa: ANN001, ANN201
                # Serve a stale summary reflecting T1 state.
                if project_key != PROJECT_KEY:
                    return []
                return [
                    {
                        "session_id": sid,
                        "mtime": stale_mtime,
                        "data": {
                            "custom_title": "old",
                            "first_prompt": "old prompt",
                            "first_prompt_locked": True,
                            "created_at": stale_mtime,
                        },
                    }
                ]

            async def load(self, key):  # noqa: ANN001, ANN201
                self.load_calls.append(key["session_id"])
                return await super().load(key)

        store = StaleSidecarStore()
        # Populate the real transcript with fresh entries so list_sessions()
        # reports the fresh mtime and a gap-fill load() yields fresh info.
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid},
            [
                _user("fresh prompt", ts="2024-01-02T00:00:00Z"),
                {
                    "type": "x",
                    "timestamp": "2024-01-02T00:01:00Z",
                    "customTitle": "fresh",
                },
            ],
        )
        # Sanity-check the setup: list_sessions reports fresh mtime.
        listed = await InMemorySessionStore.list_sessions(store, PROJECT_KEY)
        assert listed[0]["mtime"] > stale_mtime

        sessions = await list_sessions_from_store(store, directory=DIR)
        assert len(sessions) == 1
        info = sessions[0]
        assert info.session_id == sid
        # Fresh transcript state wins — stale customTitle must NOT leak through.
        assert info.custom_title == "fresh"
        assert info.summary == "fresh"
        assert info.last_modified >= fresh_mtime
        # Stale entry was routed into gap-fill, so load() was called for it.
        assert store.load_calls == [sid]

    async def test_fresh_sidecar_with_storage_newer_mtime_not_gap_filled(self) -> None:
        """The sidecar's mtime is storage write time, not entry time. For
        adapters that use native storage mtime (file mtime, S3 LastModified,
        Postgres updated_at), every successful batched append records a
        storage mtime strictly later than the last entry's ISO timestamp
        (~100ms batch cadence + network latency). The staleness check must
        NOT flag these fresh sidecars as stale — otherwise every slot routes
        through gap-fill load() and the fast path's N->1 goal is defeated.
        """
        sid = str(uuid_mod.uuid4())
        # T1: entry ISO timestamp embedded in the transcript.
        t1 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
        # T2: storage write time for both list_sessions and sidecar —
        # strictly later than T1 (batcher + network delay). The point of
        # this test is that T2 > T1 does NOT by itself mark the sidecar
        # stale: both list_sessions().mtime and sidecar.mtime come from
        # the same storage clock (T2), so summary.mtime == known.mtime.
        t2 = 1_704_067_200_250  # +250ms of persist latency

        class StorageMtimeStore(InMemorySessionStore):
            """Adapter that stamps both list_sessions and the sidecar with
            storage-native mtime — strictly later than entry ISO timestamps.
            """

            def __init__(self) -> None:
                super().__init__()
                self.load_calls: list[str] = []

            async def list_sessions(self, project_key: str):  # noqa: ANN201
                # Pretend storage mtime is T2 for every session.
                full = await super().list_sessions(project_key)
                return [{"session_id": e["session_id"], "mtime": t2} for e in full]

            async def list_session_summaries(self, project_key: str):  # noqa: ANN201
                # Sidecar mtime is also T2 (same clock).
                full = await super().list_session_summaries(project_key)
                return [
                    {"session_id": s["session_id"], "mtime": t2, "data": s["data"]}
                    for s in full
                ]

            async def load(self, key):  # noqa: ANN001, ANN201
                self.load_calls.append(key["session_id"])
                return await super().load(key)

        store = StorageMtimeStore()
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid},
            [
                _user("fresh prompt", ts="2024-01-01T00:00:00.000Z"),
                {
                    "type": "x",
                    "timestamp": "2024-01-01T00:00:00.000Z",
                    "customTitle": "fresh",
                },
            ],
        )
        # Entries carry ISO timestamps at T1; storage records the write at T2.
        # Sanity-check the adapter reports T2 from both surfaces.
        listed = await store.list_sessions(PROJECT_KEY)
        assert listed[0]["mtime"] == t2
        summ = await store.list_session_summaries(PROJECT_KEY)
        assert summ[0]["mtime"] == t2
        # Entry timestamps are at T1, strictly older than T2.
        assert t2 > t1

        sessions = await list_sessions_from_store(store, directory=DIR)
        assert len(sessions) == 1
        info = sessions[0]
        assert info.session_id == sid
        assert info.summary == "fresh"
        assert info.last_modified == t2
        # Fast path: load() must NOT have been called — summary.mtime ==
        # known.mtime means not stale, so the slot returns its summary-derived
        # info directly.
        assert store.load_calls == []

    async def test_summary_without_listing_is_dropped(self) -> None:
        """A summary for a session that list_sessions() no longer reports must
        be dropped from the fast-path result."""
        sid_real = str(uuid_mod.uuid4())
        sid_ghost = str(uuid_mod.uuid4())

        class GhostStore(InMemorySessionStore):
            async def list_sessions(self, project_key: str):  # noqa: ANN201
                full = await super().list_sessions(project_key)
                return [s for s in full if s["session_id"] != sid_ghost]

        store = GhostStore()
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_real},
            [_user("real", ts="2024-01-02T00:00:00Z")],
        )
        await store.append(
            {"project_key": PROJECT_KEY, "session_id": sid_ghost},
            [_user("ghost", ts="2024-01-01T00:00:00Z")],
        )

        sessions = await list_sessions_from_store(store, directory=DIR)
        assert {s.session_id for s in sessions} == {sid_real}

    async def test_gap_fill_bounded_concurrency(self) -> None:
        """Gap-fill reuses the bounded per-session load helper, so
        ``_STORE_LIST_LOAD_CONCURRENCY`` applies to the missing-session set."""
        import asyncio

        from claude_agent_sdk._internal import sessions as _sessions

        in_flight = 0
        peak = 0
        gate = asyncio.Event()

        class PartialSlowStore(InMemorySessionStore):
            async def list_session_summaries(self, project_key: str):  # noqa: ANN201
                return []  # everything is "missing"

            async def load(self, key):  # noqa: ANN001, ANN201
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                await gate.wait()
                in_flight -= 1
                return await super().load(key)

        store = PartialSlowStore()
        n = _sessions._STORE_LIST_LOAD_CONCURRENCY * 2
        for i in range(n):
            await InMemorySessionStore.append(
                store,
                {"project_key": PROJECT_KEY, "session_id": str(uuid_mod.uuid4())},
                [_user(f"p{i}")],
            )

        task = asyncio.create_task(list_sessions_from_store(store, directory=DIR))
        for _ in range(5):
            await asyncio.sleep(0)
        assert 0 < peak <= _sessions._STORE_LIST_LOAD_CONCURRENCY
        gate.set()
        await task
        assert peak == _sessions._STORE_LIST_LOAD_CONCURRENCY


# ---------------------------------------------------------------------------
# Parity: incremental fold == batch lite-parse
# ---------------------------------------------------------------------------


class TestParityWithLiteParse:
    def test_incremental_equals_batch(self) -> None:
        """``summary_entry_to_sdk_info(fold(...))`` must equal
        ``_parse_session_info_from_lite`` on the same entry stream."""
        sid = "22222222-2222-4222-8222-222222222222"
        k: SessionKey = {"project_key": PROJECT_KEY, "session_id": sid}
        entries: list[dict[str, Any]] = [
            _user(
                "<command-name>/clear</command-name>",
                ts="2024-01-01T00:00:00.000Z",
                cwd="/work",
                gitBranch="main",
            ),
            _user("ignored", ts="2024-01-01T00:00:01.000Z", isMeta=True),
            _user("real prompt here", ts="2024-01-01T00:00:02.000Z"),
            {
                "type": "assistant",
                "timestamp": "2024-01-01T00:00:03.000Z",
                "message": {"content": [{"type": "text", "text": "ok"}]},
            },
            {
                "type": "x",
                "timestamp": "2024-01-01T00:00:04.000Z",
                "aiTitle": "AI Named",
            },
            {"type": "tag", "timestamp": "2024-01-01T00:00:05.000Z", "tag": "wip"},
            {
                "type": "x",
                "timestamp": "2024-01-01T00:00:06.000Z",
                "customTitle": "User Named",
                "gitBranch": "feature",
            },
        ]

        # Incremental — fold across two append batches to exercise carry-over.
        folded = fold_session_summary(None, k, entries[:3])
        folded = fold_session_summary(folded, k, entries[3:])
        incremental = summary_entry_to_sdk_info(folded, "/work")

        # Batch — same path list_sessions_from_store fallback uses.
        jsonl = _entries_to_jsonl(entries)
        batch = _parse_session_info_from_lite(
            sid, _jsonl_to_lite(jsonl, folded["mtime"]), "/work"
        )

        assert incremental is not None and batch is not None
        # file_size is a byte count only meaningful for the JSONL path.
        batch.file_size = None
        assert incremental == batch

    def test_parity_first_prompt_only(self) -> None:
        sid = "33333333-3333-4333-8333-333333333333"
        k: SessionKey = {"project_key": PROJECT_KEY, "session_id": sid}
        entries: list[dict[str, Any]] = [
            _user("just a prompt", ts="2024-02-01T00:00:00.000Z", cwd="/w"),
        ]
        folded = fold_session_summary(None, k, entries)
        incremental = summary_entry_to_sdk_info(folded, "/w")
        jsonl = _entries_to_jsonl(entries)
        batch = _parse_session_info_from_lite(
            sid, _jsonl_to_lite(jsonl, folded["mtime"]), "/w"
        )
        assert incremental is not None and batch is not None
        batch.file_size = None
        assert incremental == batch
