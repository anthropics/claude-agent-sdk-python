"""Live e2e tests for the example :class:`S3SessionStore` adapter.

These hit a REAL S3-compatible endpoint (AWS S3, MinIO, etc.) and are gated
entirely on environment variables — by default they skip. See
``examples/session_stores/README.md`` for how to point them at a local MinIO.

A random key prefix is used per test run and everything under it is removed
in fixture teardown, so concurrent runs against the same bucket don't
interfere and no objects are left behind.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import uuid as uuid_mod
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

S3_ENDPOINT = os.environ.get("SESSION_STORE_S3_ENDPOINT")
S3_BUCKET = os.environ.get("SESSION_STORE_S3_BUCKET")
S3_ACCESS_KEY = os.environ.get("SESSION_STORE_S3_ACCESS_KEY")
S3_SECRET_KEY = os.environ.get("SESSION_STORE_S3_SECRET_KEY")
S3_REGION = os.environ.get("SESSION_STORE_S3_REGION", "us-east-1")

if not all([S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY]):
    pytest.skip(
        "live S3 e2e: set SESSION_STORE_S3_ENDPOINT / _BUCKET / _ACCESS_KEY / "
        "_SECRET_KEY env vars (see examples/session_stores/README.md)",
        allow_module_level=True,
    )

boto3 = pytest.importorskip("boto3")

# examples/ is not a package — see test_example_s3_session_store.py.
sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))
from session_stores.s3_session_store import S3SessionStore  # noqa: E402

from claude_agent_sdk import ClaudeAgentOptions, project_key_for_directory  # noqa: E402
from claude_agent_sdk._internal.session_resume import (  # noqa: E402
    materialize_resume_session,
)
from claude_agent_sdk._internal.transcript_mirror_batcher import (  # noqa: E402
    TranscriptMirrorBatcher,
)
from claude_agent_sdk.testing import run_session_store_conformance  # noqa: E402
from claude_agent_sdk.types import SessionKey  # noqa: E402

SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture(scope="module")
def s3_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
    )


@pytest.fixture
def run_prefix(s3_client: Any) -> Iterator[str]:
    """Random per-test prefix; deletes everything under it on teardown."""
    prefix = f"sdk-live-{secrets.token_hex(8)}/"
    try:
        yield prefix
    finally:
        # Best-effort cleanup: paginate ListObjectsV2 and DeleteObjects in
        # batches of ≤1000 (S3's per-request limit).
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"Bucket": S3_BUCKET, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = s3_client.list_objects_v2(**kw)
            objs = [{"Key": o["Key"]} for o in resp.get("Contents") or []]
            if objs:
                s3_client.delete_objects(
                    Bucket=S3_BUCKET, Delete={"Objects": objs, "Quiet": True}
                )
            token = resp.get("NextContinuationToken")
            if not token:
                break


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return home


def _entry(role: str, n: int, parent: str | None) -> dict[str, Any]:
    return {
        "type": role,
        "uuid": str(uuid_mod.uuid4()),
        "parentUuid": parent,
        "sessionId": SESSION_ID,
        "timestamp": "2024-01-01T00:00:00.000Z",
        "message": {"role": role, "content": f"msg {n}"},
    }


# ---------------------------------------------------------------------------
# Conformance suite against the live backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_conformance(s3_client: Any, run_prefix: str) -> None:
    counter = 0

    def factory() -> S3SessionStore:
        nonlocal counter
        counter += 1
        return S3SessionStore(
            bucket=S3_BUCKET, prefix=f"{run_prefix}iso{counter}", client=s3_client
        )

    await run_session_store_conformance(factory)


# ---------------------------------------------------------------------------
# materialize_resume_session round-trip against the live backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_materialize_round_trip(
    s3_client: Any, run_prefix: str, tmp_path: Path, isolated_home: Path
) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()
    store = S3SessionStore(bucket=S3_BUCKET, prefix=run_prefix, client=s3_client)
    project_key = project_key_for_directory(cwd)

    seeded: list[dict[str, Any]] = []
    parent: str | None = None
    for i in range(2):
        u = _entry("user", i, parent)
        a = _entry("assistant", i, u["uuid"])
        seeded.extend([u, a])
        parent = a["uuid"]
    await store.append(
        {"project_key": project_key, "session_id": SESSION_ID},
        seeded,  # type: ignore[arg-type]
    )
    await store.append(
        {
            "project_key": project_key,
            "session_id": SESSION_ID,
            "subpath": "subagents/agent-1",
        },
        [_entry("user", 0, None)],  # type: ignore[list-item]
    )

    opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
    result = await materialize_resume_session(opts)
    assert result is not None
    try:
        assert result.resume_session_id == SESSION_ID
        main = result.config_dir / "projects" / project_key / f"{SESSION_ID}.jsonl"
        lines = [json.loads(ln) for ln in main.read_text().splitlines() if ln]
        assert lines == seeded
        sub = (
            result.config_dir
            / "projects"
            / project_key
            / SESSION_ID
            / "subagents"
            / "agent-1.jsonl"
        )
        assert sub.exists()
    finally:
        await result.cleanup()
    assert not result.config_dir.exists()


# ---------------------------------------------------------------------------
# TranscriptMirrorBatcher 50-entry flush against the live backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_batcher_50_entries(
    s3_client: Any, run_prefix: str, tmp_path: Path
) -> None:
    store = S3SessionStore(bucket=S3_BUCKET, prefix=run_prefix, client=s3_client)
    projects_dir = str(tmp_path / "projects")
    file_path = str(Path(projects_dir) / "proj" / "sess.jsonl")

    async def noop_error(_k: SessionKey | None, _e: str) -> None:
        pass

    batcher = TranscriptMirrorBatcher(
        store=store, projects_dir=projects_dir, on_error=noop_error
    )
    for i in range(50):
        batcher.enqueue(file_path, [{"type": "user", "n": i}])
    await batcher.flush()

    loaded = await store.load({"project_key": "proj", "session_id": "sess"})
    assert loaded is not None
    assert [e["n"] for e in loaded] == list(range(50))
