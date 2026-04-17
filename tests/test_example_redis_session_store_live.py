"""Live-Redis end-to-end tests for the example ``RedisSessionStore`` adapter.

Skipped unless ``SESSION_STORE_REDIS_URL`` is set in the environment. Each
run writes under a random ``test-{hex}`` prefix and ``SCAN``/``DEL``s it on
teardown so the target database is left clean.

Run locally::

    docker run -d -p 6379:6379 redis:7-alpine
    SESSION_STORE_REDIS_URL=redis://localhost:6379/0 \\
        pytest tests/test_example_redis_session_store_live.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

REDIS_URL = os.environ.get("SESSION_STORE_REDIS_URL")
if not REDIS_URL:
    pytest.skip(
        "live Redis e2e: set SESSION_STORE_REDIS_URL (e.g. redis://localhost:6379/0)",
        allow_module_level=True,
    )

redis = pytest.importorskip(
    "redis", reason="redis not installed (pip install .[examples])"
)
import redis.asyncio as aredis  # noqa: E402

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

# Import the example adapter directly from examples/ without polluting sys.path.
_EXAMPLE_PATH = (
    Path(__file__).parent.parent
    / "examples"
    / "session_stores"
    / "redis_session_store.py"
)
_spec = importlib.util.spec_from_file_location(
    "_redis_session_store_example_live", _EXAMPLE_PATH
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
RedisSessionStore = _module.RedisSessionStore


SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest_asyncio.fixture
async def live_prefix() -> AsyncIterator[str]:
    """Per-test random key prefix; SCAN+DEL everything under it on teardown."""
    prefix = f"test-{uuid.uuid4().hex[:8]}"
    yield prefix
    client = aredis.from_url(REDIS_URL, decode_responses=True)
    try:
        keys = [k async for k in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


def _make_store(prefix: str) -> SessionStore:
    return RedisSessionStore(
        client=aredis.from_url(REDIS_URL, decode_responses=True),
        prefix=prefix,
    )


class TestLiveConformance:
    @pytest.mark.asyncio
    async def test_conformance(self, live_prefix: str) -> None:
        # The harness calls make_store() once per contract for isolation. Give
        # each call its own sub-prefix so contracts don't see each other's
        # leftover keys; everything stays under live_prefix for the sweep.
        import itertools

        counter = itertools.count()
        await run_session_store_conformance(
            lambda: _make_store(f"{live_prefix}:{next(counter)}")
        )


class TestLiveRoundTrip:
    @pytest.mark.asyncio
    async def test_mirror_then_resume(
        self,
        live_prefix: str,
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
        store = _make_store(live_prefix)

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
