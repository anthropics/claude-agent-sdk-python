"""Tests for the SessionStore write path: ``--session-mirror`` flag,
``file_path_to_session_key``, ``TranscriptMirrorBatcher``, and frame peeling
in the receive loop.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
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
from claude_agent_sdk._internal.session_resume import build_mirror_batcher
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


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    """Yield to the event loop until ``predicate()`` returns truthy.

    Replaces fixed ``await asyncio.sleep(0)`` counts in eager-flush tests
    where the path from ``enqueue`` to ``store.append`` requires multiple
    event-loop turns under lock contention. See #928 for the original
    flakiness analysis.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(
                f"_wait_until predicate did not become truthy within {timeout}s"
            )
        await asyncio.sleep(0)


# Patch target for the retry backoff — the batcher does ``import asyncio`` so
# patching this attribute swaps the global ``asyncio.sleep`` for the duration
# of the ``with`` block.
_BATCHER_SLEEP = "claude_agent_sdk._internal.transcript_mirror_batcher.asyncio.sleep"


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
        with patch(_BATCHER_SLEEP, new=AsyncMock()):
            await batcher.flush()  # must not raise

        assert len(errors) == 1
        assert errors[0][0] == {"project_key": "proj", "session_id": "sess"}
        assert "boom" in errors[0][1]

    @pytest.mark.asyncio
    async def test_append_timeout_calls_on_error(self) -> None:
        """Timeout → on_error fires once, append is NOT retried (1 attempt)."""
        calls: list[int] = []

        class HangingStore(InMemorySessionStore):
            async def append(self, key, entries):
                calls.append(1)
                await asyncio.Event().wait()  # never resolves

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
        sleep_mock = AsyncMock()
        with patch(_BATCHER_SLEEP, new=sleep_mock):
            await batcher.flush()
        assert len(calls) == 1  # not retried on timeout
        assert len(errors) == 1
        sleep_mock.assert_not_awaited()  # no backoff sleep

    @pytest.mark.asyncio
    async def test_append_timeout_no_concurrent_retry(self) -> None:
        """A slow append that outlives send_timeout is attempted exactly once;
        no retry overlaps the still-in-flight first call."""
        in_flight = 0
        max_in_flight = 0
        calls = 0

        class SlowStore(InMemorySessionStore):
            async def append(self, key, entries):
                nonlocal in_flight, max_in_flight, calls
                calls += 1
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                try:
                    # Outlives send_timeout=0.02 and shields against the
                    # cancellation wait_for issues — models a non-cancellable
                    # adapter (e.g. sync I/O in a thread).
                    await asyncio.shield(asyncio.sleep(0.1))
                finally:
                    in_flight -= 1

        errors: list[str] = []

        async def on_error(_key: SessionKey | None, err: str) -> None:
            errors.append(err)

        batcher = TranscriptMirrorBatcher(
            store=SlowStore(),
            projects_dir=PROJECTS_DIR,
            on_error=on_error,
            send_timeout=0.02,
        )
        batcher.enqueue(_main_path(), [{"type": "x"}])
        await batcher.flush()
        # Let any (incorrectly) shielded/retried task observe overlap.
        await asyncio.sleep(0.15)

        assert calls == 1
        assert max_in_flight == 1
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_append_retries_then_succeeds_no_error_reported(self) -> None:
        """Transient outage: append raises twice then succeeds on the 3rd
        attempt — batch is delivered, no mirror error reported."""
        attempts: list[int] = []

        class FlakyStore(InMemorySessionStore):
            async def append(self, key, entries):
                attempts.append(1)
                if len(attempts) < 3:
                    raise RuntimeError("transient")
                await super().append(key, entries)

        errors: list[tuple[SessionKey | None, str]] = []

        async def on_error(key: SessionKey | None, err: str) -> None:
            errors.append((key, err))

        store = FlakyStore()
        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=PROJECTS_DIR, on_error=on_error
        )
        batcher.enqueue(_main_path(), [{"type": "x"}])
        sleep_mock = AsyncMock()
        with patch(_BATCHER_SLEEP, new=sleep_mock):
            await batcher.flush()

        assert len(attempts) == 3
        assert errors == []
        assert await store.load({"project_key": "proj", "session_id": "sess"}) == [
            {"type": "x"}
        ]
        # Backoff schedule honoured between attempts.
        assert [c.args[0] for c in sleep_mock.await_args_list] == [0.2, 0.8]

    @pytest.mark.asyncio
    async def test_append_retries_exhausted_reports_error_once(self) -> None:
        """append raises on all 3 attempts → exactly one mirror error."""
        attempts: list[int] = []

        class AlwaysFailingStore(InMemorySessionStore):
            async def append(self, key, entries):
                attempts.append(1)
                raise RuntimeError("boom")

        errors: list[tuple[SessionKey | None, str]] = []

        async def on_error(key: SessionKey | None, err: str) -> None:
            errors.append((key, err))

        batcher = TranscriptMirrorBatcher(
            store=AlwaysFailingStore(), projects_dir=PROJECTS_DIR, on_error=on_error
        )
        batcher.enqueue(_main_path(), [{"type": "x"}])
        with patch(_BATCHER_SLEEP, new=AsyncMock()):
            await batcher.flush()

        assert len(attempts) == 3
        assert len(errors) == 1
        assert "boom" in errors[0][1]

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
# build_mirror_batcher / session_store_flush
# ---------------------------------------------------------------------------


class TestBuildMirrorBatcherFlushMode:
    """``session_store_flush`` threads through ``build_mirror_batcher`` to the
    batcher's pending thresholds: ``"batched"`` keeps the defaults,
    ``"eager"`` zeroes them so every enqueue schedules a background flush."""

    @pytest.mark.parametrize(
        ("kwargs", "want_entries", "want_bytes"),
        [
            ({}, MAX_PENDING_ENTRIES, MAX_PENDING_BYTES),
            ({"flush_mode": "batched"}, MAX_PENDING_ENTRIES, MAX_PENDING_BYTES),
            ({"flush_mode": "eager"}, 0, 0),
        ],
        ids=["default", "batched", "eager"],
    )
    def test_flush_mode_sets_thresholds(
        self, kwargs: dict[str, Any], want_entries: int, want_bytes: int
    ) -> None:
        batcher = build_mirror_batcher(
            store=InMemorySessionStore(),
            materialized=None,
            env={"CLAUDE_CONFIG_DIR": str(Path(PROJECTS_DIR).parent)},
            on_error=_noop_error,
            **kwargs,
        )
        assert batcher.max_pending_entries == want_entries
        assert batcher.max_pending_bytes == want_bytes

    @pytest.mark.asyncio
    async def test_eager_mode_flushes_per_frame(self) -> None:
        store = _RecordingStore()
        batcher = build_mirror_batcher(
            store=store,
            materialized=None,
            env={"CLAUDE_CONFIG_DIR": str(Path(PROJECTS_DIR).parent)},
            on_error=_noop_error,
            flush_mode="eager",
        )
        # Use _wait_until rather than a fixed ``sleep(0)`` count: the path
        # from enqueue() to store.append() needs multiple event-loop turns
        # under lock contention between consecutive drains. See #928.
        batcher.enqueue(_main_path(), [{"type": "user", "n": 1}])
        await _wait_until(lambda: len(store.append_calls) == 1)
        batcher.enqueue(_main_path(), [{"type": "assistant", "n": 2}])
        await _wait_until(lambda: len(store.append_calls) == 2)
        assert [e["n"] for c in store.append_calls for e in c[1]] == [1, 2]

    def test_options_default_is_batched(self) -> None:
        assert ClaudeAgentOptions().session_store_flush == "batched"


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


def _make_mock_transport(
    messages: list[dict[str, Any]], *, yields_between: int = 0
) -> Any:
    """Mock transport. ``yields_between`` inserts that many ``sleep(0)``
    cycles before each frame (except the first) — needed by eager-flush
    tests so background drain tasks have enough event-loop turns to
    complete before the next frame arrives. See #928."""
    mock_transport = AsyncMock()

    async def mock_receive():
        for msg in messages:
            for _ in range(yields_between):
                await anyio.sleep(0)
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

    def test_eager_flush_mode_appends_per_frame_before_result(self) -> None:
        """With ``session_store_flush="eager"`` each ``transcript_mirror`` frame
        is flushed as it arrives, so the store sees one ``append()`` per frame
        rather than a single coalesced batch at ``result`` time."""

        async def _test() -> None:
            store = _RecordingStore()
            frame1 = {
                "type": "transcript_mirror",
                "filePath": _main_path("p", "s"),
                "entries": [{"type": "user", "uuid": "u1"}],
            }
            frame2 = {
                "type": "transcript_mirror",
                "filePath": _main_path("p", "s"),
                "entries": [{"type": "assistant", "uuid": "a1"}],
            }
            # Yield to the event loop multiple times between frames so the
            # eager background drain scheduled by enqueue() can complete
            # before the next frame arrives — models the await on real
            # stdout I/O. Two yields aren't enough under lock contention
            # between consecutive drains (~4 needed). See #928.
            mock_transport = _make_mock_transport(
                [frame1, frame2, _ASSISTANT_MSG, _RESULT_MSG],
                yields_between=10,
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
                appends_at_assistant = None
                async for msg in query(
                    prompt="Hello",
                    options=ClaudeAgentOptions(
                        session_store=store, session_store_flush="eager"
                    ),
                ):
                    if isinstance(msg, AssistantMessage):
                        appends_at_assistant = len(store.append_calls)

            # Both frames flushed individually before the assistant message
            # was yielded (eager background flush ran while the read loop
            # awaited the next stdout line).
            assert appends_at_assistant == 2
            assert len(store.append_calls) == 2
            assert store.append_calls[0][1] == [{"type": "user", "uuid": "u1"}]
            assert store.append_calls[1][1] == [{"type": "assistant", "uuid": "a1"}]

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
                patch(_BATCHER_SLEEP, new=AsyncMock()),
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
