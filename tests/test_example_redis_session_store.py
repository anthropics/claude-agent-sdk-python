"""Tests for the Redis :class:`SessionStore` reference adapter.

Exercises ``examples/session_stores/redis_session_store.py`` against
``fakeredis`` so no Redis server is required. Covers:

- The shipped 13-contract conformance harness.
- Port of the TypeScript adapter's unit tests (key wiring, scoping, cascade).
- A full mirror → resume round-trip through :class:`TranscriptMirrorBatcher`
  and :func:`materialize_resume_session`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    SessionKey,
    SessionStore,
    project_key_for_directory,
)
from claude_agent_sdk._internal.session_resume import materialize_resume_session
from claude_agent_sdk._internal.transcript_mirror_batcher import TranscriptMirrorBatcher
from claude_agent_sdk.testing import run_session_store_conformance

# The example adapter and these tests are optional — skip the whole module
# if the [examples] dependency group isn't installed.
pytest.importorskip("redis", reason="redis not installed (pip install .[examples])")
fakeredis = pytest.importorskip(
    "fakeredis", reason="fakeredis not installed (pip install .[examples])"
)

# ---------------------------------------------------------------------------
# Import the example adapter without polluting sys.path globally.
# ---------------------------------------------------------------------------

_EXAMPLE_PATH = (
    Path(__file__).parent.parent
    / "examples"
    / "session_stores"
    / "redis_session_store.py"
)
_spec = importlib.util.spec_from_file_location(
    "_redis_session_store_example", _EXAMPLE_PATH
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
RedisSessionStore = _module.RedisSessionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def store(client: fakeredis.FakeAsyncRedis) -> SessionStore:
    return RedisSessionStore(client=client, prefix="p")


# ---------------------------------------------------------------------------
# Conformance harness
# ---------------------------------------------------------------------------


class TestConformance:
    @pytest.mark.asyncio
    async def test_conformance(self) -> None:
        # Fresh fake server per make_store() call so each contract is isolated.
        await run_session_store_conformance(
            lambda: RedisSessionStore(
                client=fakeredis.FakeAsyncRedis(decode_responses=True),
                prefix="conformance",
            )
        )

    def test_store_implements_required_methods(self, store: SessionStore) -> None:
        """SessionStore is not @runtime_checkable; probe via _store_implements()."""
        from claude_agent_sdk._internal.session_store_validation import (
            _store_implements,
        )

        assert _store_implements(store, "append")
        assert _store_implements(store, "load")


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestAppend:
    """Key-wiring assertions verified against fakeredis state.

    redis-py pipelines buffer commands and bypass ``execute_command``, so we
    assert on the resulting key types/values rather than spying on calls.
    """

    @pytest.mark.asyncio
    async def test_rpushes_json_and_zadds_session_index(
        self, client: fakeredis.FakeAsyncRedis, store: SessionStore
    ) -> None:
        await store.append(
            {"project_key": "proj", "session_id": "sess"},
            [{"type": "x", "a": 1}, {"type": "x", "b": 2}],
        )
        # Entries land at {prefix}:{project_key}:{session_id} as a list of JSON.
        assert await client.type("p:proj:sess") == "list"
        raw = await client.lrange("p:proj:sess", 0, -1)
        assert [json.loads(v) for v in raw] == [
            {"type": "x", "a": 1},
            {"type": "x", "b": 2},
        ]
        # Session index is a sorted set at {prefix}:{project_key}:__sessions.
        assert await client.type("p:proj:__sessions") == "zset"
        score = await client.zscore("p:proj:__sessions", "sess")
        assert score is not None and score > 1e12
        # No subkey set is touched for main-transcript appends.
        assert await client.exists("p:proj:sess:__subkeys") == 0

    @pytest.mark.asyncio
    async def test_subpath_sadds_subkeys_and_skips_session_index(
        self, client: fakeredis.FakeAsyncRedis, store: SessionStore
    ) -> None:
        await store.append(
            {"project_key": "proj", "session_id": "sess", "subpath": "subagents/a-1"},
            [{"type": "x", "n": 1}],
        )
        assert await client.type("p:proj:sess:subagents/a-1") == "list"
        assert await client.type("p:proj:sess:__subkeys") == "set"
        assert await client.smembers("p:proj:sess:__subkeys") == {"subagents/a-1"}
        # Subpath appends do NOT bump the session index.
        assert await client.exists("p:proj:__sessions") == 0

    @pytest.mark.asyncio
    async def test_noop_on_empty_entries(
        self, client: fakeredis.FakeAsyncRedis, store: SessionStore
    ) -> None:
        await store.append({"project_key": "proj", "session_id": "sess"}, [])
        # Nothing written — no list, no index.
        assert await client.keys("*") == []


class TestLoad:
    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_key(self, store: SessionStore) -> None:
        assert await store.load({"project_key": "proj", "session_id": "nope"}) is None

    @pytest.mark.asyncio
    async def test_round_trips_in_append_order(self, store: SessionStore) -> None:
        k: SessionKey = {"project_key": "proj", "session_id": "sess"}
        await store.append(k, [{"type": "x", "n": 0}, {"type": "x", "n": 1}])
        await store.append(k, [{"type": "x", "n": 2}])
        assert await store.load(k) == [
            {"type": "x", "n": 0},
            {"type": "x", "n": 1},
            {"type": "x", "n": 2},
        ]

    @pytest.mark.asyncio
    async def test_skips_malformed_json(
        self, client: fakeredis.FakeAsyncRedis, store: SessionStore
    ) -> None:
        await client.rpush(
            "p:proj:sess", '{"type":"x","ok":1}', "not json", '{"type":"x","ok":2}'
        )
        assert await store.load({"project_key": "proj", "session_id": "sess"}) == [
            {"type": "x", "ok": 1},
            {"type": "x", "ok": 2},
        ]


class TestListSessions:
    @pytest.mark.asyncio
    async def test_scoped_by_project_with_mtime(self, store: SessionStore) -> None:
        await store.append({"project_key": "proj", "session_id": "a"}, [{"type": "x"}])
        await store.append({"project_key": "proj", "session_id": "b"}, [{"type": "x"}])
        await store.append({"project_key": "other", "session_id": "c"}, [{"type": "x"}])
        sessions = await store.list_sessions("proj")
        assert sorted(s["session_id"] for s in sessions) == ["a", "b"]
        assert all(isinstance(s["mtime"], int) and s["mtime"] > 1e12 for s in sessions)
        assert await store.list_sessions("never-seen") == []


class TestDelete:
    @pytest.mark.asyncio
    async def test_cascade_without_subpath(self, store: SessionStore) -> None:
        base: SessionKey = {"project_key": "proj", "session_id": "sess"}
        await store.append(base, [{"type": "x", "m": 1}])
        await store.append({**base, "subpath": "a"}, [{"type": "x", "a": 1}])
        await store.append({**base, "subpath": "b"}, [{"type": "x", "b": 1}])

        await store.delete(base)

        assert await store.load(base) is None
        assert await store.load({**base, "subpath": "a"}) is None
        assert await store.load({**base, "subpath": "b"}) is None
        assert await store.list_subkeys(base) == []
        assert await store.list_sessions("proj") == []

    @pytest.mark.asyncio
    async def test_targeted_with_subpath(self, store: SessionStore) -> None:
        base: SessionKey = {"project_key": "proj", "session_id": "sess"}
        await store.append(base, [{"type": "x", "m": 1}])
        await store.append({**base, "subpath": "a"}, [{"type": "x", "a": 1}])
        await store.append({**base, "subpath": "b"}, [{"type": "x", "b": 1}])

        await store.delete({**base, "subpath": "a"})

        assert await store.load({**base, "subpath": "a"}) is None
        assert await store.load({**base, "subpath": "b"}) == [{"type": "x", "b": 1}]
        assert await store.load(base) == [{"type": "x", "m": 1}]
        assert await store.list_subkeys(base) == ["b"]


class TestListSubkeys:
    @pytest.mark.asyncio
    async def test_scoped_by_session(self, store: SessionStore) -> None:
        base: SessionKey = {"project_key": "proj", "session_id": "sess"}
        await store.append(base, [{"type": "x"}])
        await store.append({**base, "subpath": "subagents/a-1"}, [{"type": "x"}])
        await store.append({**base, "subpath": "subagents/a-2"}, [{"type": "x"}])
        await store.append(
            {"project_key": "proj", "session_id": "other", "subpath": "subagents/x"},
            [{"type": "x"}],
        )
        sub = await store.list_subkeys(base)
        assert sorted(sub) == ["subagents/a-1", "subagents/a-2"]


class TestPrefixNormalization:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ["", "p", "p:", "p:::"])
    async def test_no_double_colon_artifact(self, raw: str) -> None:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        s = RedisSessionStore(client=client, prefix=raw)
        await s.append({"project_key": "proj", "session_id": "sess"}, [{"type": "x"}])
        by_type: dict[str, str] = {}
        for k in await client.keys("*"):
            by_type[await client.type(k)] = k
        list_key = by_type["list"]
        z_key = by_type["zset"]
        assert "::" not in list_key
        assert not list_key.startswith(":")
        # Entry key is under the same prefix list_sessions()/list_subkeys() search.
        assert z_key[: -len("__sessions")] + "sess" == list_key


# ---------------------------------------------------------------------------
# Full round-trip: TranscriptMirrorBatcher → Redis → materialize_resume_session
# ---------------------------------------------------------------------------


SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_mirror_then_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Isolate ~ so _copy_auth_files doesn't touch the real config.
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

        cwd = tmp_path / "project"
        cwd.mkdir()
        project_key = project_key_for_directory(cwd)

        # One fake Redis server shared between the batcher (writer) and resume
        # (reader) so the round-trip is observable.
        server = fakeredis.FakeServer()
        writer = RedisSessionStore(
            client=fakeredis.FakeAsyncRedis(server=server, decode_responses=True),
            prefix="t",
        )

        errors: list[tuple] = []

        async def on_error(key, msg) -> None:
            errors.append((key, msg))

        projects_dir = str(tmp_path / "config" / "projects")
        batcher = TranscriptMirrorBatcher(
            store=writer, projects_dir=projects_dir, on_error=on_error
        )

        main_path = f"{projects_dir}/{project_key}/{SESSION_ID}.jsonl"
        sub_path = f"{projects_dir}/{project_key}/{SESSION_ID}/subagents/agent-1.jsonl"
        main_entries = [
            {
                "type": "user",
                "uuid": "u1",
                "message": {"role": "user", "content": "hi"},
            },
            {"type": "assistant", "uuid": "a1", "message": {"role": "assistant"}},
        ]
        sub_entries = [{"type": "user", "uuid": "su1", "isSidechain": True}]

        batcher.enqueue(main_path, main_entries)
        batcher.enqueue(sub_path, sub_entries)
        await batcher.flush()
        assert errors == []

        # Resume via a fresh client against the same server.
        reader = RedisSessionStore(
            client=fakeredis.FakeAsyncRedis(server=server, decode_responses=True),
            prefix="t",
        )
        opts = ClaudeAgentOptions(cwd=cwd, session_store=reader, resume=SESSION_ID)
        result = await materialize_resume_session(opts)
        assert result is not None
        try:
            assert result.resume_session_id == SESSION_ID
            jsonl = (
                result.config_dir / "projects" / project_key / f"{SESSION_ID}.jsonl"
            ).read_text()
            lines = [json.loads(line) for line in jsonl.splitlines()]
            assert lines == main_entries
            sub_jsonl = (
                result.config_dir
                / "projects"
                / project_key
                / SESSION_ID
                / "subagents"
                / "agent-1.jsonl"
            ).read_text()
            assert [json.loads(line) for line in sub_jsonl.splitlines()] == sub_entries
        finally:
            await result.cleanup()
