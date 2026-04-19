"""Live-Postgres tests for the example ``PostgresSessionStore`` adapter.

There is no in-process Postgres mock comparable to ``moto``/``fakeredis``, so
this module is **live-only**: it skips unless ``SESSION_STORE_POSTGRES_URL`` is
set. Each run creates a random-suffixed table and ``DROP``s it on teardown.

Run locally::

    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16-alpine
    SESSION_STORE_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres \\
        pytest tests/test_example_postgres_session_store.py -v
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

# The example adapter and these tests are optional — skip the whole module
# if the [examples] dependency group isn't installed.
asyncpg = pytest.importorskip(
    "asyncpg", reason="asyncpg not installed (pip install .[examples])"
)

POSTGRES_URL = os.environ.get("SESSION_STORE_POSTGRES_URL")
if not POSTGRES_URL:
    pytest.skip(
        "live Postgres e2e: set SESSION_STORE_POSTGRES_URL "
        "(e.g. postgresql://postgres:postgres@localhost:5432/postgres)",
        allow_module_level=True,
    )

from claude_agent_sdk import (  # noqa: E402
    ClaudeAgentOptions,
    SessionStore,
    project_key_for_directory,
)
from claude_agent_sdk._internal.session_resume import (  # noqa: E402
    materialize_resume_session,
)
from claude_agent_sdk._internal.transcript_mirror_batcher import (  # noqa: E402
    TranscriptMirrorBatcher,
)
from claude_agent_sdk.testing import run_session_store_conformance  # noqa: E402

# ---------------------------------------------------------------------------
# Import the example adapter without polluting sys.path globally.
# ---------------------------------------------------------------------------

_EXAMPLE_PATH = (
    Path(__file__).parent.parent
    / "examples"
    / "session_stores"
    / "postgres_session_store.py"
)
_spec = importlib.util.spec_from_file_location(
    "_postgres_session_store_example", _EXAMPLE_PATH
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
PostgresSessionStore = _module.PostgresSessionStore
PostgresSessionStoreOptions = _module.PostgresSessionStoreOptions


SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    p = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=4)
    try:
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture
async def live_table(pool: asyncpg.Pool) -> AsyncIterator[str]:
    """Per-test random table; CREATE on setup, DROP on teardown."""
    table = f"cas_test_{uuid.uuid4().hex[:8]}"
    store = PostgresSessionStore(pool=pool, table=table)
    await store.create_schema()
    try:
        yield table
    finally:
        await pool.execute(f"DROP TABLE IF EXISTS {table}")


@pytest_asyncio.fixture
async def store(pool: asyncpg.Pool, live_table: str) -> SessionStore:
    return PostgresSessionStore(
        options=PostgresSessionStoreOptions(pool=pool, table=live_table)
    )


# ---------------------------------------------------------------------------
# Conformance harness
# ---------------------------------------------------------------------------


class TestConformance:
    @pytest.mark.asyncio
    async def test_conformance(self, pool: asyncpg.Pool) -> None:
        # The harness calls make_store() once per contract for isolation. Give
        # each call its own table so contracts don't see each other's rows;
        # track them and DROP on exit.
        tables: list[str] = []
        counter = itertools.count()

        async def make_store() -> SessionStore:
            table = f"cas_conf_{uuid.uuid4().hex[:6]}_{next(counter)}"
            tables.append(table)
            s = PostgresSessionStore(pool=pool, table=table)
            await s.create_schema()
            return s

        try:
            await run_session_store_conformance(make_store)
        finally:
            for t in tables:
                await pool.execute(f"DROP TABLE IF EXISTS {t}")

    def test_store_implements_required_methods(self, store: SessionStore) -> None:
        """SessionStore is not @runtime_checkable; probe via _store_implements()."""
        from claude_agent_sdk._internal.session_store_validation import (
            _store_implements,
        )

        assert _store_implements(store, "append")
        assert _store_implements(store, "load")

    def test_rejects_unsafe_table_name(self, pool: asyncpg.Pool) -> None:
        with pytest.raises(ValueError, match="must match"):
            PostgresSessionStore(pool=pool, table="bad; DROP TABLE x")


# ---------------------------------------------------------------------------
# JSONB key-order semantics
# ---------------------------------------------------------------------------


class TestJsonbOrdering:
    """Postgres JSONB reorders object keys; the contract is deep-equal only."""

    @pytest.mark.asyncio
    async def test_load_is_deep_equal_not_byte_equal(self, store: SessionStore) -> None:
        # Intentionally non-sorted key order on input. JSONB will reorder
        # (shorter keys first), but dict equality is order-insensitive — so
        # the SessionStore contract holds.
        entry = {
            "type": "user",
            "zzz_long_key": 1,
            "a": 2,
            "message": {"b": 1, "aa": 2},
        }
        await store.append({"project_key": "p", "session_id": "s"}, [entry])
        loaded = await store.load({"project_key": "p", "session_id": "s"})
        assert loaded == [entry]


# ---------------------------------------------------------------------------
# Full round-trip: TranscriptMirrorBatcher → Postgres → materialize_resume_session
# ---------------------------------------------------------------------------


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_mirror_then_resume(
        self,
        store: SessionStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Isolate ~ so auth-file copying doesn't touch the real config.
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

        cwd = tmp_path / "project"
        cwd.mkdir()
        project_key = project_key_for_directory(cwd)

        errors: list[tuple] = []

        async def on_error(key, msg) -> None:
            errors.append((key, msg))

        projects_dir = str(tmp_path / "config" / "projects")
        batcher = TranscriptMirrorBatcher(
            store=store, projects_dir=projects_dir, on_error=on_error
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

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        result = await materialize_resume_session(opts)
        assert result is not None
        try:
            assert result.resume_session_id == SESSION_ID
            jsonl = (
                result.config_dir / "projects" / project_key / f"{SESSION_ID}.jsonl"
            ).read_text()
            # Deep-equal (not byte-equal): JSONB may have reordered keys, and
            # _entries_to_jsonl re-serializes with "type" hoisted first.
            assert [json.loads(line) for line in jsonl.splitlines()] == main_entries
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
