"""Tests for ``import_session_to_store`` (local JSONL → SessionStore replay)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from claude_agent_sdk import (
    InMemorySessionStore,
    import_session_to_store,
    project_key_for_directory,
)
from claude_agent_sdk.types import SessionKey, SessionStoreEntry

SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def project_key(cwd: Path) -> str:
    return project_key_for_directory(cwd)


@pytest.fixture
def claude_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_key: str
) -> Path:
    """Create an isolated ~/.claude/projects/<project_key>/ tree."""
    config = tmp_path / "claude_config"
    project_dir = config / "projects" / project_key
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return project_dir


def _entry(i: int) -> SessionStoreEntry:
    return {"type": "user", "uuid": f"u{i}", "timestamp": f"2026-01-01T00:00:{i:02d}Z"}


def _write_jsonl(path: Path, entries: list[SessionStoreEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main transcript import
# ---------------------------------------------------------------------------


class TestMainTranscript:
    @pytest.mark.asyncio
    async def test_imports_main_transcript(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        entries = [_entry(i) for i in range(7)]
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", entries)

        store = InMemorySessionStore()
        await import_session_to_store(SESSION_ID, store, directory=str(cwd))

        key: SessionKey = {"project_key": project_key, "session_id": SESSION_ID}
        assert store.get_entries(key) == entries

    @pytest.mark.asyncio
    async def test_batching_calls_append_per_chunk(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        entries = [_entry(i) for i in range(5)]
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", entries)

        store = InMemorySessionStore()
        spy = AsyncMock(wraps=store.append)
        store.append = spy  # type: ignore[method-assign]

        await import_session_to_store(
            SESSION_ID, store, directory=str(cwd), batch_size=2
        )

        # 5 entries / batch_size 2 → 3 append() calls (2 + 2 + 1).
        assert spy.await_count == 3
        key: SessionKey = {"project_key": project_key, "session_id": SESSION_ID}
        assert spy.await_args_list[0].args == (key, entries[0:2])
        assert spy.await_args_list[1].args == (key, entries[2:4])
        assert spy.await_args_list[2].args == (key, entries[4:5])
        assert store.get_entries(key) == entries

    @pytest.mark.asyncio
    async def test_skips_blank_lines(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        path = claude_dir / f"{SESSION_ID}.jsonl"
        path.write_text(
            json.dumps(_entry(0)) + "\n\n" + json.dumps(_entry(1)) + "\n",
            encoding="utf-8",
        )

        store = InMemorySessionStore()
        await import_session_to_store(SESSION_ID, store, directory=str(cwd))

        key: SessionKey = {"project_key": project_key, "session_id": SESSION_ID}
        assert store.get_entries(key) == [_entry(0), _entry(1)]

    @pytest.mark.asyncio
    async def test_nonpositive_batch_size_uses_default(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        entries = [_entry(i) for i in range(3)]
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", entries)

        store = InMemorySessionStore()
        spy = AsyncMock(wraps=store.append)
        store.append = spy  # type: ignore[method-assign]

        await import_session_to_store(
            SESSION_ID, store, directory=str(cwd), batch_size=0
        )

        assert spy.await_count == 1
        key: SessionKey = {"project_key": project_key, "session_id": SESSION_ID}
        assert store.get_entries(key) == entries


# ---------------------------------------------------------------------------
# Subagent transcripts
# ---------------------------------------------------------------------------


class TestSubagents:
    @pytest.mark.asyncio
    async def test_imports_subagent_transcripts_with_subpath(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", [_entry(0)])
        sub_entries = [_entry(10), _entry(11)]
        _write_jsonl(
            claude_dir / SESSION_ID / "subagents" / "agent-abc.jsonl", sub_entries
        )

        store = InMemorySessionStore()
        await import_session_to_store(SESSION_ID, store, directory=str(cwd))

        sub_key: SessionKey = {
            "project_key": project_key,
            "session_id": SESSION_ID,
            "subpath": "subagents/agent-abc",
        }
        assert store.get_entries(sub_key) == sub_entries
        assert await store.list_subkeys(
            {"project_key": project_key, "session_id": SESSION_ID}
        ) == ["subagents/agent-abc"]

    @pytest.mark.asyncio
    async def test_imports_nested_subagent_transcripts(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", [_entry(0)])
        nested = claude_dir / SESSION_ID / "subagents" / "workflows" / "run-1"
        _write_jsonl(nested / "agent-def.jsonl", [_entry(20)])

        store = InMemorySessionStore()
        await import_session_to_store(SESSION_ID, store, directory=str(cwd))

        sub_key: SessionKey = {
            "project_key": project_key,
            "session_id": SESSION_ID,
            "subpath": "subagents/workflows/run-1/agent-def",
        }
        assert store.get_entries(sub_key) == [_entry(20)]

    @pytest.mark.asyncio
    async def test_imports_meta_json_sidecar_as_agent_metadata(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", [_entry(0)])
        sub_dir = claude_dir / SESSION_ID / "subagents"
        _write_jsonl(sub_dir / "agent-abc.jsonl", [_entry(10)])
        (sub_dir / "agent-abc.meta.json").write_text(
            json.dumps({"agentType": "coder", "worktreePath": "/tmp/wt"}),
            encoding="utf-8",
        )

        store = InMemorySessionStore()
        await import_session_to_store(SESSION_ID, store, directory=str(cwd))

        sub_key: SessionKey = {
            "project_key": project_key,
            "session_id": SESSION_ID,
            "subpath": "subagents/agent-abc",
        }
        stored = store.get_entries(sub_key)
        assert stored[0] == _entry(10)
        assert stored[1] == {
            "type": "agent_metadata",
            "agentType": "coder",
            "worktreePath": "/tmp/wt",
        }

    @pytest.mark.asyncio
    async def test_include_subagents_false_skips_subagents(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", [_entry(0)])
        _write_jsonl(
            claude_dir / SESSION_ID / "subagents" / "agent-abc.jsonl", [_entry(10)]
        )

        store = InMemorySessionStore()
        await import_session_to_store(
            SESSION_ID, store, directory=str(cwd), include_subagents=False
        )

        assert (
            await store.list_subkeys(
                {"project_key": project_key, "session_id": SESSION_ID}
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_no_subagents_dir_is_noop(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", [_entry(0)])

        store = InMemorySessionStore()
        # include_subagents defaults to True; absence of the dir must not raise.
        await import_session_to_store(SESSION_ID, store, directory=str(cwd))

        key: SessionKey = {"project_key": project_key, "session_id": SESSION_ID}
        assert store.get_entries(key) == [_entry(0)]


# ---------------------------------------------------------------------------
# Validation / errors
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.asyncio
    async def test_invalid_uuid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            await import_session_to_store("../../etc/passwd", InMemorySessionStore())

    @pytest.mark.asyncio
    async def test_session_not_found_raises(self, claude_dir: Path, cwd: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            await import_session_to_store(
                SESSION_ID, InMemorySessionStore(), directory=str(cwd)
            )


# ---------------------------------------------------------------------------
# Round-trip with file_path_to_session_key
# ---------------------------------------------------------------------------


class TestKeyParity:
    @pytest.mark.asyncio
    async def test_subpath_matches_file_path_to_session_key(
        self, claude_dir: Path, cwd: Path, project_key: str
    ) -> None:
        """Import keys must match what TranscriptMirrorBatcher would have
        produced for the same on-disk file, so an imported session is
        indistinguishable from a live-mirrored one."""
        from claude_agent_sdk._internal.session_store import file_path_to_session_key

        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", [_entry(0)])
        sub_file = claude_dir / SESSION_ID / "subagents" / "agent-xyz.jsonl"
        _write_jsonl(sub_file, [_entry(1)])

        store = InMemorySessionStore()
        await import_session_to_store(SESSION_ID, store, directory=str(cwd))

        projects_dir = str(claude_dir.parent)
        expected_main = file_path_to_session_key(
            str(claude_dir / f"{SESSION_ID}.jsonl"), projects_dir
        )
        expected_sub = file_path_to_session_key(str(sub_file), projects_dir)

        assert expected_main is not None and expected_sub is not None
        assert store.get_entries(expected_main) == [_entry(0)]
        assert store.get_entries(expected_sub) == [_entry(1)]

    @pytest.mark.asyncio
    async def test_directory_none_keys_from_resolved_path_not_cwd(
        self,
        claude_dir: Path,
        project_key: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``directory=None`` the resolver searches all project dirs;
        the destination key must come from where the file was *found*, not
        the process cwd. Regression for divergence with
        ``file_path_to_session_key()``."""
        _write_jsonl(claude_dir / f"{SESSION_ID}.jsonl", [_entry(0)])

        # Run the import from an unrelated cwd.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert project_key_for_directory(None) != project_key  # precondition

        store = InMemorySessionStore()
        await import_session_to_store(SESSION_ID, store, directory=None)

        key: SessionKey = {"project_key": project_key, "session_id": SESSION_ID}
        assert store.get_entries(key) == [_entry(0)]
