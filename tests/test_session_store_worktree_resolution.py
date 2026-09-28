"""Known-ID store lookups must find sessions imported from git worktrees.

These tests use local git repositories and synthetic transcripts. No Claude CLI,
model, external store, or network service is involved. Session enumeration remains
scoped to the requested project's key; it is not a known-ID lookup.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    InMemorySessionStore,
    delete_session_via_store,
    fork_session_via_store,
    get_session_info_from_store,
    get_session_messages_from_store,
    get_subagent_messages_from_store,
    import_session_to_store,
    list_sessions,
    list_sessions_from_store,
    list_subagents_from_store,
    project_key_for_directory,
    rename_session_via_store,
    tag_session_via_store,
)
from claude_agent_sdk._internal.session_resume import (
    build_mirror_batcher,
    materialize_resume_session,
)
from claude_agent_sdk.types import SessionKey

pytestmark = pytest.mark.anyio

SID = "11111111-1111-4111-8111-111111111111"
USER_ID = "22222222-2222-4222-8222-222222222222"
ASSISTANT_ID = "33333333-3333-4333-8333-333333333333"
MISSING_ID = "44444444-4444-4444-8444-444444444444"


def _entries(directory: Path, prompt: str = "Worktree history") -> list[dict[str, Any]]:
    return [
        {
            "type": "user",
            "uuid": USER_ID,
            "parentUuid": None,
            "sessionId": SID,
            "timestamp": "2024-01-01T00:00:00Z",
            "cwd": str(directory),
            "message": {"role": "user", "content": prompt},
        },
        {
            "type": "assistant",
            "uuid": ASSISTANT_ID,
            "parentUuid": USER_ID,
            "sessionId": SID,
            "timestamp": "2024-01-01T00:00:01Z",
            "cwd": str(directory),
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Saved answer"}],
            },
        },
    ]


def _write_jsonl(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


@dataclass
class WorktreeSession:
    repo: Path
    worktree: Path
    config: Path
    entries: list[dict[str, Any]]

    def key(self, directory: Path, session_id: str = SID) -> SessionKey:
        return {
            "project_key": project_key_for_directory(directory),
            "session_id": session_id,
        }

    async def imported(self) -> InMemorySessionStore:
        assert [s.session_id for s in list_sessions(directory=str(self.repo))] == [SID]
        store = InMemorySessionStore()
        await import_session_to_store(SID, store, directory=str(self.repo))
        assert await store.load(self.key(self.worktree)) == self.entries
        assert await store.load(self.key(self.repo)) is None
        assert await store.list_subkeys(self.key(self.worktree)) == [
            "subagents/agent-local"
        ]
        return store

    def options(
        self, store: InMemorySessionStore, directory: Path, session_id: str = SID
    ) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            session_store=store,
            resume=session_id,
            cwd=str(directory),
            env={"CLAUDE_CONFIG_DIR": str(self.config)},
        )


@pytest.fixture
def worktree_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> WorktreeSession:
    if shutil.which("git") is None:
        pytest.skip("git is required for worktree resolution tests")
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    config = tmp_path / "claude-config"
    home = tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        "claude_agent_sdk._internal.session_resume._read_keychain_credentials",
        lambda: None,
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Local test",
            "-c",
            "user.email=local@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "test fixture",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(worktree)],
        check=True,
    )
    entries = _entries(worktree)
    project = config / "projects" / project_key_for_directory(worktree)
    _write_jsonl(project / f"{SID}.jsonl", entries)
    _write_jsonl(project / SID / "subagents" / "agent-local.jsonl", entries)
    return WorktreeSession(repo, worktree, config, entries)


@pytest.mark.parametrize("lookup", ["repo", "worktree"])
async def test_known_id_readers_find_imported_session(
    worktree_session: WorktreeSession, lookup: str
) -> None:
    f = worktree_session
    store = await f.imported()
    directory = str(getattr(f, lookup))
    info = await get_session_info_from_store(store, SID, directory=directory)
    assert info is not None
    assert info.session_id == SID
    assert info.first_prompt == "Worktree history"
    messages = await get_session_messages_from_store(store, SID, directory=directory)
    assert [m.uuid for m in messages] == [USER_ID, ASSISTANT_ID]
    assert await list_subagents_from_store(store, SID, directory=directory) == ["local"]
    submessages = await get_subagent_messages_from_store(
        store, SID, "local", directory=directory
    )
    assert [m.uuid for m in submessages] == [USER_ID, ASSISTANT_ID]


@pytest.mark.parametrize("lookup", ["repo", "worktree"])
@pytest.mark.parametrize("mutation", ["rename", "tag", "delete"])
async def test_mutations_target_imported_key(
    worktree_session: WorktreeSession, lookup: str, mutation: str
) -> None:
    f = worktree_session
    store = await f.imported()
    directory = str(getattr(f, lookup))
    if mutation == "rename":
        await rename_session_via_store(store, SID, "Renamed", directory=directory)
    elif mutation == "tag":
        await tag_session_via_store(store, SID, "reviewed", directory=directory)
    else:
        await delete_session_via_store(store, SID, directory=directory)
    entries = await store.load(f.key(f.worktree))
    assert await store.load(f.key(f.repo)) is None
    if mutation == "delete":
        assert entries is None
        assert await store.list_subkeys(f.key(f.worktree)) == []
        assert store.size == 0
    else:
        assert store.size == 1
        assert entries is not None
        assert entries[:2] == f.entries
        assert entries[-1]["type"] == (
            "custom-title" if mutation == "rename" else "tag"
        )


@pytest.mark.parametrize("lookup", ["repo", "worktree"])
async def test_resume_materializes_full_history_and_subagents_after_rename(
    worktree_session: WorktreeSession, lookup: str
) -> None:
    f = worktree_session
    store = await f.imported()
    directory = getattr(f, lookup)
    for renamed in (False, True):
        if renamed:
            await rename_session_via_store(
                store, SID, "Renamed", directory=str(directory)
            )
        materialized = await materialize_resume_session(f.options(store, directory))
        assert materialized is not None
        try:
            project = (
                materialized.config_dir
                / "projects"
                / project_key_for_directory(directory)
            )
            rows = [
                json.loads(line)
                for line in (project / f"{SID}.jsonl").read_text().splitlines()
            ]
            assert rows[:2] == f.entries
            assert [e["type"] for e in rows] == (
                ["user", "assistant", "custom-title"]
                if renamed
                else ["user", "assistant"]
            )
            subagent = project / SID / "subagents" / "agent-local.jsonl"
            assert [
                json.loads(line) for line in subagent.read_text().splitlines()
            ] == f.entries
        finally:
            await materialized.cleanup()


@pytest.mark.parametrize("lookup", ["repo", "worktree"])
async def test_fork_is_readable_and_resumable_without_disk_transcript(
    worktree_session: WorktreeSession, lookup: str
) -> None:
    f = worktree_session
    store = await f.imported()
    before = deepcopy(await store.load(f.key(f.worktree)))
    directory = getattr(f, lookup)
    result = await fork_session_via_store(store, SID, directory=str(directory))
    assert result.session_id != SID
    assert await store.load(f.key(f.worktree)) == before
    assert store.size == 2
    assert not list(f.config.rglob(f"{result.session_id}.jsonl"))
    # The returned ID must work through the same public directory argument;
    # resolving it must not depend on an on-disk file created for the fork.
    messages = await get_session_messages_from_store(
        store, result.session_id, directory=str(directory)
    )
    assert [m.type for m in messages] == ["user", "assistant"]
    assert all(m.uuid not in {USER_ID, ASSISTANT_ID} for m in messages)
    materialized = await materialize_resume_session(
        f.options(store, directory, result.session_id)
    )
    assert materialized is not None
    try:
        project = (
            materialized.config_dir / "projects" / project_key_for_directory(directory)
        )
        rows = [
            json.loads(line)
            for line in (project / f"{result.session_id}.jsonl")
            .read_text()
            .splitlines()
        ]
        transcript = [e for e in rows if e["type"] in ("user", "assistant")]
        assert [e["message"] for e in transcript] == [e["message"] for e in f.entries]
        assert all(e["sessionId"] == result.session_id for e in transcript)
        assert all(e["forkedFrom"]["sessionId"] == SID for e in transcript)
        assert transcript[0]["parentUuid"] is None
        assert transcript[1]["parentUuid"] == transcript[0]["uuid"]
    finally:
        await materialized.cleanup()


async def test_direct_key_takes_priority_over_same_id_in_worktree(
    worktree_session: WorktreeSession,
) -> None:
    f = worktree_session
    store = await f.imported()
    direct = _entries(f.repo, "Direct project history")
    await store.append(f.key(f.repo), direct)
    info = await get_session_info_from_store(store, SID, directory=str(f.repo))
    assert info is not None and info.first_prompt == "Direct project history"
    await rename_session_via_store(store, SID, "Direct title", directory=str(f.repo))
    assert await store.load(f.key(f.worktree)) == f.entries
    assert (await store.load(f.key(f.repo)))[-1]["customTitle"] == "Direct title"


async def test_store_only_direct_key_does_not_call_git(
    worktree_session: WorktreeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = worktree_session
    store = InMemorySessionStore()
    await store.append(f.key(f.repo), f.entries)

    async def unexpected_git(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("direct store hits must not require git")

    def unexpected_sync_git(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("direct store hits must not require git")

    monkeypatch.setattr(anyio, "run_process", unexpected_git)
    monkeypatch.setattr(subprocess, "run", unexpected_sync_git)
    directory = str(f.repo)
    assert (
        await get_session_info_from_store(store, SID, directory=directory) is not None
    )
    assert (
        len(await get_session_messages_from_store(store, SID, directory=directory)) == 2
    )
    assert await list_subagents_from_store(store, SID, directory=directory) == []
    assert (
        await get_subagent_messages_from_store(store, SID, "local", directory=directory)
        == []
    )
    await rename_session_via_store(store, SID, "Direct", directory=directory)
    await tag_session_via_store(store, SID, "tag", directory=directory)
    fork = await fork_session_via_store(store, SID, directory=directory)
    materialized = await materialize_resume_session(
        f.options(store, f.repo, fork.session_id)
    )
    assert materialized is not None
    await materialized.cleanup()
    await delete_session_via_store(store, SID, directory=directory)
    assert await store.load(f.key(f.repo)) is None


async def test_unknown_key_preserves_existing_helper_semantics(
    worktree_session: WorktreeSession,
) -> None:
    f = worktree_session
    store = InMemorySessionStore()
    directory = str(f.repo)
    assert (
        await get_session_info_from_store(store, MISSING_ID, directory=directory)
        is None
    )
    assert (
        await get_session_messages_from_store(store, MISSING_ID, directory=directory)
        == []
    )
    assert await list_subagents_from_store(store, MISSING_ID, directory=directory) == []
    assert (
        await get_subagent_messages_from_store(
            store, MISSING_ID, "local", directory=directory
        )
        == []
    )
    assert (
        await materialize_resume_session(f.options(store, f.repo, MISSING_ID)) is None
    )
    await delete_session_via_store(store, MISSING_ID, directory=directory)
    assert store.size == 0
    with pytest.raises(FileNotFoundError):
        await fork_session_via_store(store, MISSING_ID, directory=directory)
    # append() is allowed to create a stream: resolving a missing key must not
    # impose a new adapter-contract restriction on rename/tag.
    await rename_session_via_store(store, MISSING_ID, "New", directory=directory)
    await tag_session_via_store(store, MISSING_ID, "tag", directory=directory)
    rows = await store.load(f.key(f.repo, MISSING_ID))
    assert rows is not None and [e["type"] for e in rows] == ["custom-title", "tag"]
    assert store.size == 1


async def test_unrelated_project_with_same_id_is_not_searched(
    worktree_session: WorktreeSession,
) -> None:
    f = worktree_session
    store = InMemorySessionStore()
    unrelated = f.repo.parent / "unrelated"
    unrelated_key = f.key(unrelated)
    await store.append(unrelated_key, f.entries)
    assert (
        await get_session_messages_from_store(store, SID, directory=str(f.repo)) == []
    )
    assert await materialize_resume_session(f.options(store, f.repo)) is None
    await delete_session_via_store(store, SID, directory=str(f.repo))
    assert await store.load(unrelated_key) == f.entries
    await rename_session_via_store(store, SID, "New", directory=str(f.repo))
    assert await store.load(unrelated_key) == f.entries
    assert store.size == 2


async def test_listing_remains_scoped_to_single_project_key(
    worktree_session: WorktreeSession,
) -> None:
    """Known-ID fallback does not introduce cross-worktree enumeration."""
    f = worktree_session
    store = await f.imported()
    assert await list_sessions_from_store(store, directory=str(f.repo)) == []
    assert [
        s.session_id
        for s in await list_sessions_from_store(store, directory=str(f.worktree))
    ] == [SID]


async def test_empty_direct_stream_takes_priority_over_worktree(
    worktree_session: WorktreeSession,
) -> None:
    f = worktree_session
    store = await f.imported()
    await store.append(f.key(f.repo), [])
    assert await store.load(f.key(f.repo)) == []
    assert (
        await get_session_messages_from_store(store, SID, directory=str(f.repo)) == []
    )
    assert await materialize_resume_session(f.options(store, f.repo)) is None
    await rename_session_via_store(store, SID, "Empty direct", directory=str(f.repo))
    assert await store.load(f.key(f.worktree)) == f.entries
    assert (await store.load(f.key(f.repo)))[-1]["customTitle"] == "Empty direct"


async def test_direct_subagent_without_main_stream_remains_readable(
    worktree_session: WorktreeSession,
) -> None:
    f = worktree_session
    store = await f.imported()
    direct_entries = _entries(f.repo, "Direct subagent")
    await store.append(
        {**f.key(f.repo), "subpath": "subagents/agent-local"}, direct_entries
    )
    assert await store.load(f.key(f.repo)) is None
    assert await list_subagents_from_store(store, SID, directory=str(f.repo)) == [
        "local"
    ]
    messages = await get_subagent_messages_from_store(
        store, SID, "local", directory=str(f.repo)
    )
    assert [m.message for m in messages] == [e["message"] for e in direct_entries]


@pytest.mark.parametrize("operation", ["read", "rename", "fork"])
async def test_ambiguous_worktree_matches_do_not_choose_arbitrarily(
    worktree_session: WorktreeSession, operation: str
) -> None:
    f = worktree_session
    store = await f.imported()
    other = f.repo.parent / "another-worktree"
    subprocess.run(
        ["git", "-C", str(f.repo), "worktree", "add", "-q", "--detach", str(other)],
        check=True,
    )
    await store.append(f.key(other), _entries(other, "Other worktree"))
    with pytest.raises(ValueError):
        if operation == "read":
            await get_session_messages_from_store(store, SID, directory=str(f.repo))
        elif operation == "rename":
            await rename_session_via_store(
                store, SID, "Ambiguous", directory=str(f.repo)
            )
        else:
            await fork_session_via_store(store, SID, directory=str(f.repo))
    assert await store.load(f.key(f.repo)) is None
    assert await store.load(f.key(f.worktree)) == f.entries
    assert await store.load(f.key(other)) == _entries(other, "Other worktree")
    assert store.size == 2


async def test_resumed_mirror_appends_to_original_worktree_key(
    worktree_session: WorktreeSession,
) -> None:
    f = worktree_session
    store = await f.imported()
    materialized = await materialize_resume_session(f.options(store, f.repo))
    assert materialized is not None
    errors: list[str] = []

    async def on_error(_key: SessionKey | None, message: str) -> None:
        errors.append(message)

    batcher = build_mirror_batcher(store, materialized, None, on_error)
    new_main = {"type": "user", "uuid": "new-main", "sessionId": SID}
    new_sub = {"type": "assistant", "uuid": "new-sub", "sessionId": SID}
    new_session = {"type": "user", "uuid": "new-session", "sessionId": MISSING_ID}
    nested = {"type": "user", "uuid": "nested-subagent", "sessionId": SID}
    other_project_entry = {"type": "user", "uuid": "other-project", "sessionId": SID}
    other_project_key = project_key_for_directory(f.repo.parent / "unrelated")
    try:
        project = (
            materialized.config_dir / "projects" / project_key_for_directory(f.repo)
        )
        batcher.enqueue(str(project / f"{SID}.jsonl"), [new_main])
        batcher.enqueue(
            str(project / SID / "subagents" / "agent-local.jsonl"), [new_sub]
        )
        # Mapping must apply only to the resumed ID, not all files in that project.
        batcher.enqueue(str(project / f"{MISSING_ID}.jsonl"), [new_session])
        batcher.enqueue(
            str(
                project
                / SID
                / "subagents"
                / "workflows"
                / "run-1"
                / "agent-nested.jsonl"
            ),
            [nested],
        )
        # The same session ID in another project is not the resumed stream.
        batcher.enqueue(
            str(
                materialized.config_dir
                / "projects"
                / other_project_key
                / f"{SID}.jsonl"
            ),
            [other_project_entry],
        )
        await batcher.flush()
        assert errors == []
        assert await store.load(f.key(f.worktree)) == [*f.entries, new_main]
        assert await store.load(
            {**f.key(f.worktree), "subpath": "subagents/agent-local"}
        ) == [*f.entries, new_sub]
        assert await store.load(f.key(f.repo)) is None
        assert await store.list_subkeys(f.key(f.repo)) == []
        assert await store.load(f.key(f.repo, MISSING_ID)) == [new_session]
        assert await store.load(f.key(f.worktree, MISSING_ID)) is None
        assert await store.load(
            {**f.key(f.worktree), "subpath": "subagents/workflows/run-1/agent-nested"}
        ) == [nested]
        assert await store.load(
            {"project_key": other_project_key, "session_id": SID}
        ) == [other_project_entry]
    finally:
        await batcher.close()
        await materialized.cleanup()


@pytest.mark.parametrize("operation", ["list", "continue"])
async def test_stale_enumeration_does_not_load_worktree_transcript(
    worktree_session: WorktreeSession, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A stale direct-project listing must not turn into cross-project discovery."""
    f = worktree_session
    loads: list[SessionKey] = []

    class StaleListingStore(InMemorySessionStore):
        async def list_session_summaries(self, project_key):
            raise NotImplementedError

        async def list_sessions(self, project_key):
            return [{"session_id": SID, "mtime": 1}]

        async def load(self, key):
            loads.append(dict(key))
            return await super().load(key)

    store = StaleListingStore()
    await store.append(f.key(f.worktree), f.entries)

    async def unexpected_discovery(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("enumeration must stay in the requested project")

    monkeypatch.setattr(anyio, "run_process", unexpected_discovery)
    if operation == "list":
        assert await list_sessions_from_store(store, directory=str(f.repo)) == []
    else:
        options = f.options(store, f.repo)
        options.resume = None
        options.continue_conversation = True
        assert await materialize_resume_session(options) is None
    assert loads == [f.key(f.repo)]


@pytest.mark.parametrize("operation", ["read", "rename", "resume"])
async def test_fallback_adapter_error_is_not_a_missing_session(
    worktree_session: WorktreeSession, operation: str
) -> None:
    f = worktree_session

    class FailingFallbackStore(InMemorySessionStore):
        async def load(self, key):
            if key["project_key"] == project_key_for_directory(f.worktree):
                raise RuntimeError("fallback backend unavailable")
            return await super().load(key)

    store = FailingFallbackStore()
    with pytest.raises(RuntimeError, match="fallback backend unavailable"):
        if operation == "read":
            await get_session_messages_from_store(store, SID, directory=str(f.repo))
        elif operation == "rename":
            await rename_session_via_store(
                store, SID, "Must not append", directory=str(f.repo)
            )
        else:
            await materialize_resume_session(f.options(store, f.repo))
    assert store.size == 0


async def test_missing_git_preserves_unknown_session_behavior(
    worktree_session: WorktreeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = worktree_session
    store = InMemorySessionStore()

    async def missing_git(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("git is not installed")

    monkeypatch.setattr(anyio, "run_process", missing_git)
    assert (
        await get_session_messages_from_store(store, SID, directory=str(f.repo)) == []
    )
    await rename_session_via_store(store, SID, "New", directory=str(f.repo))
    assert (await store.load(f.key(f.repo)))[-1]["customTitle"] == "New"
    assert store.size == 1


async def test_resume_timeout_cancels_worktree_discovery(
    worktree_session: WorktreeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = worktree_session
    started = False
    finished = False

    async def stalled_git(*args: Any, **kwargs: Any) -> None:
        nonlocal started, finished
        started = True
        try:
            await anyio.sleep_forever()
        finally:
            finished = True

    monkeypatch.setattr(anyio, "run_process", stalled_git)
    options = f.options(InMemorySessionStore(), f.repo)
    options.load_timeout_ms = 10
    # The outer bound distinguishes SDK timeout handling from a hung discovery.
    with anyio.fail_after(1):
        with pytest.raises(
            RuntimeError, match="timed out.*during resume materialization"
        ):
            await materialize_resume_session(options)
    assert started and finished
