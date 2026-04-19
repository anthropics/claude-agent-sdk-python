"""Tests for the SessionStore write path: ``--session-mirror`` flag,
``file_path_to_session_key``, ``TranscriptMirrorBatcher``, and frame peeling
in the receive loop.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    InMemorySessionStore,
    MirrorErrorMessage,
    ResultMessage,
    SessionKey,
    query,
)
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.session_store import file_path_to_session_key
from claude_agent_sdk._internal.sessions import _get_projects_dir
from claude_agent_sdk._internal.transcript_mirror_batcher import (
    MAX_PENDING_BYTES,
    MAX_PENDING_ENTRIES,
    TranscriptMirrorBatcher,
)
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

# ---------------------------------------------------------------------------
# file_path_to_session_key
# ---------------------------------------------------------------------------

PROJECTS_DIR = str(Path(os.sep, "home", "user", ".claude", "projects"))


def _p(*parts: str) -> str:
    """Join path parts under PROJECTS_DIR using the native separator."""
    return str(Path(PROJECTS_DIR, *parts))


class TestFilePathToSessionKey:
    def test_main_transcript(self) -> None:
        assert file_path_to_session_key(
            _p("-home-user-repo", "abc-123.jsonl"), PROJECTS_DIR
        ) == {
            "project_key": "-home-user-repo",
            "session_id": "abc-123",
        }

    def test_subagent_transcript(self) -> None:
        path = _p("-home-user-repo", "abc-123", "subagents", "agent-xyz.jsonl")
        assert file_path_to_session_key(path, PROJECTS_DIR) == {
            "project_key": "-home-user-repo",
            "session_id": "abc-123",
            "subpath": "subagents/agent-xyz",
        }

    def test_nested_subagent_subpath(self) -> None:
        path = _p("proj", "sess", "subagents", "nested", "agent-1.jsonl")
        assert file_path_to_session_key(path, PROJECTS_DIR) == {
            "project_key": "proj",
            "session_id": "sess",
            "subpath": "subagents/nested/agent-1",
        }

    def test_outside_projects_dir_returns_none(self) -> None:
        elsewhere = str(Path(os.sep, "elsewhere", "proj", "sess.jsonl"))
        assert file_path_to_session_key(elsewhere, PROJECTS_DIR) is None

    def test_too_few_parts_returns_none(self) -> None:
        assert file_path_to_session_key(_p("proj-only.jsonl"), PROJECTS_DIR) is None

    def test_three_parts_returns_none(self) -> None:
        # <project_key>/<session_id>/<file>.jsonl is neither main (2 parts)
        # nor subagent (>=4 parts).
        assert (
            file_path_to_session_key(_p("proj", "sess", "weird.jsonl"), PROJECTS_DIR)
            is None
        )

    def test_main_transcript_without_jsonl_suffix_returns_none(self) -> None:
        assert file_path_to_session_key(_p("proj", "sess.txt"), PROJECTS_DIR) is None

    def test_relpath_value_error_returns_none(self) -> None:
        """On Windows, ``os.path.relpath`` raises ``ValueError`` when the two
        paths are on different drives. The function must catch it and return
        ``None`` so the batcher's ``_drain()`` "Never raises" contract holds.
        Patched for portability — the real raise only happens on Windows."""
        with patch("os.path.relpath", side_effect=ValueError("different drives")):
            assert file_path_to_session_key("D:\\cfg\\p\\s.jsonl", "C:\\home") is None

    def test_projects_dir_with_trailing_separator(self) -> None:
        """Parity with TS: a trailing path separator on projects_dir must
        not change the derived key (relpath normalizes it)."""
        with_slash = PROJECTS_DIR + os.sep
        assert file_path_to_session_key(
            _p("-home-user-repo", "abc-123.jsonl"), with_slash
        ) == {"project_key": "-home-user-repo", "session_id": "abc-123"}
        # And a subagent path still parses identically.
        path = _p("-home-user-repo", "abc-123", "subagents", "agent-xyz.jsonl")
        assert file_path_to_session_key(path, with_slash) == {
            "project_key": "-home-user-repo",
            "session_id": "abc-123",
            "subpath": "subagents/agent-xyz",
        }


class TestGetProjectsDirEnvOverride:
    """``_get_projects_dir`` must consult ``options.env`` before ``os.environ``
    so the batcher's ``projects_dir`` matches what the subprocess (which
    receives ``options.env`` merged on top) actually writes to."""

    def test_env_override_takes_precedence(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp_path / "ambient")}):
            assert _get_projects_dir({"CLAUDE_CONFIG_DIR": str(custom)}) == (
                custom / "projects"
            )

    def test_falls_back_to_os_environ_when_override_absent(
        self, tmp_path: Path
    ) -> None:
        ambient = tmp_path / "ambient"
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(ambient)}):
            assert _get_projects_dir({}) == ambient / "projects"
            assert _get_projects_dir(None) == ambient / "projects"

    def test_empty_string_override_ignored(self, tmp_path: Path) -> None:
        ambient = tmp_path / "ambient"
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(ambient)}):
            assert _get_projects_dir({"CLAUDE_CONFIG_DIR": ""}) == ambient / "projects"


# ---------------------------------------------------------------------------
# TranscriptMirrorBatcher
# ---------------------------------------------------------------------------


def _main_path(project: str = "proj", session: str = "sess") -> str:
    return _p(project, f"{session}.jsonl")


async def _noop_error(_key: SessionKey | None, _err: str) -> None:
    pass


class _RecordingStore(InMemorySessionStore):
    """InMemorySessionStore that records each append call separately."""

    def __init__(self) -> None:
        super().__init__()
        self.append_calls: list[tuple[SessionKey, list[Any]]] = []

    async def append(self, key: SessionKey, entries: list[Any]) -> None:
        self.append_calls.append((key, list(entries)))
        await super().append(key, entries)


class TestTranscriptMirrorBatcher:
    @pytest.mark.asyncio
    async def test_enqueue_then_flush_calls_store_append(self) -> None:
        store = _RecordingStore()
        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=PROJECTS_DIR, on_error=_noop_error
        )
        batcher.enqueue(_main_path(), [{"type": "user", "n": 1}])
        batcher.enqueue(_main_path(), [{"type": "assistant", "n": 2}])
        # Nothing flushed yet
        assert store.append_calls == []

        await batcher.flush()

        assert len(store.append_calls) == 1  # coalesced into one append
        key, entries = store.append_calls[0]
        assert key == {"project_key": "proj", "session_id": "sess"}
        assert entries == [{"type": "user", "n": 1}, {"type": "assistant", "n": 2}]

    @pytest.mark.asyncio
    async def test_empty_entries_batch_skips_append(self) -> None:
        store = _RecordingStore()
        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=PROJECTS_DIR, on_error=_noop_error
        )
        batcher.enqueue(_main_path(), [])
        await batcher.flush()
        # No append for empty batch — adapters must not see phantom keys.
        assert store.append_calls == []

    @pytest.mark.asyncio
    async def test_coalesces_per_file_path_preserving_order(self) -> None:
        store = _RecordingStore()
        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=PROJECTS_DIR, on_error=_noop_error
        )
        batcher.enqueue(_main_path("p", "a"), [{"type": "x", "n": 1}])
        batcher.enqueue(_main_path("p", "b"), [{"type": "x", "n": 2}])
        batcher.enqueue(_main_path("p", "a"), [{"type": "x", "n": 3}])
        await batcher.flush()

        assert len(store.append_calls) == 2
        assert store.append_calls[0][0]["session_id"] == "a"
        assert [e["n"] for e in store.append_calls[0][1]] == [1, 3]
        assert store.append_calls[1][0]["session_id"] == "b"
        assert [e["n"] for e in store.append_calls[1][1]] == [2]

    @pytest.mark.asyncio
    async def test_eager_flush_on_entry_count_threshold(self) -> None:
        store = _RecordingStore()
        batcher = TranscriptMirrorBatcher(
            store=store,
            projects_dir=PROJECTS_DIR,
            on_error=_noop_error,
            max_pending_entries=5,
        )
        batcher.enqueue(_main_path(), [{"type": "x"}] * 6)  # > 5
        # Eager flush is fire-and-forget — yield to let it run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(store.append_calls) == 1
        assert len(store.append_calls[0][1]) == 6

    @pytest.mark.asyncio
    async def test_eager_flush_on_byte_threshold(self) -> None:
        store = _RecordingStore()
        batcher = TranscriptMirrorBatcher(
            store=store,
            projects_dir=PROJECTS_DIR,
            on_error=_noop_error,
            max_pending_bytes=100,
        )
        batcher.enqueue(_main_path(), [{"type": "x", "blob": "a" * 200}])
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(store.append_calls) == 1

    @pytest.mark.asyncio
    async def test_default_thresholds(self) -> None:
        assert MAX_PENDING_ENTRIES == 500
        assert MAX_PENDING_BYTES == 1 << 20

    @pytest.mark.asyncio
    async def test_append_exception_calls_on_error_and_does_not_raise(self) -> None:
        class FailingStore(InMemorySessionStore):
            async def append(self, key, entries):
                raise RuntimeError("boom")

        errors: list[tuple[SessionKey | None, str]] = []

        async def on_error(key: SessionKey | None, err: str) -> None:
            errors.append((key, err))

        batcher = TranscriptMirrorBatcher(
            store=FailingStore(), projects_dir=PROJECTS_DIR, on_error=on_error
        )
        batcher.enqueue(_main_path(), [{"type": "x"}])
        await batcher.flush()  # must not raise

        assert len(errors) == 1
        assert errors[0][0] == {"project_key": "proj", "session_id": "sess"}
        assert "boom" in errors[0][1]

    @pytest.mark.asyncio
    async def test_append_timeout_calls_on_error(self) -> None:
        class HangingStore(InMemorySessionStore):
            async def append(self, key, entries):
                await asyncio.sleep(10)

        errors: list[str] = []

        async def on_error(_key: SessionKey | None, err: str) -> None:
            errors.append(err)

        batcher = TranscriptMirrorBatcher(
            store=HangingStore(),
            projects_dir=PROJECTS_DIR,
            on_error=on_error,
            send_timeout=0.05,
        )
        batcher.enqueue(_main_path(), [{"type": "x"}])
        await batcher.flush()
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_close_flushes_pending(self) -> None:
        store = _RecordingStore()
        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=PROJECTS_DIR, on_error=_noop_error
        )
        batcher.enqueue(_main_path(), [{"type": "x"}])
        await batcher.close()
        assert len(store.append_calls) == 1

    @pytest.mark.asyncio
    async def test_drain_never_raises_on_unexpected_do_flush_error(self) -> None:
        """Defense in depth: even if ``_do_flush`` raises something its own
        try/except doesn't cover, ``_drain()`` must swallow it so the receive
        loop's pre-result ``flush()`` cannot terminate the session."""
        store = _RecordingStore()
        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=PROJECTS_DIR, on_error=AsyncMock()
        )
        batcher.enqueue(_main_path(), [{"type": "x"}])
        with patch.object(batcher, "_do_flush", side_effect=RuntimeError("boom")):
            await batcher.flush()  # must not raise
        assert store.append_calls == []

    @pytest.mark.asyncio
    async def test_unmapped_file_path_is_dropped_silently(self) -> None:
        store = _RecordingStore()
        errors: list[Any] = []

        async def on_error(key: SessionKey | None, err: str) -> None:
            errors.append((key, err))

        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=PROJECTS_DIR, on_error=on_error
        )
        batcher.enqueue("/elsewhere/x.jsonl", [{"type": "x"}])
        await batcher.flush()
        assert store.append_calls == []
        assert errors == []

    @pytest.mark.asyncio
    async def test_two_eager_flushes_do_not_interleave_or_duplicate(self) -> None:
        """Parity with TS: two eager flushes triggered back-to-back (the
        second while the first is mid-append) must serialize via the lock
        — entries land once each, in enqueue order."""
        gate = asyncio.Event()
        appended: list[int] = []

        class SlowStore(InMemorySessionStore):
            async def append(self, key, entries):
                await gate.wait()
                appended.extend(e["n"] for e in entries)

        batcher = TranscriptMirrorBatcher(
            store=SlowStore(),
            projects_dir=PROJECTS_DIR,
            on_error=_noop_error,
            max_pending_entries=0,  # every enqueue triggers an eager flush
        )
        batcher.enqueue(_main_path(), [{"type": "x", "n": 1}])
        first = batcher._flush_task
        await asyncio.sleep(0)  # let first drain detach + block on gate
        batcher.enqueue(_main_path(), [{"type": "x", "n": 2}])
        second = batcher._flush_task
        assert first is not None and second is not None and first is not second

        gate.set()
        await asyncio.gather(first, second)
        assert appended == [1, 2]  # no dup, no interleave

    @pytest.mark.asyncio
    async def test_flush_awaits_in_flight_eager_flush(self) -> None:
        """Explicit flush() must serialize after a background eager flush so
        append ordering holds across the two batches."""
        order: list[int] = []
        gate = asyncio.Event()

        class SlowStore(InMemorySessionStore):
            async def append(self, key, entries):
                await gate.wait()
                order.extend(e["n"] for e in entries)

        batcher = TranscriptMirrorBatcher(
            store=SlowStore(),
            projects_dir=PROJECTS_DIR,
            on_error=_noop_error,
            max_pending_entries=1,
        )
        batcher.enqueue(_main_path(), [{"type": "x", "n": 1}, {"type": "x", "n": 2}])
        await asyncio.sleep(0)  # let eager flush start (now blocked on gate)
        batcher.enqueue(_main_path(), [{"type": "x", "n": 3}])
        flush_task = asyncio.create_task(batcher.flush())
        await asyncio.sleep(0)
        gate.set()
        await flush_task
        assert order == [1, 2, 3]


# ---------------------------------------------------------------------------
# --session-mirror CLI flag
# ---------------------------------------------------------------------------


class TestSessionMirrorFlag:
    def test_flag_present_when_session_store_set(self) -> None:
        transport = SubprocessCLITransport(
            prompt="hi",
            options=ClaudeAgentOptions(
                cli_path="/usr/bin/claude", session_store=InMemorySessionStore()
            ),
        )
        cmd = transport._build_command()
        assert "--session-mirror" in cmd

    def test_flag_absent_when_session_store_unset(self) -> None:
        transport = SubprocessCLITransport(
            prompt="hi",
            options=ClaudeAgentOptions(cli_path="/usr/bin/claude"),
        )
        cmd = transport._build_command()
        assert "--session-mirror" not in cmd


# ---------------------------------------------------------------------------
# Receive-loop integration: frame peeling, flush-before-result, mirror_error
# ---------------------------------------------------------------------------


def _make_mock_transport(messages: list[dict[str, Any]]) -> Any:
    mock_transport = AsyncMock()

    async def mock_receive():
        for msg in messages:
            yield msg

    mock_transport.read_messages = mock_receive
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)
    return mock_transport


_ASSISTANT_MSG = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "Hi"}],
        "model": "claude-3-5-sonnet-20241022",
    },
}

_RESULT_MSG = {
    "type": "result",
    "subtype": "success",
    "duration_ms": 100,
    "duration_api_ms": 80,
    "is_error": False,
    "num_turns": 1,
    "session_id": "test",
    "total_cost_usd": 0.001,
}


class TestReceiveLoopFramePeeling:
    def test_transcript_mirror_frames_not_yielded_and_store_appended(self) -> None:
        async def _test() -> None:
            store = _RecordingStore()
            mirror_frame = {
                "type": "transcript_mirror",
                "filePath": _main_path("myproj", "mysess"),
                "entries": [{"type": "user", "uuid": "u1"}],
            }
            mock_transport = _make_mock_transport(
                [mirror_frame, _ASSISTANT_MSG, mirror_frame, _RESULT_MSG]
            )

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
                patch(
                    "claude_agent_sdk._internal.session_resume._get_projects_dir",
                    return_value=PROJECTS_DIR,
                ),
            ):
                mock_cls.return_value = mock_transport
                messages = []
                async for msg in query(
                    prompt="Hello",
                    options=ClaudeAgentOptions(session_store=store),
                ):
                    messages.append(msg)

            # transcript_mirror frames must not surface to consumers
            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)

            # Both frames flushed (coalesced) to the store before result yields
            assert len(store.append_calls) == 1
            key, entries = store.append_calls[0]
            assert key == {"project_key": "myproj", "session_id": "mysess"}
            assert entries == [
                {"type": "user", "uuid": "u1"},
                {"type": "user", "uuid": "u1"},
            ]

        anyio.run(_test)

    def test_flush_happens_before_result_yields(self) -> None:
        """Store must be up-to-date by the time the consumer sees ResultMessage."""

        async def _test() -> None:
            store = _RecordingStore()
            mock_transport = _make_mock_transport(
                [
                    {
                        "type": "transcript_mirror",
                        "filePath": _main_path(),
                        "entries": [{"type": "user", "uuid": "u1"}],
                    },
                    _RESULT_MSG,
                ]
            )

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
                patch(
                    "claude_agent_sdk._internal.session_resume._get_projects_dir",
                    return_value=PROJECTS_DIR,
                ),
            ):
                mock_cls.return_value = mock_transport
                appended_before_result = None
                async for msg in query(
                    prompt="Hello",
                    options=ClaudeAgentOptions(session_store=store),
                ):
                    if isinstance(msg, ResultMessage):
                        appended_before_result = len(store.append_calls)

            assert appended_before_result == 1

        anyio.run(_test)

    def test_late_mirror_frames_after_result_still_flushed(self) -> None:
        """Parity with TS: transcript_mirror frames arriving AFTER the
        result message (late subagent writes) are still enqueued and
        flushed by the read-loop's finally-block flush on stream end."""

        async def _test() -> None:
            store = _RecordingStore()
            mock_transport = _make_mock_transport(
                [
                    _RESULT_MSG,
                    {
                        "type": "transcript_mirror",
                        "filePath": _main_path("late", "sess"),
                        "entries": [{"type": "user", "uuid": "late-u1"}],
                    },
                ]
            )
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
                patch(
                    "claude_agent_sdk._internal.session_resume._get_projects_dir",
                    return_value=PROJECTS_DIR,
                ),
            ):
                mock_cls.return_value = mock_transport
                messages = [
                    m
                    async for m in query(
                        prompt="Hello",
                        options=ClaudeAgentOptions(session_store=store),
                    )
                ]

            assert any(isinstance(m, ResultMessage) for m in messages)
            # Late frame must have been flushed via the finally-block flush.
            assert len(store.append_calls) == 1
            key, entries = store.append_calls[0]
            assert key == {"project_key": "late", "session_id": "sess"}
            assert entries == [{"type": "user", "uuid": "late-u1"}]

        anyio.run(_test)

    def test_mirror_frames_dropped_when_no_session_store(self) -> None:
        """Without a session_store the batcher isn't attached; frames are
        peeled and dropped (still not yielded), normal messages flow."""

        async def _test() -> None:
            mock_transport = _make_mock_transport(
                [
                    {
                        "type": "transcript_mirror",
                        "filePath": _main_path(),
                        "entries": [{"type": "user"}],
                    },
                    _ASSISTANT_MSG,
                    _RESULT_MSG,
                ]
            )
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport
                messages = [
                    m async for m in query(prompt="Hello", options=ClaudeAgentOptions())
                ]

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)

        anyio.run(_test)

    def test_store_append_failure_yields_mirror_error_message(self) -> None:
        async def _test() -> None:
            class FailingStore(InMemorySessionStore):
                async def append(self, key, entries):
                    raise RuntimeError("disk full")

            mock_transport = _make_mock_transport(
                [
                    {
                        "type": "transcript_mirror",
                        "filePath": _main_path(),
                        "entries": [{"type": "user"}],
                    },
                    _RESULT_MSG,
                ]
            )
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
                patch(
                    "claude_agent_sdk._internal.session_resume._get_projects_dir",
                    return_value=PROJECTS_DIR,
                ),
            ):
                mock_cls.return_value = mock_transport
                messages = [
                    m
                    async for m in query(
                        prompt="Hello",
                        options=ClaudeAgentOptions(session_store=FailingStore()),
                    )
                ]

            mirror_errors = [m for m in messages if isinstance(m, MirrorErrorMessage)]
            assert len(mirror_errors) == 1
            assert mirror_errors[0].subtype == "mirror_error"
            assert "disk full" in mirror_errors[0].error
            assert mirror_errors[0].key == {"project_key": "proj", "session_id": "sess"}
            # Non-fatal: result still yielded
            assert any(isinstance(m, ResultMessage) for m in messages)

        anyio.run(_test)


class TestQueryReportMirrorError:
    @pytest.mark.asyncio
    async def test_report_mirror_error_injects_system_message(self) -> None:
        transport = AsyncMock()
        transport.is_ready = Mock(return_value=True)
        q = Query(transport=transport, is_streaming_mode=True)
        q.report_mirror_error({"project_key": "p", "session_id": "s"}, "boom")
        # Drain one message from the receive stream
        msg = q._message_receive.receive_nowait()
        assert msg["type"] == "system"
        assert msg["subtype"] == "mirror_error"
        assert msg["error"] == "boom"
        assert msg["key"] == {"project_key": "p", "session_id": "s"}
        assert msg["session_id"] == "s"
