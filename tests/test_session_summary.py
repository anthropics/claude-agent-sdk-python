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
        assert s == {"session_id": KEY["session_id"], "mtime": 0}

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
        assert s["created_at"] == 1704067200000
        assert s["cwd"] == "/a"
        assert s["is_sidechain"] is False
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
        assert s2["created_at"] == 1704067200000
        assert s2["cwd"] == "/a"
        assert s2["is_sidechain"] is False

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
        assert s["custom_title"] == "t2"
        assert s["git_branch"] == "main"
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
        assert s2["custom_title"] == "t2"
        assert s2["ai_title"] == "ai"
        assert s2["last_prompt"] == "lp"
        assert s2["summary_hint"] == "sm"
        assert s2["git_branch"] == "dev"

    def test_mtime_takes_max(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [
                {"type": "x", "timestamp": "2024-01-01T00:00:05.000Z"},
                {"type": "x", "timestamp": "2024-01-01T00:00:01.000Z"},
            ],
        )
        assert s["mtime"] == 1704067205000

    def test_tag_set_and_clear(self) -> None:
        s = fold_session_summary(None, KEY, [{"type": "tag", "tag": "wip"}])
        assert s["tag"] == "wip"
        s2 = fold_session_summary(s, KEY, [{"type": "tag", "tag": ""}])
        assert "tag" not in s2
        # Non-tag entries with a "tag" key (e.g. tool_use input) are ignored.
        s3 = fold_session_summary(s, KEY, [{"type": "user", "tag": "ignored"}])
        assert s3["tag"] == "wip"

    def test_sidechain_from_first_entry(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [{"type": "x", "timestamp": "2024-01-01T00:00:00Z", "isSidechain": True}],
        )
        assert s["is_sidechain"] is True

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
        assert s["first_prompt"] == "real first"
        assert s["first_prompt_locked"] is True

    def test_first_prompt_command_fallback(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [
                _user("<command-name>/init</command-name> stuff"),
                _user("<command-name>/second</command-name>"),
            ],
        )
        assert s.get("first_prompt_locked") is not True
        assert s["command_fallback"] == "/init"
        # A later real prompt locks it.
        s2 = fold_session_summary(s, KEY, [_user("now real")])
        assert s2["first_prompt"] == "now real"
        assert s2["first_prompt_locked"] is True

    def test_first_prompt_skip_pattern(self) -> None:
        s = fold_session_summary(
            None,
            KEY,
            [_user("<local-command-stdout> some output"), _user("hello")],
        )
        assert s["first_prompt"] == "hello"

    def test_first_prompt_truncated(self) -> None:
        s = fold_session_summary(None, KEY, [_user("x" * 300)])
        assert len(s["first_prompt"]) <= 201
        assert s["first_prompt"].endswith("\u2026")

    def test_prev_is_not_mutated(self) -> None:
        prev: SessionSummaryEntry = {"session_id": "a", "mtime": 5}
        fold_session_summary(prev, KEY, [{"type": "x", "customTitle": "t"}])
        assert prev == {"session_id": "a", "mtime": 5}


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
                    "is_sidechain": True,
                    "custom_title": "t",
                },
                None,
            )
            is None
        )

    def test_empty_summary_returns_none(self) -> None:
        assert summary_entry_to_sdk_info({"session_id": "s", "mtime": 1}, None) is None

    def test_precedence_chain(self) -> None:
        base: SessionSummaryEntry = {
            "session_id": "s",
            "mtime": 1,
            "first_prompt": "fp",
            "first_prompt_locked": True,
            "command_fallback": "/cmd",
            "summary_hint": "sh",
            "last_prompt": "lp",
            "ai_title": "ai",
            "custom_title": "ct",
        }
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "ct" and info.custom_title == "ct"

        del base["custom_title"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "ai" and info.custom_title == "ai"

        del base["ai_title"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "lp" and info.custom_title is None

        del base["last_prompt"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "sh"

        del base["summary_hint"]
        info = summary_entry_to_sdk_info(base, None)
        assert info is not None and info.summary == "fp" and info.first_prompt == "fp"

        base["first_prompt_locked"] = False
        info = summary_entry_to_sdk_info(base, None)
        assert (
            info is not None and info.summary == "/cmd" and info.first_prompt == "/cmd"
        )

    def test_cwd_fallback_to_project_path(self) -> None:
        info = summary_entry_to_sdk_info(
            {"session_id": "s", "mtime": 1, "custom_title": "t"}, "/proj"
        )
        assert info is not None and info.cwd == "/proj"
        info2 = summary_entry_to_sdk_info(
            {"session_id": "s", "mtime": 1, "custom_title": "t", "cwd": "/own"}, "/proj"
        )
        assert info2 is not None and info2.cwd == "/own"

    def test_field_passthrough(self) -> None:
        info = summary_entry_to_sdk_info(
            {
                "session_id": "s",
                "mtime": 99,
                "custom_title": "t",
                "git_branch": "main",
                "tag": "wip",
                "created_at": 50,
                "file_size": 1234,
            },
            None,
        )
        assert info is not None
        assert info.session_id == "s"
        assert info.last_modified == 99
        assert info.git_branch == "main"
        assert info.tag == "wip"
        assert info.created_at == 50
        assert info.file_size == 1234


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
        assert summaries["a"]["custom_title"] == "Title A"
        assert summaries["a"]["first_prompt"] == "hello a"
        assert summaries["b"]["first_prompt"] == "hello b"

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
        assert summaries[0]["first_prompt"] == "main prompt"
        assert "custom_title" not in summaries[0]

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
