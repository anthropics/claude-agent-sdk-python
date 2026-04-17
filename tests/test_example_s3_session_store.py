"""Tests for the example :class:`S3SessionStore` adapter.

The full conformance + round-trip + batcher checks run against a
``moto``-backed real ``boto3`` client. Unit tests that need to assert on the
exact sequence of S3 operations use :class:`_RecordingClient` (a hand-rolled
in-memory double exported alongside the adapter).
"""

from __future__ import annotations

import json
import re
import sys
import uuid as uuid_mod
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# examples/ is not a package (no __init__.py at the top level — it's a
# collection of standalone scripts). Put it on sys.path so the
# session_stores subpackage is importable regardless of where pytest is
# invoked from.
sys.path.insert(0, str(Path(__file__).parents[1] / "examples"))

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402
from session_stores.s3_session_store import (  # noqa: E402
    S3SessionStore,
    S3SessionStoreOptions,
    _RecordingClient,
)

from claude_agent_sdk import ClaudeAgentOptions, project_key_for_directory  # noqa: E402
from claude_agent_sdk._internal.session_resume import (  # noqa: E402
    materialize_resume_session,
)
from claude_agent_sdk._internal.transcript_mirror_batcher import (  # noqa: E402
    TranscriptMirrorBatcher,
)
from claude_agent_sdk.testing import run_session_store_conformance  # noqa: E402
from claude_agent_sdk.types import SessionKey  # noqa: E402

PART_NAME_RE = re.compile(r"^part-\d{13}-[0-9a-f]{6}\.jsonl$")
BUCKET = "test-bucket"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """moto requires *some* credentials in env."""
    for var, val in (
        ("AWS_ACCESS_KEY_ID", "testing"),
        ("AWS_SECRET_ACCESS_KEY", "testing"),
        ("AWS_SECURITY_TOKEN", "testing"),
        ("AWS_SESSION_TOKEN", "testing"),
        ("AWS_DEFAULT_REGION", "us-east-1"),
    ):
        monkeypatch.setenv(var, val)


@pytest.fixture
def s3_client(aws_credentials: None) -> Iterator[Any]:
    """Real boto3 client backed by moto's in-memory S3."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return home


def _calls_of(client: _RecordingClient, name: str) -> list[dict[str, Any]]:
    return [kw for op, kw in client.calls if op == name]


def _make_store(client: Any, prefix: str = "p") -> S3SessionStore:
    return S3SessionStore(bucket=BUCKET, prefix=prefix, client=client)


# ---------------------------------------------------------------------------
# Conformance suite (moto-backed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conformance(s3_client: Any) -> None:
    counter = 0

    def factory() -> S3SessionStore:
        # Distinct prefix per call → fresh state for each of the 13 contracts.
        nonlocal counter
        counter += 1
        return S3SessionStore(bucket=BUCKET, prefix=f"iso{counter}", client=s3_client)

    await run_session_store_conformance(factory)


@pytest.mark.asyncio
async def test_conformance_with_options_dataclass(s3_client: Any) -> None:
    """``S3SessionStoreOptions`` is an alternative to keyword args."""
    counter = 0

    def factory() -> S3SessionStore:
        nonlocal counter
        counter += 1
        return S3SessionStore(
            options=S3SessionStoreOptions(
                bucket=BUCKET, prefix=f"opt{counter}", client=s3_client
            )
        )

    await run_session_store_conformance(factory)


# ---------------------------------------------------------------------------
# append — part-name format / sortability / serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_part_name_format_and_sortable() -> None:
    client = _RecordingClient()
    store = _make_store(client)
    await store.append(
        {"project_key": "proj", "session_id": "sess"}, [{"type": "x", "a": 1}]
    )
    await store.append(
        {"project_key": "proj", "session_id": "sess"}, [{"type": "x", "b": 2}]
    )

    puts = _calls_of(client, "put_object")
    assert len(puts) == 2
    assert puts[0]["Bucket"] == BUCKET
    assert puts[0]["ContentType"] == "application/x-ndjson"

    k0, k1 = puts[0]["Key"], puts[1]["Key"]
    assert k0.startswith("p/proj/sess/")
    assert PART_NAME_RE.fullmatch(k0[len("p/proj/sess/") :])
    assert PART_NAME_RE.fullmatch(k1[len("p/proj/sess/") :])
    # Distinct (rand suffix prevents same-ms collision)
    assert k0 != k1
    # Lexical order matches write order (fixed-width epoch ms)
    assert k0 <= k1


@pytest.mark.asyncio
async def test_append_two_instances_distinct_part_names() -> None:
    client = _RecordingClient()
    a = _make_store(client)
    b = _make_store(client)
    await a.append(
        {"project_key": "proj", "session_id": "sess"}, [{"type": "x", "n": 1}]
    )
    await b.append(
        {"project_key": "proj", "session_id": "sess"}, [{"type": "x", "n": 2}]
    )
    k0, k1 = (c["Key"] for c in _calls_of(client, "put_object"))
    assert k0 != k1


@pytest.mark.asyncio
async def test_append_jsonl_serialization() -> None:
    client = _RecordingClient()
    store = _make_store(client)
    await store.append(
        {"project_key": "proj", "session_id": "sess"},
        [{"type": "x", "a": 1}, {"type": "x", "b": 2}],
    )
    body = _calls_of(client, "put_object")[0]["Body"]
    assert body == b'{"type":"x","a":1}\n{"type":"x","b":2}\n'


@pytest.mark.asyncio
async def test_append_same_ms_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: with time.time() alone, two same-ms appends got identical ms
    # prefixes and sorted by the random hex suffix → nondeterministic load()
    # order. The per-instance monotonic counter makes ms strictly increasing.
    client = _RecordingClient()
    monkeypatch.setattr(
        "session_stores.s3_session_store.time.time", lambda: 1_700_000_000.0
    )
    store = _make_store(client)
    key: SessionKey = {"project_key": "proj", "session_id": "sess"}
    for i in range(3):
        await store.append(key, [{"type": "x", "i": i}])

    puts = [c["Key"] for c in _calls_of(client, "put_object")]
    assert puts[0] < puts[1] < puts[2]
    assert await store.load(key) == [
        {"type": "x", "i": 0},
        {"type": "x", "i": 1},
        {"type": "x", "i": 2},
    ]


@pytest.mark.asyncio
async def test_append_empty_is_noop() -> None:
    # Regression: append([]) used to PUT a "\n" body, creating a junk part
    # file per call.
    client = _RecordingClient()
    store = _make_store(client)
    await store.append({"project_key": "proj", "session_id": "sess"}, [])
    assert client.calls == []


@pytest.mark.asyncio
async def test_append_includes_subpath() -> None:
    client = _RecordingClient()
    store = _make_store(client)
    await store.append(
        {"project_key": "proj", "session_id": "sess", "subpath": "subagents/agent-1"},
        [{"type": "x", "x": 1}],
    )
    key = _calls_of(client, "put_object")[0]["Key"]
    dir_prefix = "p/proj/sess/subagents/agent-1/"
    assert key.startswith(dir_prefix)
    assert PART_NAME_RE.fullmatch(key[len(dir_prefix) :])


# ---------------------------------------------------------------------------
# load — sort, paginate, exclude subpaths, skip malformed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_null_when_empty(s3_client: Any) -> None:
    store = _make_store(s3_client)
    assert await store.load({"project_key": "proj", "session_id": "sess"}) is None


@pytest.mark.asyncio
async def test_load_sorts_and_concatenates() -> None:
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess/part-0000000000002-000000.jsonl": b'{"n":3}\n',
        "p/proj/sess/part-0000000000000-000000.jsonl": b'{"n":0}\n',
        "p/proj/sess/part-0000000000001-000000.jsonl": b'{"n":1}\n{"n":2}\n',
    }
    store = _make_store(client)
    result = await store.load({"project_key": "proj", "session_id": "sess"})
    assert result == [{"n": 0}, {"n": 1}, {"n": 2}, {"n": 3}]


@pytest.mark.asyncio
async def test_load_paginates(s3_client: Any) -> None:
    """Real moto pagination: 5 parts at MaxKeys=2 → 3 pages."""

    class PagedClient:
        """Wrap a real boto3 client; force MaxKeys=2 on list_objects_v2."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.list_calls: list[dict[str, Any]] = []

        def list_objects_v2(self, **kw: Any) -> Any:
            self.list_calls.append(kw)
            return self._inner.list_objects_v2(**{**kw, "MaxKeys": 2})

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    paged = PagedClient(s3_client)
    store = _make_store(paged)
    key: SessionKey = {"project_key": "proj", "session_id": "sess"}
    for i in range(5):
        await store.append(key, [{"type": "x", "n": i}])

    result = await store.load(key)
    assert result is not None
    assert [e["n"] for e in result] == [0, 1, 2, 3, 4]
    # 5 keys / page size 2 → 3 list calls; first has no token, rest do.
    assert len(paged.list_calls) == 3
    assert "ContinuationToken" not in paged.list_calls[0]
    assert all("ContinuationToken" in c for c in paged.list_calls[1:])


@pytest.mark.asyncio
async def test_load_excludes_subpath_parts() -> None:
    # Regression: ListObjectsV2 with a bare Prefix recurses into subpaths,
    # so load({project_key, session_id}) was mixing subagent entries into the
    # main transcript. Mock returns both even though Delimiter='/' is set, to
    # verify the client-side guard holds against S3-compatibles that ignore it.
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess/part-0000000000000-aaaaaa.jsonl": b'{"main":1}\n',
        "p/proj/sess/subagents/agent-1/part-0000000000001-bbbbbb.jsonl": b'{"sub":1}\n',
        "p/proj/sess/part-0000000000002-cccccc.jsonl": b'{"main":2}\n',
    }
    # Override list to ignore Delimiter so the recursive guard is exercised.
    real_list = client.list_objects_v2

    def list_ignoring_delimiter(**kw: Any) -> dict[str, Any]:
        kw.pop("Delimiter", None)
        return real_list(**kw)

    client.list_objects_v2 = list_ignoring_delimiter  # type: ignore[assignment]

    store = _make_store(client)
    result = await store.load({"project_key": "proj", "session_id": "sess"})
    assert result == [{"main": 1}, {"main": 2}]
    # Subagent part must not be fetched at all. get_object calls are
    # bounded-parallel via asyncio.gather so recording order is unspecified —
    # the slot-indexed bodies[] preserves entry order regardless.
    fetched = sorted(c["Key"] for c in _calls_of(client, "get_object"))
    assert fetched == [
        "p/proj/sess/part-0000000000000-aaaaaa.jsonl",
        "p/proj/sess/part-0000000000002-cccccc.jsonl",
    ]


@pytest.mark.asyncio
async def test_load_skips_malformed_lines() -> None:
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess/part-0000000000000-000000.jsonl": b'{"ok":1}\nnot json\n{"ok":2}\n',
    }
    store = _make_store(client)
    result = await store.load({"project_key": "proj", "session_id": "sess"})
    assert result == [{"ok": 1}, {"ok": 2}]


# ---------------------------------------------------------------------------
# list_sessions — mtime extraction, subagent filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_extracts_mtime() -> None:
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess-a/part-1700000000000-abcdef.jsonl": b"{}\n",
        "p/proj/sess-a/part-1700000005000-abcdef.jsonl": b"{}\n",
        "p/proj/sess-b/part-1700000003000-abcdef.jsonl": b"{}\n",
    }
    store = _make_store(client)
    result = await store.list_sessions("proj")
    by_id = {r["session_id"]: r["mtime"] for r in result}
    assert sorted(by_id) == ["sess-a", "sess-b"]
    # mtime is the max epochMs across that session's parts.
    assert by_id["sess-a"] == 1_700_000_005_000
    assert by_id["sess-b"] == 1_700_000_003_000


@pytest.mark.asyncio
async def test_list_sessions_ignores_subagent_parts() -> None:
    # Regression: without a depth filter, {session_id}/subagents/*/part-*
    # matched the part-regex, surfacing phantom session_ids and skewing mtime.
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess-a/part-1700000000000-aaaaaa.jsonl": b"{}\n",
        "p/proj/sess-a/subagents/agent-1/part-1700000009000-bbbbbb.jsonl": b"{}\n",
        "p/proj/sess-ghost/subagents/agent-1/part-1700000001000-cccccc.jsonl": b"{}\n",
    }
    store = _make_store(client)
    result = await store.list_sessions("proj")
    assert result == [{"session_id": "sess-a", "mtime": 1_700_000_000_000}]


# ---------------------------------------------------------------------------
# delete — cascade vs targeted, error surfacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_batch_deletes_parts() -> None:
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess/part-0000000000000-000000.jsonl": b"{}\n",
        "p/proj/sess/part-0000000000001-000000.jsonl": b"{}\n",
    }
    store = _make_store(client)
    await store.delete({"project_key": "proj", "session_id": "sess"})
    dels = _calls_of(client, "delete_objects")
    assert len(dels) == 1
    assert dels[0]["Bucket"] == BUCKET
    assert sorted(o["Key"] for o in dels[0]["Delete"]["Objects"]) == [
        "p/proj/sess/part-0000000000000-000000.jsonl",
        "p/proj/sess/part-0000000000001-000000.jsonl",
    ]


@pytest.mark.asyncio
async def test_delete_subpath_direct_only(s3_client: Any) -> None:
    # Regression: InMemorySessionStore.delete({...,subpath:'a'}) removes
    # exactly key 'a'; cascade is gated on subpath is None. S3's recursive
    # prefix list was nuking 'a/b' too.
    store = _make_store(s3_client)
    base: SessionKey = {"project_key": "proj", "session_id": "sess"}
    await store.append({**base, "subpath": "a"}, [{"type": "x", "a": 1}])
    await store.append({**base, "subpath": "a/b"}, [{"type": "x", "ab": 1}])

    await store.delete({**base, "subpath": "a"})

    assert await store.load({**base, "subpath": "a"}) is None
    assert await store.load({**base, "subpath": "a/b"}) == [{"type": "x", "ab": 1}]


@pytest.mark.asyncio
async def test_delete_cascades_without_subpath(s3_client: Any) -> None:
    store = _make_store(s3_client)
    base: SessionKey = {"project_key": "proj", "session_id": "sess"}
    await store.append(base, [{"type": "x", "m": 1}])
    await store.append({**base, "subpath": "a"}, [{"type": "x", "a": 1}])
    await store.append({**base, "subpath": "a/b"}, [{"type": "x", "ab": 1}])

    await store.delete(base)

    assert await store.load(base) is None
    assert await store.load({**base, "subpath": "a"}) is None
    assert await store.load({**base, "subpath": "a/b"}) is None


@pytest.mark.asyncio
async def test_delete_surfaces_errors() -> None:
    client = _RecordingClient()
    client.objects = {"p/proj/sess/part-0000000000000-000000.jsonl": b"{}\n"}

    def failing_delete(**kw: Any) -> dict[str, Any]:
        client.calls.append(("delete_objects", kw))
        return {"Errors": [{"Key": "x", "Code": "AccessDenied"}]}

    client.delete_objects = failing_delete  # type: ignore[assignment]
    store = _make_store(client)
    with pytest.raises(
        RuntimeError, match=r"S3 delete failed for 1 object\(s\): x: AccessDenied"
    ):
        await store.delete({"project_key": "proj", "session_id": "sess"})


# ---------------------------------------------------------------------------
# list_subkeys — extraction + traversal filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_subkeys_extracts_unique_subpaths() -> None:
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess/subagents/agent-1/part-0000000000000-000000.jsonl": b"{}\n",
        "p/proj/sess/subagents/agent-1/part-0000000000001-000000.jsonl": b"{}\n",
        "p/proj/sess/subagents/agent-2/part-0000000000000-000000.jsonl": b"{}\n",
    }
    store = _make_store(client)
    result = await store.list_subkeys({"project_key": "proj", "session_id": "sess"})
    assert sorted(result) == ["subagents/agent-1", "subagents/agent-2"]


@pytest.mark.asyncio
async def test_list_subkeys_filters_traversal_segments() -> None:
    # Real AWS S3 (unlike MinIO) accepts '..' literally in object keys, so a
    # compromised/buggy writer could produce these.
    client = _RecordingClient()
    client.objects = {
        "p/proj/sess/../x/part-0000000000001-aaaaaa.jsonl": b"{}\n",
        "p/proj/sess/a/../b/part-0000000000001-aaaaaa.jsonl": b"{}\n",
        "p/proj/sess/a/./b/part-0000000000001-aaaaaa.jsonl": b"{}\n",
        "p/proj/sess/a//b/part-0000000000001-aaaaaa.jsonl": b"{}\n",
        "p/proj/sess/ok/part-0000000000001-aaaaaa.jsonl": b"{}\n",
    }
    store = _make_store(client)
    result = await store.list_subkeys({"project_key": "proj", "session_id": "sess"})
    assert result == ["ok"]


# ---------------------------------------------------------------------------
# prefix normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_prefix", ["", "p", "p/", "p///"])
async def test_prefix_normalization(raw_prefix: str) -> None:
    client = _RecordingClient()
    store = _make_store(client, prefix=raw_prefix)
    key: SessionKey = {"project_key": "proj", "session_id": "sess"}

    await store.append(key, [{"type": "x", "n": 1}])
    put_key = _calls_of(client, "put_object")[0]["Key"]

    await store.list_sessions("proj")
    await store.list_subkeys(key)
    list_call, subkeys_call = _calls_of(client, "list_objects_v2")

    # The object key append() wrote must be under the prefix that
    # list_sessions()/list_subkeys() search — otherwise round-trip is broken.
    assert put_key.startswith(list_call["Prefix"])
    assert put_key.startswith(subkeys_call["Prefix"])
    # No double-slash or leading-slash artifacts.
    assert "//" not in put_key
    assert not put_key.startswith("/")


# ---------------------------------------------------------------------------
# materialize_resume_session round-trip (moto-backed)
# ---------------------------------------------------------------------------


SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


def _entry(role: str, n: int, parent: str | None, sid: str) -> dict[str, Any]:
    return {
        "type": role,
        "uuid": str(uuid_mod.uuid4()),
        "parentUuid": parent,
        "sessionId": sid,
        "timestamp": "2024-01-01T00:00:00.000Z",
        "message": {"role": role, "content": f"msg {n}"},
    }


@pytest.mark.asyncio
async def test_materialize_round_trip(
    s3_client: Any, tmp_path: Path, isolated_home: Path
) -> None:
    cwd = tmp_path / "project"
    cwd.mkdir()
    store = _make_store(s3_client)
    project_key = project_key_for_directory(cwd)

    seeded: list[dict[str, Any]] = []
    parent: str | None = None
    for i in range(2):
        u = _entry("user", i, parent, SESSION_ID)
        a = _entry("assistant", i, u["uuid"], SESSION_ID)
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
        [_entry("user", 0, None, SESSION_ID)],  # type: ignore[list-item]
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
# TranscriptMirrorBatcher 50-entry flush (moto-backed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batcher_50_entries(s3_client: Any, tmp_path: Path) -> None:
    store = _make_store(s3_client)
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
