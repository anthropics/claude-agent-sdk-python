"""The listing fallback should retain summaries, not completed transcripts."""

from __future__ import annotations

import gc
import uuid
import weakref
from typing import Any, cast

import anyio
import pytest

from claude_agent_sdk._internal import sessions
from claude_agent_sdk.types import SessionStore

pytestmark = pytest.mark.anyio


async def test_completed_transcripts_are_released_during_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TrackedJSONL(str):
        pass

    live = peak = 0

    def released() -> None:
        nonlocal live
        live -= 1

    async def load(store: SessionStore, sid: str, directory: str | None) -> str:
        nonlocal live, peak
        await anyio.sleep(0)
        # Also collect on interpreters without immediate reference counting.
        gc.collect()
        value = TrackedJSONL(
            '{"type":"user","uuid":"' + sid + '","message":{"content":"hello"}}\n'
        )
        live += 1
        peak = max(peak, live)
        weakref.finalize(value, released)
        return value

    monkeypatch.setattr(sessions, "_load_store_entries_as_jsonl", load)
    listing = [{"session_id": str(uuid.UUID(int=i + 1)), "mtime": i} for i in range(64)]
    results = await sessions._derive_infos_via_load(
        cast(SessionStore, object()), listing, "/project", "/project"
    )
    assert [result.session_id for result in results] == [
        entry["session_id"] for entry in listing
    ]
    assert [result.last_modified for result in results] == list(range(64))
    assert all(result.summary == "hello" for result in results)
    gc.collect()
    assert live == 0
    assert peak <= sessions._STORE_LIST_LOAD_CONCURRENCY


@pytest.mark.parametrize("failing_stage", ["lite", "parse"])
async def test_parse_errors_preserve_identity_input_order_and_load_completion(
    monkeypatch: pytest.MonkeyPatch, failing_stage: str
) -> None:
    listing = [{"session_id": str(i), "mtime": i} for i in range(33)]
    errors = [RuntimeError("first input error"), ValueError("second input error")]
    second_loaded = anyio.Event()
    finished: list[int] = []

    async def load(store: SessionStore, sid: str, directory: str | None) -> str:
        i = int(sid)
        if i == 0:
            await second_loaded.wait()
        elif i == 1:
            second_loaded.set()
        finished.append(i)
        return sid

    def fail_first_two(value: str) -> None:
        i = int(value)
        if i < len(errors):
            raise errors[i]

    original_lite = sessions._jsonl_to_lite

    def lite(jsonl: str, mtime: int) -> Any:
        if failing_stage == "lite":
            fail_first_two(jsonl)
        return original_lite(jsonl, mtime)

    def parse(sid: str, lite: Any, project_path: str) -> None:
        if failing_stage == "parse":
            fail_first_two(sid)
        return None

    monkeypatch.setattr(sessions, "_load_store_entries_as_jsonl", load)
    monkeypatch.setattr(sessions, "_jsonl_to_lite", lite)
    monkeypatch.setattr(sessions, "_parse_session_info_from_lite", parse)
    with anyio.fail_after(5), pytest.raises(RuntimeError) as caught:
        await sessions._derive_infos_via_load(
            cast(SessionStore, object()), listing, "/project", "/project"
        )
    assert caught.value is errors[0]
    assert finished.index(1) < finished.index(0)
    assert sorted(finished) == list(range(len(listing)))


@pytest.mark.parametrize(
    "cancelling_stage", ["_jsonl_to_lite", "_parse_session_info_from_lite"]
)
async def test_cancellation_during_derivation_releases_transcripts_and_loads(
    monkeypatch: pytest.MonkeyPatch, cancelling_stage: str
) -> None:
    class TrackedJSONL(str):
        pass

    concurrency = sessions._STORE_LIST_LOAD_CONCURRENCY
    listing = [
        {"session_id": str(uuid.UUID(int=i + 1)), "mtime": i}
        for i in range(concurrency * 2)
    ]
    started: set[str] = set()
    finished: set[str] = set()
    transcripts: list[weakref.ReferenceType[TrackedJSONL]] = []
    all_started = anyio.Event()
    stage_reached = False
    first_started: str | None = None

    async def load(store: SessionStore, sid: str, directory: str | None) -> str:
        nonlocal first_started
        if first_started is None:
            first_started = sid
        value = TrackedJSONL(
            '{"type":"user","uuid":"' + sid + '","message":{"content":"hello"}}\n'
        )
        transcripts.append(weakref.ref(value))
        started.add(sid)
        if len(started) == concurrency:
            all_started.set()
        try:
            if sid == first_started:
                await all_started.wait()
                return value
            await anyio.sleep_forever()
        finally:
            finished.add(sid)

    original_stage = getattr(sessions, cancelling_stage)

    def cancel_during_derivation(*args: Any) -> Any:
        nonlocal stage_reached
        stage_reached = True
        # Parsing is synchronous: request cancellation here, then let it be
        # delivered at the next checkpoint rather than pretending it can
        # interrupt the parser midway through its Python call.
        cancel_scope.cancel()
        return original_stage(*args)

    monkeypatch.setattr(sessions, "_load_store_entries_as_jsonl", load)
    monkeypatch.setattr(sessions, cancelling_stage, cancel_during_derivation)
    with anyio.fail_after(5):
        with anyio.CancelScope() as cancel_scope:
            await sessions._derive_infos_via_load(
                cast(SessionStore, object()), listing, "/project", "/project"
            )
            pytest.fail("listing swallowed cancellation")

    assert stage_reached
    assert cancel_scope.cancelled_caught
    assert len(started) == concurrency
    assert finished == started
    gc.collect()
    # Covers the completed transcript and payloads in cancelled load frames.
    assert all(reference() is None for reference in transcripts)
