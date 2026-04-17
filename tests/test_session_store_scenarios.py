"""Resume scenario matrix for SessionStore-backed sessions.

Systematically exercises ``materialize_resume_session`` and the
``ClaudeSDKClient.connect()`` wiring across the full cross-product of:

- resume mode (``resume`` / ``continue_conversation`` / both / neither)
- store population (empty, single, with subagents, many sessions, empty load)
- subkey shapes (none, flat, nested, with ``agent_metadata``)
- ``load_timeout_ms`` (default, fast store, one slow subkey)

plus a handful of real-world flows (fork → resume, rename → resume,
mirror-after-resume key resolution, full connect/disconnect round-trip).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, InMemorySessionStore
from claude_agent_sdk._internal.session_mutations import (
    fork_session_via_store,
    rename_session_via_store,
)
from claude_agent_sdk._internal.session_resume import (
    MaterializedResume,
    materialize_resume_session,
)
from claude_agent_sdk._internal.session_store import (
    file_path_to_session_key,
    project_key_for_directory,
)
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import SessionKey, SessionStoreEntry

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

UUIDS = [str(uuid.UUID(int=i)) for i in range(1, 8)]
SID, SID2, SID3, SID4, SID5 = UUIDS[:5]


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def project_key(cwd: Path) -> str:
    return project_key_for_directory(cwd)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~ and clear auth env so tests don't touch the real config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return home


def _entry(kind: str, n: int, **extra: Any) -> SessionStoreEntry:
    return {"type": kind, "uuid": f"{kind[0]}{n}", **extra}  # type: ignore[typeddict-item]


async def _seed_main(
    store: InMemorySessionStore, project_key: str, session_id: str, n: int = 2
) -> None:
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [_entry("user", i) if i % 2 == 0 else _entry("assistant", i) for i in range(n)],
    )


def _mock_transport() -> Any:
    t = AsyncMock()

    async def _no_messages():
        return
        yield  # pragma: no cover

    t.read_messages = _no_messages
    t.connect = AsyncMock()
    t.close = AsyncMock()
    t.end_input = AsyncMock()
    t.write = AsyncMock()
    t.is_ready = Mock(return_value=True)
    return t


# ---------------------------------------------------------------------------
# Dimension: mode × store-state
#
# For each (mode, store_state) pair assert whether materialize returns None
# (fresh session) or a MaterializedResume with the expected resume_session_id.
# ---------------------------------------------------------------------------


async def _build_store(
    state: str, project_key: str
) -> tuple[InMemorySessionStore, str | None]:
    """Return (store, expected_most_recent_session_id_or_None)."""
    store = InMemorySessionStore()
    if state == "empty":
        return store, None
    if state == "one_session":
        await _seed_main(store, project_key, SID)
        return store, SID
    if state == "one_session_three_subagents":
        await _seed_main(store, project_key, SID)
        for sub in ("subagents/agent-a", "subagents/agent-b", "subagents/agent-c"):
            await store.append(
                {"project_key": project_key, "session_id": SID, "subpath": sub},
                [_entry("user", 0), _entry("assistant", 1)],
            )
        return store, SID
    if state == "five_sessions_mixed_mtimes":
        for i, sid in enumerate(UUIDS[:5]):
            await _seed_main(store, project_key, sid)
            store._mtimes[f"{project_key}/{sid}"] = (i + 1) * 1000
        # Most-recent is index 4 → SID5.
        return store, SID5
    if state == "exists_but_empty_load":
        # Session listed but load() returns [] — resume should fall through.
        await store.append({"project_key": project_key, "session_id": SID}, [])
        return store, SID
    raise AssertionError(f"unknown state {state}")


MODE_STORE_CELLS: list[tuple[str, str, str | None]] = [
    # (mode, store_state, expected_resume_id-or-None)
    # neither → never materializes regardless of store
    ("neither", "empty", None),
    ("neither", "one_session", None),
    ("neither", "five_sessions_mixed_mtimes", None),
    # resume=SID → materializes iff SID has entries
    ("resume", "empty", None),
    ("resume", "one_session", SID),
    ("resume", "one_session_three_subagents", SID),
    ("resume", "five_sessions_mixed_mtimes", SID),
    ("resume", "exists_but_empty_load", None),
    # continue → picks most recent; empty store / empty-load → fresh
    ("continue", "empty", None),
    ("continue", "one_session", SID),
    ("continue", "one_session_three_subagents", SID),
    ("continue", "five_sessions_mixed_mtimes", SID5),
    ("continue", "exists_but_empty_load", None),
    # both → resume wins (no list_sessions consulted)
    ("both", "empty", None),
    ("both", "one_session", SID),
    ("both", "five_sessions_mixed_mtimes", SID),  # NOT SID5: explicit beats mtime
    ("both", "exists_but_empty_load", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "state", "expected_id"), MODE_STORE_CELLS)
async def test_mode_x_store_state(
    mode: str,
    state: str,
    expected_id: str | None,
    cwd: Path,
    project_key: str,
    isolated_home: Path,
) -> None:
    store, _most_recent = await _build_store(state, project_key)
    kwargs: dict[str, Any] = {"cwd": cwd, "session_store": store}
    if mode in ("resume", "both"):
        kwargs["resume"] = SID
    if mode in ("continue", "both"):
        kwargs["continue_conversation"] = True
    opts = ClaudeAgentOptions(**kwargs)

    m = await materialize_resume_session(opts)

    if expected_id is None:
        assert m is None
        return

    assert isinstance(m, MaterializedResume)
    assert m.resume_session_id == expected_id

    # Main JSONL on disk under config_dir, content matches store entries.
    jsonl = m.config_dir / "projects" / project_key / f"{expected_id}.jsonl"
    assert jsonl.is_file()
    on_disk = [json.loads(ln) for ln in jsonl.read_text().splitlines()]
    in_store = store.get_entries(
        {"project_key": project_key, "session_id": expected_id}
    )
    assert on_disk == in_store

    # Subagent dir exists iff state seeded subagents and we resumed that one.
    session_dir = m.config_dir / "projects" / project_key / expected_id
    if state == "one_session_three_subagents":
        for sub in ("agent-a", "agent-b", "agent-c"):
            assert (session_dir / "subagents" / f"{sub}.jsonl").is_file()
    else:
        assert not session_dir.exists()

    await m.cleanup()
    assert not m.config_dir.exists()


# ---------------------------------------------------------------------------
# Dimension: subkey shapes
# ---------------------------------------------------------------------------


SUBKEY_SHAPES: dict[str, list[tuple[str, list[SessionStoreEntry]]]] = {
    "none": [],
    "flat": [
        ("subagents/agent-x", [_entry("user", 0), _entry("assistant", 1)]),
    ],
    "nested": [
        (
            "subagents/workflows/w1/agent-x",
            [_entry("user", 0), _entry("assistant", 1)],
        ),
    ],
    "mixed_with_metadata": [
        ("subagents/agent-a", [_entry("user", 0)]),
        (
            "subagents/workflows/w1/agent-b",
            [
                _entry("user", 0),
                {"type": "agent_metadata", "agentType": "general", "ver": 2},
            ],
        ),
    ],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", list(SUBKEY_SHAPES))
async def test_subkey_shapes(
    shape: str, cwd: Path, project_key: str, isolated_home: Path
) -> None:
    store = InMemorySessionStore()
    await _seed_main(store, project_key, SID)
    for subpath, entries in SUBKEY_SHAPES[shape]:
        await store.append(
            {"project_key": project_key, "session_id": SID, "subpath": subpath},
            entries,
        )

    opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SID)
    m = await materialize_resume_session(opts)
    assert m is not None
    session_dir = m.config_dir / "projects" / project_key / SID

    for subpath, entries in SUBKEY_SHAPES[shape]:
        jsonl = session_dir / f"{subpath}.jsonl"
        meta = session_dir / f"{subpath}.meta.json"
        transcript = [e for e in entries if e.get("type") != "agent_metadata"]
        metadata = [e for e in entries if e.get("type") == "agent_metadata"]
        if transcript:
            assert jsonl.is_file()
            assert [
                json.loads(ln) for ln in jsonl.read_text().splitlines()
            ] == transcript
        if metadata:
            assert meta.is_file()
            assert json.loads(meta.read_text()) == {
                k: v for k, v in metadata[-1].items() if k != "type"
            }
        else:
            assert not meta.exists()

    if shape == "none":
        assert not session_dir.exists()

    await m.cleanup()
    assert not m.config_dir.exists()


# ---------------------------------------------------------------------------
# Dimension: load_timeout_ms
# ---------------------------------------------------------------------------


class _OneSlowSubkeyStore(InMemorySessionStore):
    """Hangs on load() for exactly one subpath; everything else is instant."""

    def __init__(self, slow_subpath: str) -> None:
        super().__init__()
        self._slow = slow_subpath

    async def load(self, key):  # type: ignore[override]
        if key.get("subpath") == self._slow:
            await asyncio.sleep(3600)
        return await super().load(key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeout_ms", "slow_subkey", "expect_error"),
    [
        (60_000, None, False),  # default, fast store
        (100, None, False),  # tight budget, fast store → still ok
        (50, "subagents/agent-slow", True),  # tight budget, one slow subkey
    ],
    ids=["default", "fast_100ms", "slow_subkey_50ms"],
)
async def test_load_timeout_ms(
    timeout_ms: int,
    slow_subkey: str | None,
    expect_error: bool,
    cwd: Path,
    project_key: str,
    isolated_home: Path,
) -> None:
    store: InMemorySessionStore
    store = _OneSlowSubkeyStore(slow_subkey) if slow_subkey else InMemorySessionStore()
    await _seed_main(store, project_key, SID)
    if slow_subkey:
        await store.append(
            {"project_key": project_key, "session_id": SID, "subpath": slow_subkey},
            [_entry("user", 0)],
        )
    # A second, fast subkey to confirm the timeout is per-call.
    await store.append(
        {
            "project_key": project_key,
            "session_id": SID,
            "subpath": "subagents/agent-ok",
        },
        [_entry("user", 0)],
    )

    opts = ClaudeAgentOptions(
        cwd=cwd, session_store=store, resume=SID, load_timeout_ms=timeout_ms
    )

    if expect_error:
        with pytest.raises(RuntimeError, match="timed out"):
            await materialize_resume_session(opts)
        return

    m = await materialize_resume_session(opts)
    assert m is not None
    assert (
        m.config_dir / "projects" / project_key / SID / "subagents" / "agent-ok.jsonl"
    ).is_file()
    await m.cleanup()
    assert not m.config_dir.exists()


# ---------------------------------------------------------------------------
# Transport wiring: --resume / --continue suppression per mode cell
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "state", "want_resume_flag", "want_continue_flag"),
    [
        # When materialized: --resume <id>, --continue suppressed.
        ("resume", "one_session", SID, False),
        ("continue", "one_session", SID, False),
        ("both", "five_sessions_mixed_mtimes", SID, False),
        ("continue", "five_sessions_mixed_mtimes", SID5, False),
        # Not materialized: original flags pass through unchanged.
        ("neither", "one_session", None, False),
        ("resume", "empty", SID, False),  # store empty → not materialized
        ("continue", "empty", None, True),  # falls through to raw --continue
    ],
)
async def test_transport_flag_suppression(
    mode: str,
    state: str,
    want_resume_flag: str | None,
    want_continue_flag: bool,
    cwd: Path,
    project_key: str,
    isolated_home: Path,
) -> None:
    store, _ = await _build_store(state, project_key)
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "session_store": store,
        "cli_path": "/usr/bin/claude",
    }
    if mode in ("resume", "both"):
        kwargs["resume"] = SID
    if mode in ("continue", "both"):
        kwargs["continue_conversation"] = True
    opts = ClaudeAgentOptions(**kwargs)

    captured: dict[str, Any] = {}

    def _capture(*, prompt, options):
        captured["options"] = options
        return _mock_transport()

    client = ClaudeSDKClient(options=opts)
    with (
        patch(
            "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport",
            side_effect=_capture,
        ),
        patch(
            "claude_agent_sdk._internal.query.Query.initialize", new_callable=AsyncMock
        ),
    ):
        await client.connect()

    transport_opts: ClaudeAgentOptions = captured["options"]
    cmd = SubprocessCLITransport(prompt="x", options=transport_opts)._build_command()

    if want_resume_flag is not None:
        assert "--resume" in cmd
        assert cmd[cmd.index("--resume") + 1] == want_resume_flag
    else:
        assert "--resume" not in cmd
    assert ("--continue" in cmd) is want_continue_flag
    assert "--session-mirror" in cmd  # store always present in this matrix

    await client.disconnect()
    # Any materialized config_dir is gone after disconnect.
    cfg = transport_opts.env.get("CLAUDE_CONFIG_DIR")
    if cfg:
        assert not Path(cfg).exists()


# ---------------------------------------------------------------------------
# Real-world: resume a fork
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_a_forked_session(
    cwd: Path, project_key: str, isolated_home: Path
) -> None:
    store = InMemorySessionStore()
    main_key: SessionKey = {"project_key": project_key, "session_id": SID}
    # Full transcript entries with parentUuid chain so fork transform applies.
    u1, a1 = str(uuid.uuid4()), str(uuid.uuid4())
    await store.append(
        main_key,
        [
            {
                "type": "user",
                "uuid": u1,
                "parentUuid": None,
                "sessionId": SID,
                "timestamp": "2024-01-01T00:00:00Z",
                "message": {"role": "user", "content": "hi"},
            },
            {
                "type": "assistant",
                "uuid": a1,
                "parentUuid": u1,
                "sessionId": SID,
                "timestamp": "2024-01-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello"}],
                },
            },
        ],
    )

    fork = await fork_session_via_store(store, SID, directory=str(cwd))
    fork_key: SessionKey = {"project_key": project_key, "session_id": fork.session_id}
    forked_entries = store.get_entries(fork_key)
    # Every transcript entry carries forkedFrom; trailing custom-title does not.
    assert all(
        e.get("forkedFrom", {}).get("sessionId") == SID
        for e in forked_entries
        if e["type"] in ("user", "assistant")
    )

    # Resume the fork.
    opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=fork.session_id)
    m = await materialize_resume_session(opts)
    assert m is not None
    assert m.resume_session_id == fork.session_id
    jsonl = m.config_dir / "projects" / project_key / f"{fork.session_id}.jsonl"
    on_disk = [json.loads(ln) for ln in jsonl.read_text().splitlines()]
    assert on_disk == forked_entries
    # forkedFrom survived the JSONL round-trip.
    assert on_disk[0]["forkedFrom"]["messageUuid"] == u1
    await m.cleanup()


# ---------------------------------------------------------------------------
# Real-world: rename → resume carries the custom-title entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_rename_carries_custom_title(
    cwd: Path, project_key: str, isolated_home: Path
) -> None:
    store = InMemorySessionStore()
    await _seed_main(store, project_key, SID)
    await rename_session_via_store(store, SID, "  My Title  ", directory=str(cwd))

    opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SID)
    m = await materialize_resume_session(opts)
    assert m is not None
    lines = [
        json.loads(ln)
        for ln in (m.config_dir / "projects" / project_key / f"{SID}.jsonl")
        .read_text()
        .splitlines()
    ]
    titles = [e for e in lines if e.get("type") == "custom-title"]
    assert len(titles) == 1
    assert titles[0]["customTitle"] == "My Title"
    assert titles[0]["sessionId"] == SID
    await m.cleanup()


# ---------------------------------------------------------------------------
# Real-world: resume → mirror new entries → batcher path resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_then_mirror_resolves_to_original_key(
    cwd: Path, project_key: str, isolated_home: Path
) -> None:
    """After a materialized resume, transcript_mirror frames carry filePaths
    under the temp ``config_dir`` — the batcher's ``projects_dir`` must point
    there so :func:`file_path_to_session_key` round-trips to the original
    ``{project_key, session_id}``."""
    store = InMemorySessionStore()
    await _seed_main(store, project_key, SID, n=2)

    captured: dict[str, Any] = {}

    def _capture(*, prompt, options):
        captured["options"] = options
        return _mock_transport()

    opts = ClaudeAgentOptions(
        cwd=cwd, session_store=store, resume=SID, cli_path="/usr/bin/claude"
    )
    client = ClaudeSDKClient(options=opts)
    with (
        patch(
            "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport",
            side_effect=_capture,
        ),
        patch(
            "claude_agent_sdk._internal.query.Query.initialize", new_callable=AsyncMock
        ),
    ):
        await client.connect()

    assert client._query is not None
    batcher = client._query._transcript_mirror_batcher
    assert batcher is not None
    config_dir = captured["options"].env["CLAUDE_CONFIG_DIR"]
    assert batcher.projects_dir == str(Path(config_dir) / "projects")

    # Main-transcript frame as the subprocess would emit it.
    main_path = str(Path(config_dir) / "projects" / project_key / f"{SID}.jsonl")
    assert file_path_to_session_key(main_path, batcher.projects_dir) == {
        "project_key": project_key,
        "session_id": SID,
    }
    # Nested subagent frame.
    sub_path = str(
        Path(config_dir)
        / "projects"
        / project_key
        / SID
        / "subagents"
        / "workflows"
        / "w1"
        / "agent-x.jsonl"
    )
    assert file_path_to_session_key(sub_path, batcher.projects_dir) == {
        "project_key": project_key,
        "session_id": SID,
        "subpath": "subagents/workflows/w1/agent-x",
    }

    # Feed a frame through the batcher and confirm it lands in the store under
    # the ORIGINAL key (not a temp-dir-derived key).
    batcher.enqueue(main_path, [_entry("assistant", 99)])
    await batcher.flush()
    entries = store.get_entries({"project_key": project_key, "session_id": SID})
    assert entries[-1]["uuid"] == "a99"

    await client.disconnect()
    assert not Path(config_dir).exists()


# ---------------------------------------------------------------------------
# Real-world: two connect/disconnect cycles → second sees first's mirrored data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_cycle_round_trip(
    cwd: Path, project_key: str, isolated_home: Path
) -> None:
    store = InMemorySessionStore()
    await _seed_main(store, project_key, SID, n=1)

    config_dirs: list[str] = []

    async def _cycle(new_entry: SessionStoreEntry) -> list[dict[str, Any]]:
        captured: dict[str, Any] = {}

        def _capture(*, prompt, options):
            captured["options"] = options
            return _mock_transport()

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=store, resume=SID, cli_path="/usr/bin/claude"
        )
        client = ClaudeSDKClient(options=opts)
        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli."
                "SubprocessCLITransport",
                side_effect=_capture,
            ),
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
            ),
        ):
            await client.connect()

        config_dir = captured["options"].env["CLAUDE_CONFIG_DIR"]
        config_dirs.append(config_dir)
        # Snapshot what materialize wrote (i.e. what the subprocess would resume).
        on_disk = [
            json.loads(ln)
            for ln in (Path(config_dir) / "projects" / project_key / f"{SID}.jsonl")
            .read_text()
            .splitlines()
        ]
        # Mirror a new entry as if the subprocess emitted it.
        batcher = client._query._transcript_mirror_batcher
        main_path = str(Path(config_dir) / "projects" / project_key / f"{SID}.jsonl")
        batcher.enqueue(main_path, [new_entry])
        await batcher.flush()
        await client.disconnect()
        return on_disk

    first_seen = await _cycle(_entry("assistant", 1, marker="cycle1"))
    assert len(first_seen) == 1  # only the seed

    second_seen = await _cycle(_entry("assistant", 2, marker="cycle2"))
    # Second cycle's resume materialized the seed + cycle1's mirrored entry.
    assert len(second_seen) == 2
    assert second_seen[-1].get("marker") == "cycle1"

    # Store now holds seed + cycle1 + cycle2.
    final = store.get_entries({"project_key": project_key, "session_id": SID})
    assert [e.get("marker") for e in final] == [None, "cycle1", "cycle2"]

    # Both temp config_dirs cleaned up; they were distinct.
    assert len(set(config_dirs)) == 2
    for d in config_dirs:
        assert not Path(d).exists()
