"""Live-MongoDB tests for the example ``MongoDBSessionStore`` adapter.

There is no in-process MongoDB mock that faithfully exercises aggregation
and ``distinct``, so this module is **live-only**: it skips unless
``SESSION_STORE_MONGODB_URL`` is set. Each run uses a random database name
and drops it on teardown.

Run locally::

    docker run -d -p 27017:27017 mongo:latest
    SESSION_STORE_MONGODB_URL=mongodb://localhost:27017 \\
        pytest tests/test_example_mongodb_session_store.py -v
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
pymongo = pytest.importorskip(
    "pymongo", reason="pymongo not installed (pip install .[examples])"
)

MONGODB_URL = os.environ.get("SESSION_STORE_MONGODB_URL")
if not MONGODB_URL:
    pytest.skip(
        "live MongoDB e2e: set SESSION_STORE_MONGODB_URL "
        "(e.g. mongodb://localhost:27017)",
        allow_module_level=True,
    )

from pymongo import AsyncMongoClient  # noqa: E402

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
    / "mongodb_session_store.py"
)
_spec = importlib.util.spec_from_file_location(
    "_mongodb_session_store_example", _EXAMPLE_PATH
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
MongoDBSessionStore = _module.MongoDBSessionStore
MongoDBSessionStoreOptions = _module.MongoDBSessionStoreOptions


SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncMongoClient]:
    c: AsyncMongoClient = AsyncMongoClient(MONGODB_URL)
    try:
        yield c
    finally:
        await c.close()


@pytest_asyncio.fixture
async def db_name(client: AsyncMongoClient) -> AsyncIterator[str]:
    name = f"claude_test_{uuid.uuid4().hex[:8]}"
    try:
        yield name
    finally:
        await client.drop_database(name)


@pytest_asyncio.fixture
async def store(client: AsyncMongoClient, db_name: str) -> SessionStore:
    s = MongoDBSessionStore(
        options=MongoDBSessionStoreOptions(client=client, db_name=db_name)
    )
    await s.create_schema()
    return s


# ---------------------------------------------------------------------------
# Conformance harness
# ---------------------------------------------------------------------------


class TestConformance:
    @pytest.mark.asyncio
    async def test_conformance(
        self, client: AsyncMongoClient, db_name: str
    ) -> None:
        # The harness calls make_store() once per contract for isolation.
        # Give each call its own collection pair so contracts don't see each
        # other's documents; cleanup happens via the db_name teardown.
        counter = itertools.count()

        async def make_store() -> SessionStore:
            n = next(counter)
            s = MongoDBSessionStore(
                client=client,
                db_name=db_name,
                entries_collection=f"entries_{n}",
                summaries_collection=f"summaries_{n}",
            )
            await s.create_schema()
            return s

        await run_session_store_conformance(make_store)

    def test_store_implements_required_methods(self, store: SessionStore) -> None:
        """SessionStore is not @runtime_checkable; probe via _store_implements()."""
        from claude_agent_sdk._internal.session_store_validation import (
            _store_implements,
        )

        assert _store_implements(store, "append")
        assert _store_implements(store, "load")

    def test_rejects_unsafe_collection_name(
        self, client: AsyncMongoClient
    ) -> None:
        with pytest.raises(ValueError, match="must match"):
            MongoDBSessionStore(
                client=client,
                entries_collection="bad; drop",
            )
        with pytest.raises(ValueError, match="must match"):
            MongoDBSessionStore(
                client=client,
                summaries_collection="bad$col",
            )


# ---------------------------------------------------------------------------
# Full round-trip: TranscriptMirrorBatcher → MongoDB → materialize_resume_session
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
