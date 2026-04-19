"""Tests for SessionStore-backed resume materialization."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    InMemorySessionStore,
    query,
)
from claude_agent_sdk._internal.session_resume import (
    MaterializedResume,
    materialize_resume_session,
)
from claude_agent_sdk._internal.session_store import project_key_for_directory
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import SessionKey, SessionStore, SessionStoreEntry

SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"
SESSION_ID_2 = "660e8400-e29b-41d4-a716-446655440000"


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
    # Clearing the auth env vars above is exactly what makes _copy_auth_files()
    # fall through to the macOS Keychain branch — stub it out so tests on a
    # logged-in macOS host don't read (or write to temp dirs) real credentials.
    monkeypatch.setattr(
        "claude_agent_sdk._internal.session_resume._read_keychain_credentials",
        lambda: None,
    )
    return home


# ---------------------------------------------------------------------------
# materialize_resume_session — None cases
# ---------------------------------------------------------------------------


class TestNoMaterialization:
    @pytest.mark.asyncio
    async def test_no_store(self, cwd: Path) -> None:
        opts = ClaudeAgentOptions(cwd=cwd, resume=SESSION_ID)
        assert await materialize_resume_session(opts) is None

    @pytest.mark.asyncio
    async def test_no_resume_or_continue(self, cwd: Path) -> None:
        opts = ClaudeAgentOptions(cwd=cwd, session_store=InMemorySessionStore())
        assert await materialize_resume_session(opts) is None

    @pytest.mark.asyncio
    async def test_non_uuid_session_id(self, cwd: Path) -> None:
        store = InMemorySessionStore()
        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=store, resume="../../etc/passwd"
        )
        assert await materialize_resume_session(opts) is None

    @pytest.mark.asyncio
    async def test_load_returns_none(self, cwd: Path) -> None:
        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=InMemorySessionStore(), resume=SESSION_ID
        )
        assert await materialize_resume_session(opts) is None

    @pytest.mark.asyncio
    async def test_load_returns_empty(self, cwd: Path, project_key: str) -> None:
        store = InMemorySessionStore()
        await store.append({"project_key": project_key, "session_id": SESSION_ID}, [])
        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        assert await materialize_resume_session(opts) is None

    @pytest.mark.asyncio
    async def test_continue_with_empty_list_sessions(self, cwd: Path) -> None:
        opts = ClaudeAgentOptions(
            cwd=cwd,
            session_store=InMemorySessionStore(),
            continue_conversation=True,
        )
        assert await materialize_resume_session(opts) is None


# ---------------------------------------------------------------------------
# materialize_resume_session — happy paths
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_resume_writes_jsonl_and_cleanup_removes_dir(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        store = InMemorySessionStore()
        entries: list[SessionStoreEntry] = [
            {
                "type": "user",
                "uuid": "u1",
                "message": {"role": "user", "content": "hi"},
            },
            {"type": "assistant", "uuid": "a1"},
        ]
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID}, entries
        )

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        m = await materialize_resume_session(opts)
        assert m is not None
        assert m.resume_session_id == SESSION_ID
        assert m.config_dir.is_dir()

        jsonl = m.config_dir / "projects" / project_key / f"{SESSION_ID}.jsonl"
        assert jsonl.is_file()
        lines = jsonl.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == entries[0]
        assert json.loads(lines[1]) == entries[1]

        await m.cleanup()
        assert not m.config_dir.exists()

    @pytest.mark.asyncio
    async def test_credentials_redacted(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        # Seed a credentials file with a refreshToken under the fake ~/.claude/.
        config = isolated_home / ".claude"
        config.mkdir()
        (config / ".credentials.json").write_text(
            json.dumps(
                {"claudeAiOauth": {"accessToken": "at", "refreshToken": "SECRET"}}
            )
        )
        (isolated_home / ".claude.json").write_text('{"theme":"dark"}')

        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        m = await materialize_resume_session(opts)
        assert m is not None

        creds = json.loads((m.config_dir / ".credentials.json").read_text())
        assert creds["claudeAiOauth"]["accessToken"] == "at"
        assert "refreshToken" not in creds["claudeAiOauth"]

        # .claude.json copied verbatim from ~ (not ~/.claude/).
        assert (m.config_dir / ".claude.json").read_text() == '{"theme":"dark"}'

        await m.cleanup()

    @pytest.mark.asyncio
    async def test_credentials_from_caller_config_dir_env(
        self, cwd: Path, project_key: str, tmp_path: Path
    ) -> None:
        # options.env CLAUDE_CONFIG_DIR takes precedence over ~ lookup.
        custom = tmp_path / "custom-config"
        custom.mkdir()
        (custom / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "fromenv"}})
        )

        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )
        opts = ClaudeAgentOptions(
            cwd=cwd,
            session_store=store,
            resume=SESSION_ID,
            env={"CLAUDE_CONFIG_DIR": str(custom)},
        )
        m = await materialize_resume_session(opts)
        assert m is not None
        creds = json.loads((m.config_dir / ".credentials.json").read_text())
        assert creds["claudeAiOauth"]["accessToken"] == "fromenv"
        await m.cleanup()

    @pytest.mark.asyncio
    async def test_credentials_from_keychain_fallback(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no file/env credentials exist, the macOS keychain fallback
        supplies them. Uses a local override of the fixture-level stub so the
        production fallback path is exercised without touching a real keychain."""
        monkeypatch.setattr(
            "claude_agent_sdk._internal.session_resume._read_keychain_credentials",
            lambda: json.dumps(
                {"claudeAiOauth": {"accessToken": "kc", "refreshToken": "SECRET"}}
            ),
        )

        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )
        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        m = await materialize_resume_session(opts)
        assert m is not None
        creds = json.loads((m.config_dir / ".credentials.json").read_text())
        assert creds["claudeAiOauth"]["accessToken"] == "kc"
        assert "refreshToken" not in creds["claudeAiOauth"]
        await m.cleanup()

    @pytest.mark.asyncio
    async def test_continue_picks_most_recent(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        store = InMemorySessionStore()
        # Older session first.
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "old"}],
        )
        store._mtimes[f"{project_key}/{SESSION_ID}"] = 1000
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID_2},
            [{"type": "user", "uuid": "new"}],
        )
        store._mtimes[f"{project_key}/{SESSION_ID_2}"] = 2000

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=store, continue_conversation=True
        )
        m = await materialize_resume_session(opts)
        assert m is not None
        assert m.resume_session_id == SESSION_ID_2
        await m.cleanup()

    @pytest.mark.asyncio
    async def test_continue_skips_sidechain_sessions(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        """Sidechain transcripts are mirrored as ordinary top-level keys and
        often have the highest mtime. ``continue_conversation`` must skip them
        and resume the most-recent main session, matching the CLI's own
        ``--continue`` filter."""
        store = InMemorySessionStore()
        sidechain_sid = str(uuid.uuid4())
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "main"}],
        )
        store._mtimes[f"{project_key}/{SESSION_ID}"] = 1000
        await store.append(
            {"project_key": project_key, "session_id": sidechain_sid},
            [{"type": "user", "uuid": "sc", "isSidechain": True}],
        )
        store._mtimes[f"{project_key}/{sidechain_sid}"] = 2000  # newer

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=store, continue_conversation=True
        )
        m = await materialize_resume_session(opts)
        assert m is not None
        assert m.resume_session_id == SESSION_ID
        await m.cleanup()

    @pytest.mark.asyncio
    async def test_continue_returns_none_when_only_sidechains(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        store = InMemorySessionStore()
        sc = str(uuid.uuid4())
        await store.append(
            {"project_key": project_key, "session_id": sc},
            [{"type": "user", "isSidechain": True}],
        )
        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=store, continue_conversation=True
        )
        assert await materialize_resume_session(opts) is None

    @pytest.mark.asyncio
    async def test_continue_tie_break_is_deterministic(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        """Parity with TS: when two sessions share the same mtime,
        continue_conversation picks one deterministically (sorted() is stable
        on equal keys → first listed wins; repeated calls agree)."""
        store = InMemorySessionStore()
        for sid in (SESSION_ID, SESSION_ID_2):
            await store.append(
                {"project_key": project_key, "session_id": sid},
                [{"type": "user", "uuid": f"u-{sid}"}],
            )
            store._mtimes[f"{project_key}/{sid}"] = 5000  # identical mtimes

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=store, continue_conversation=True
        )
        first = await materialize_resume_session(opts)
        assert first is not None
        first_id = first.resume_session_id
        await first.cleanup()

        # Second call must pick the same session.
        second = await materialize_resume_session(opts)
        assert second is not None
        assert second.resume_session_id == first_id
        await second.cleanup()

    @pytest.mark.asyncio
    async def test_write_jsonl_round_trip(self, tmp_path: Path) -> None:
        """Parity with TS writeEntriesToJsonlFile: streamed output is
        byte-identical to map+join and round-trips back to the input."""
        from claude_agent_sdk._internal.session_resume import _write_jsonl

        entries = [
            {
                "type": "user" if i % 2 == 0 else "assistant",
                "uuid": f"uuid-{i}",
                "message": {"role": "user", "content": f'line {i} "q" \n nl'},
                "nested": {"a": [i, i + 1], "b": None},
            }
            for i in range(100)
        ]
        out = tmp_path / "stream.jsonl"
        _write_jsonl(out, entries)

        written = out.read_text(encoding="utf-8")
        reference = (
            "\n".join(json.dumps(e, separators=(",", ":")) for e in entries) + "\n"
        )
        assert written == reference

        lines = written.split("\n")
        assert lines[-1] == ""  # trailing newline
        parsed = [json.loads(ln) for ln in lines[:-1]]
        assert parsed == entries


# ---------------------------------------------------------------------------
# Subkey materialization
# ---------------------------------------------------------------------------


class TestSubkeyMaterialization:
    @pytest.mark.asyncio
    async def test_subagent_jsonl_and_meta_json(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        store = InMemorySessionStore()
        main_key: SessionKey = {"project_key": project_key, "session_id": SESSION_ID}
        await store.append(main_key, [{"type": "user", "uuid": "u1"}])

        sub_key: SessionKey = {
            "project_key": project_key,
            "session_id": SESSION_ID,
            "subpath": "subagents/agent-abc",
        }
        await store.append(
            sub_key,
            [
                {"type": "user", "uuid": "su1"},
                {"type": "assistant", "uuid": "sa1"},
                {"type": "agent_metadata", "agentType": "general", "ver": 1},
            ],
        )

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        m = await materialize_resume_session(opts)
        assert m is not None

        session_dir = m.config_dir / "projects" / project_key / SESSION_ID
        jsonl = session_dir / "subagents" / "agent-abc.jsonl"
        meta = session_dir / "subagents" / "agent-abc.meta.json"

        assert jsonl.is_file()
        lines = [json.loads(ln) for ln in jsonl.read_text().splitlines()]
        assert lines == [
            {"type": "user", "uuid": "su1"},
            {"type": "assistant", "uuid": "sa1"},
        ]

        assert meta.is_file()
        # 'type' field stripped from metadata.
        assert json.loads(meta.read_text()) == {"agentType": "general", "ver": 1}

        await m.cleanup()

    @pytest.mark.asyncio
    async def test_traversal_guards(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        class EvilStore(InMemorySessionStore):
            async def list_subkeys(self, key):  # type: ignore[override]
                return [
                    "",
                    ".",
                    "./",
                    "a/.",
                    "subagents/.",
                    "/etc/passwd",
                    "../escape",
                    "a/../b",
                    "C:escape",
                    "C:\\abs",
                    "subagents/agent\x00x",
                    "subagents/agent-ok",
                ]

            async def load(self, key):  # type: ignore[override]
                if key.get("subpath") == "subagents/agent-ok":
                    return [{"type": "user", "uuid": "ok"}]
                if key.get("subpath") is None:
                    return [{"type": "user", "uuid": "main"}]
                # Unsafe subpaths should never be loaded — fail loudly if they are.
                raise AssertionError(f"loaded unsafe subpath {key!r}")

        opts = ClaudeAgentOptions(cwd=cwd, session_store=EvilStore(), resume=SESSION_ID)
        m = await materialize_resume_session(opts)
        assert m is not None

        session_dir = m.config_dir / "projects" / project_key / SESSION_ID
        # Only the safe subpath was written.
        assert (session_dir / "subagents" / "agent-ok.jsonl").is_file()
        # Main transcript was not overwritten by any subkey load (regression
        # for subpath='.' which previously resolved to project_dir/{sid}.jsonl).
        main_jsonl = m.config_dir / "projects" / project_key / f"{SESSION_ID}.jsonl"
        assert [json.loads(line) for line in main_jsonl.read_text().splitlines()] == [
            {"type": "user", "uuid": "main"}
        ]
        # Nothing escaped the temp dir.
        for root, _dirs, files in os.walk(m.config_dir):
            for f in files:
                p = Path(root) / f
                assert str(p).startswith(str(m.config_dir))
        await m.cleanup()

    @pytest.mark.asyncio
    async def test_store_without_list_subkeys_skips_subagents(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        class MinimalStore(SessionStore):
            async def append(self, key, entries):  # type: ignore[override]
                pass

            async def load(self, key):  # type: ignore[override]
                return [{"type": "user", "uuid": "u1"}]

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=MinimalStore(), resume=SESSION_ID
        )
        m = await materialize_resume_session(opts)
        assert m is not None
        # Just the main transcript — no subagent dir created.
        assert (
            m.config_dir / "projects" / project_key / f"{SESSION_ID}.jsonl"
        ).is_file()
        assert not (m.config_dir / "projects" / project_key / SESSION_ID).exists()
        await m.cleanup()


# ---------------------------------------------------------------------------
# Timeouts and error wrapping
# ---------------------------------------------------------------------------


class TestTimeoutsAndErrors:
    @pytest.mark.asyncio
    async def test_load_timeout_raises(self, cwd: Path) -> None:
        class SlowStore(SessionStore):
            async def append(self, key, entries):  # type: ignore[override]
                pass

            async def load(self, key):  # type: ignore[override]
                await asyncio.sleep(3600)
                return None

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=SlowStore(), resume=SESSION_ID, load_timeout_ms=50
        )
        with pytest.raises(RuntimeError, match="timed out"):
            await materialize_resume_session(opts)

    @pytest.mark.asyncio
    async def test_list_sessions_timeout_on_continue_path(self, cwd: Path) -> None:
        """Parity with TS: load_timeout_ms applies to list_sessions() during
        continue_conversation, not just load()."""

        class HungListStore(SessionStore):
            async def append(self, key, entries):  # type: ignore[override]
                pass

            async def load(self, key):  # type: ignore[override]
                return None

            async def list_sessions(self, project_key):  # type: ignore[override]
                await asyncio.sleep(3600)
                return []

        opts = ClaudeAgentOptions(
            cwd=cwd,
            session_store=HungListStore(),
            continue_conversation=True,
            load_timeout_ms=50,
        )
        with pytest.raises(RuntimeError, match=r"list_sessions\(\).*timed out"):
            await materialize_resume_session(opts)

    @pytest.mark.asyncio
    async def test_list_subkeys_timeout_raises_and_cleans_temp_dir(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        """Parity with TS: load_timeout_ms applies to list_subkeys(); the temp
        dir created by the (already-succeeded) load() is removed on rethrow."""

        class HungSubkeysStore(InMemorySessionStore):
            async def list_subkeys(self, key):  # type: ignore[override]
                await asyncio.sleep(3600)
                return []

        store = HungSubkeysStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )
        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=store, resume=SESSION_ID, load_timeout_ms=50
        )

        real_mkdtemp = tempfile.mkdtemp
        created: list[str] = []

        def spy(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            created.append(d)
            return d

        with (
            patch("tempfile.mkdtemp", side_effect=spy),
            pytest.raises(RuntimeError, match=r"list_subkeys\(\).*timed out"),
        ):
            await materialize_resume_session(opts)

        assert created, "load() succeeded so mkdtemp should have run"
        assert not Path(created[0]).exists()

    @pytest.mark.asyncio
    async def test_cancelled_after_mkdtemp_cleans_temp_dir(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        """``asyncio.CancelledError`` is ``BaseException`` (not ``Exception``)
        since 3.8 — the cleanup-on-failure block must catch it so a temp dir
        already containing ``.credentials.json`` is not leaked when the outer
        task is cancelled mid-subkey-load."""

        class HungSubkeysStore(InMemorySessionStore):
            async def list_subkeys(self, key):  # type: ignore[override]
                await asyncio.sleep(3600)
                return []

        store = HungSubkeysStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )
        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)

        real_mkdtemp = tempfile.mkdtemp
        created: list[str] = []

        def spy(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            created.append(d)
            return d

        with patch("tempfile.mkdtemp", side_effect=spy):
            task = asyncio.create_task(materialize_resume_session(opts))
            # Let it run past mkdtemp and into list_subkeys.
            for _ in range(50):
                await asyncio.sleep(0)
                if created:
                    break
            assert created, "mkdtemp should have run before cancellation"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert not Path(created[0]).exists()

    @pytest.mark.asyncio
    async def test_non_json_serializable_entry_surfaces_clear_error(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        """A store returning non-JSON-serializable values fails materialization
        with a contextual error and leaves no temp dir.

        Python's json.dumps raises TypeError on non-serializable values; the
        materialize wrapper does not catch it — assert it surfaces and cleans
        up.
        """

        class BadStore(SessionStore):
            async def append(self, key, entries):  # type: ignore[override]
                pass

            async def load(self, key):  # type: ignore[override]
                return [{"type": "user", "uuid": "u1", "blob": {1, 2, 3}}]

        opts = ClaudeAgentOptions(cwd=cwd, session_store=BadStore(), resume=SESSION_ID)

        real_mkdtemp = tempfile.mkdtemp
        created: list[str] = []

        def spy(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            created.append(d)
            return d

        with patch("tempfile.mkdtemp", side_effect=spy), pytest.raises(TypeError):
            await materialize_resume_session(opts)

        assert created
        assert not Path(created[0]).exists()

    @pytest.mark.asyncio
    async def test_load_exception_wrapped(self, cwd: Path) -> None:
        class BrokenStore(SessionStore):
            async def append(self, key, entries):  # type: ignore[override]
                pass

            async def load(self, key):  # type: ignore[override]
                raise OSError("network down")

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=BrokenStore(), resume=SESSION_ID
        )
        with pytest.raises(RuntimeError, match="network down"):
            await materialize_resume_session(opts)

    @pytest.mark.asyncio
    async def test_failure_after_mkdir_cleans_temp_dir(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        """If list_subkeys raises after the temp dir is created, the dir is removed."""

        class FailLateStore(InMemorySessionStore):
            async def list_subkeys(self, key):  # type: ignore[override]
                raise OSError("boom")

        store = FailLateStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)

        # Capture the temp dir created by mkdtemp so we can assert it's gone.

        real_mkdtemp = tempfile.mkdtemp
        created: list[str] = []

        def spy(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            created.append(d)
            return d

        with (
            patch("tempfile.mkdtemp", side_effect=spy),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await materialize_resume_session(opts)

        assert created
        assert not Path(created[0]).exists()


# ---------------------------------------------------------------------------
# Integration: ClaudeSDKClient.connect() wiring
# ---------------------------------------------------------------------------


def _make_mock_transport() -> Any:
    mock_transport = AsyncMock()

    async def mock_receive():
        return
        yield  # pragma: no cover

    mock_transport.read_messages = mock_receive
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)
    return mock_transport


class TestClientIntegration:
    @pytest.mark.asyncio
    async def test_connect_passes_config_dir_resume_and_suppresses_continue(
        self, cwd: Path, project_key: str, isolated_home: Path
    ) -> None:
        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        captured: dict[str, Any] = {}
        mock_transport = _make_mock_transport()

        def capture_transport(*, prompt, options):
            captured["options"] = options
            return mock_transport

        opts = ClaudeAgentOptions(
            cwd=cwd,
            session_store=store,
            continue_conversation=True,
            cli_path="/usr/bin/claude",
        )
        client = ClaudeSDKClient(options=opts)

        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli."
                "SubprocessCLITransport",
                side_effect=capture_transport,
            ),
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
            ),
        ):
            await client.connect()

        # The options passed to the transport carry the materialized
        # overrides.
        transport_opts: ClaudeAgentOptions = captured["options"]
        assert transport_opts.resume == SESSION_ID
        assert transport_opts.continue_conversation is False
        config_dir = transport_opts.env["CLAUDE_CONFIG_DIR"]
        assert Path(config_dir).is_dir()
        assert (
            Path(config_dir) / "projects" / project_key / f"{SESSION_ID}.jsonl"
        ).is_file()

        # Build the actual CLI command (real class, outside the patch) to
        # assert exact flag behavior.
        cmd = SubprocessCLITransport(
            prompt="x", options=transport_opts
        )._build_command()
        assert "--resume" in cmd
        assert cmd[cmd.index("--resume") + 1] == SESSION_ID
        assert "--continue" not in cmd

        # Batcher points at the temp projects dir.
        assert client._query is not None
        batcher = client._query._transcript_mirror_batcher
        assert batcher.projects_dir == str(Path(config_dir) / "projects")

        # Original options object untouched.
        assert opts.continue_conversation is True
        assert opts.resume is None
        assert "CLAUDE_CONFIG_DIR" not in opts.env

        await client.disconnect()

        # Cleanup removed the temp dir.
        assert not Path(config_dir).exists()

    @pytest.mark.asyncio
    async def test_custom_transport_skips_materialization(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        """A pre-constructed custom transport never sees the materialized
        options, so loading the store and writing .credentials.json to a
        temp dir would be wasted (and leave the access token on disk for
        the session lifetime). connect() must skip materialization."""

        class SpyStore(InMemorySessionStore):
            load_calls = 0

            async def load(self, key):  # type: ignore[override]
                SpyStore.load_calls += 1
                return await super().load(key)

        store = SpyStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        client = ClaudeSDKClient(options=opts, transport=_make_mock_transport())

        with patch(
            "claude_agent_sdk._internal.query.Query.initialize",
            new_callable=AsyncMock,
        ):
            await client.connect()
            assert SpyStore.load_calls == 0
            assert not track_resume_dirs  # mkdtemp never ran
            assert client._materialized is None
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_query_custom_transport_skips_materialization(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        """Same gate for the one-shot ``query()`` path."""

        class SpyStore(InMemorySessionStore):
            load_calls = 0

            async def load(self, key):  # type: ignore[override]
                SpyStore.load_calls += 1
                return await super().load(key)

        store = SpyStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        custom = _make_mock_transport()
        custom.connect = AsyncMock(side_effect=OSError("spawn failed"))
        with pytest.raises(OSError, match="spawn failed"):
            async for _ in query(prompt="hi", options=opts, transport=custom):
                pass  # pragma: no cover

        # Gate runs before transport.connect(); materialization never happened.
        assert SpyStore.load_calls == 0
        assert not track_resume_dirs

    @pytest.mark.asyncio
    async def test_connect_no_materialization_passthrough(
        self, cwd: Path, isolated_home: Path
    ) -> None:
        """No store → options reach the transport unchanged."""
        captured: dict[str, Any] = {}
        mock_transport = _make_mock_transport()

        def capture_transport(*, prompt, options):
            captured["options"] = options
            return mock_transport

        opts = ClaudeAgentOptions(
            cwd=cwd, resume=SESSION_ID, cli_path="/usr/bin/claude"
        )
        client = ClaudeSDKClient(options=opts)

        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli."
                "SubprocessCLITransport",
                side_effect=capture_transport,
            ),
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
            ),
        ):
            await client.connect()
            assert "CLAUDE_CONFIG_DIR" not in captured["options"].env
            assert captured["options"].resume == SESSION_ID
            await client.disconnect()


# ---------------------------------------------------------------------------
# Temp-dir leak on spawn failure
# ---------------------------------------------------------------------------


@pytest.fixture
def track_resume_dirs() -> Any:
    """Spy on ``tempfile.mkdtemp`` to capture claude-resume-* dirs."""
    real_mkdtemp = tempfile.mkdtemp
    created: list[Path] = []

    def spy(*a: Any, **kw: Any) -> str:
        d = real_mkdtemp(*a, **kw)
        if "claude-resume-" in d:
            created.append(Path(d))
        return d

    with patch("tempfile.mkdtemp", side_effect=spy):
        yield created


class TestSpawnFailureCleanup:
    """The materialized temp dir contains a .credentials.json copy. It must be
    removed even when transport.connect() raises before any try/finally that
    normally guards cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_retries_on_transient_os_error(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Windows AV/indexer can briefly hold ``.credentials.json`` open;
        ``cleanup()`` must retry rmtree on EPERM/EBUSY so the access token
        doesn't leak in temp."""
        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )
        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        m = await materialize_resume_session(opts)
        assert m is not None
        config_dir = m.config_dir

        calls: list[Any] = []
        real_rmtree = shutil.rmtree

        def fake_rmtree(p: Any, **kw: Any) -> None:
            calls.append((p, kw))
            if len(calls) <= 2 and not kw.get("ignore_errors"):
                raise PermissionError(errno.EPERM, "held by indexer")
            if Path(p).exists():
                real_rmtree(p, **kw)

        monkeypatch.setattr(shutil, "rmtree", fake_rmtree)
        await m.cleanup()

        assert not config_dir.exists()
        assert len(calls) >= 3  # 2 failures + 1 success

    @pytest.mark.asyncio
    async def test_failure_path_retries_rmtree(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        track_resume_dirs: list[Path],
    ) -> None:
        """The except-BaseException cleanup path also retries on EPERM."""

        class FailLateStore(InMemorySessionStore):
            async def list_subkeys(self, key):  # type: ignore[override]
                raise OSError("boom")

        store = FailLateStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        calls: list[Any] = []
        real_rmtree = shutil.rmtree

        def fake_rmtree(p: Any, **kw: Any) -> None:
            calls.append((p, kw))
            if len(calls) <= 2 and not kw.get("ignore_errors"):
                raise PermissionError(errno.EPERM, "held by indexer")
            if Path(p).exists():
                real_rmtree(p, **kw)

        monkeypatch.setattr(shutil, "rmtree", fake_rmtree)

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        with pytest.raises(RuntimeError, match="boom"):
            await materialize_resume_session(opts)

        assert track_resume_dirs
        assert not track_resume_dirs[0].exists()
        assert len(calls) >= 3

    @pytest.mark.asyncio
    async def test_client_connect_failure_removes_temp_dir(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        mock_transport = _make_mock_transport()
        mock_transport.connect = AsyncMock(side_effect=OSError("spawn failed"))

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        client = ClaudeSDKClient(options=opts)

        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli."
                "SubprocessCLITransport",
                return_value=mock_transport,
            ),
            pytest.raises(OSError, match="spawn failed"),
        ):
            await client.connect()

        assert track_resume_dirs, "materialize_resume_session never created a temp dir"
        for d in track_resume_dirs:
            assert not d.exists(), f"leaked temp dir {d}"
        assert client._materialized is None

    @pytest.mark.asyncio
    async def test_client_aenter_failure_removes_temp_dir(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        mock_transport = _make_mock_transport()
        mock_transport.connect = AsyncMock(side_effect=OSError("spawn failed"))

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)

        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli."
                "SubprocessCLITransport",
                return_value=mock_transport,
            ),
            pytest.raises(OSError, match="spawn failed"),
        ):
            async with ClaudeSDKClient(options=opts):
                pass  # pragma: no cover

        assert track_resume_dirs
        for d in track_resume_dirs:
            assert not d.exists(), f"leaked temp dir {d}"

    @pytest.mark.asyncio
    async def test_client_initialize_failure_closes_subprocess_before_cleanup(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        """When connect() fails *after* the subprocess spawned (at
        query.initialize()), the subprocess/read task must be closed before
        the temp CLAUDE_CONFIG_DIR is removed and must not leak."""
        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        mock_transport = _make_mock_transport()
        # transport.connect() succeeds → subprocess "spawned"; failure happens
        # later at Query.initialize().

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        client = ClaudeSDKClient(options=opts)

        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli."
                "SubprocessCLITransport",
                return_value=mock_transport,
            ),
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
                side_effect=CLIConnectionError("control timeout"),
            ),
            pytest.raises(CLIConnectionError, match="control timeout"),
        ):
            await client.connect()

        # disconnect() must have run: query/transport cleared, transport.close
        # awaited (subprocess terminated), then temp dir removed.
        assert client._query is None
        assert client._transport is None
        assert client._materialized is None
        mock_transport.close.assert_awaited()
        assert track_resume_dirs
        for d in track_resume_dirs:
            assert not d.exists(), f"leaked temp dir {d}"

    @pytest.mark.asyncio
    async def test_connect_cancelled_before_spawn_removes_temp_dir(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        """Parity with TS 'close() before deferred spawn': Python has no
        deferred spawn, so the equivalent race is cancelling connect() while
        it awaits a slow store.load(). The cancel must not hang and must
        leave no claude-resume-* dir on disk.

        load() is awaited before mkdtemp(), so cancelling here exercises the
        no-dir-yet path. The post-mkdtemp failure path is covered by
        test_failure_after_mkdir_cleans_temp_dir.
        """

        class SlowStore(SessionStore):
            async def append(self, key, entries):  # type: ignore[override]
                pass

            async def load(self, key):  # type: ignore[override]
                await asyncio.sleep(3600)
                return [{"type": "user", "uuid": "u1"}]

        spawn_count = 0

        def fake_transport(**_kw):
            nonlocal spawn_count
            spawn_count += 1
            raise AssertionError("should not spawn")

        opts = ClaudeAgentOptions(
            cwd=cwd, session_store=SlowStore(), resume=SESSION_ID, load_timeout_ms=10000
        )
        client = ClaudeSDKClient(options=opts)

        with patch(
            "claude_agent_sdk._internal.transport.subprocess_cli."
            "SubprocessCLITransport",
            side_effect=fake_transport,
        ):
            connect_task = asyncio.create_task(client.connect())
            await asyncio.sleep(0)  # let connect reach the awaited load()
            connect_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(connect_task, timeout=2)

        assert spawn_count == 0
        for d in track_resume_dirs:
            assert not d.exists(), f"leaked temp dir {d}"
        # disconnect() after a cancelled connect must be a safe no-op.
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_query_transport_failure_removes_temp_dir(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        mock_transport = _make_mock_transport()
        mock_transport.connect = AsyncMock(side_effect=OSError("spawn failed"))

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)

        with (
            patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport",
                return_value=mock_transport,
            ),
            pytest.raises(OSError, match="spawn failed"),
        ):
            async for _ in query(prompt="hi", options=opts):
                pass  # pragma: no cover

        assert track_resume_dirs
        for d in track_resume_dirs:
            assert not d.exists(), f"leaked temp dir {d}"

    @pytest.mark.asyncio
    async def test_query_early_break_closes_transport_before_temp_dir_removed(
        self,
        cwd: Path,
        project_key: str,
        isolated_home: Path,
        track_resume_dirs: list[Path],
    ) -> None:
        """Regression: ``async for`` does not close its iterator when the loop
        body raises (PEP 533 deferred). When a consumer breaks early, the
        outer ``finally`` must explicitly ``aclose()`` the inner generator so
        ``transport.close()`` (subprocess termination) runs *before* the temp
        CLAUDE_CONFIG_DIR — which the subprocess is reading/writing — is
        removed."""
        store = InMemorySessionStore()
        await store.append(
            {"project_key": project_key, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1"}],
        )

        order: list[str] = []
        config_dir_at_close: dict[str, bool] = {}

        mock_transport = _make_mock_transport()

        async def mock_receive():
            yield {
                "type": "system",
                "subtype": "init",
                "data": {"session_id": SESSION_ID},
            }
            yield {"type": "system", "subtype": "noop", "data": {}}

        mock_transport.read_messages = mock_receive

        async def tracked_close() -> None:
            order.append("transport.close")
            config_dir_at_close["existed"] = track_resume_dirs[0].exists()

        mock_transport.close = tracked_close

        real_rmtree = shutil.rmtree

        def tracked_rmtree(p: Any, **kw: Any) -> None:
            order.append("rmtree")
            real_rmtree(p, **kw)

        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        from claude_agent_sdk._internal.client import InternalClient

        with (
            patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport",
                return_value=mock_transport,
            ),
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
            ),
            patch.object(shutil, "rmtree", tracked_rmtree),
        ):
            agen = InternalClient().process_query(prompt="hi", options=opts)
            async for _ in agen:
                break  # consumer abandons mid-stream
            await agen.aclose()

        assert track_resume_dirs, "materialize_resume_session never created a temp dir"
        assert "transport.close" in order, f"transport never closed: {order}"
        assert "rmtree" in order, f"temp dir never removed: {order}"
        assert order.index("transport.close") < order.index("rmtree"), (
            f"temp dir removed before subprocess terminated: {order}"
        )
        assert config_dir_at_close["existed"], (
            "temp CLAUDE_CONFIG_DIR was already gone when transport.close() ran"
        )
        for d in track_resume_dirs:
            assert not d.exists(), f"leaked temp dir {d}"


# ---------------------------------------------------------------------------
# MaterializedResume shape
# ---------------------------------------------------------------------------


def test_materialized_resume_dataclass() -> None:
    async def noop() -> None:
        pass

    m = MaterializedResume(
        config_dir=Path("/tmp/x"),
        resume_session_id=str(uuid.uuid4()),
        cleanup=noop,
    )
    assert m.config_dir == Path("/tmp/x")
