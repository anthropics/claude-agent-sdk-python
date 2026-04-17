"""Cross-platform tests for SessionStore path handling and auth resolution.

These tests run on the full CI matrix (Linux/macOS/Windows). Where behavior
is genuinely OS-dependent (drive-letter absolutism, Keychain availability),
the relevant case is gated with ``skipif`` so it executes on the matching
matrix leg instead of being mocked into a false positive.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from claude_agent_sdk import ClaudeAgentOptions, InMemorySessionStore
from claude_agent_sdk._internal.session_resume import (
    _copy_auth_files,
    _is_safe_subpath,
    _read_keychain_credentials,
    materialize_resume_session,
)
from claude_agent_sdk._internal.session_store import (
    file_path_to_session_key,
    project_key_for_directory,
)
from claude_agent_sdk._internal.sessions import _sanitize_path

SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~`` and clear auth env so tests never touch the real config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return home


# ---------------------------------------------------------------------------
# file_path_to_session_key — sep-agnostic parsing
# ---------------------------------------------------------------------------


class TestFilePathToSessionKey:
    """``file_path_to_session_key`` uses ``os.path.relpath`` + ``Path.parts``,
    so it must work with whatever separator the host OS uses. These tests
    build paths via ``os.path.join`` so the same assertions hold on every
    matrix leg."""

    def test_main_transcript_native_sep(self, tmp_path: Path) -> None:
        projects_dir = str(tmp_path)
        file_path = str(tmp_path / "my-project" / f"{SESSION_ID}.jsonl")
        assert os.sep in file_path  # path uses the host separator
        key = file_path_to_session_key(file_path, projects_dir)
        assert key == {"project_key": "my-project", "session_id": SESSION_ID}

    def test_subagent_transcript_native_sep_yields_forward_slash_subpath(
        self, tmp_path: Path
    ) -> None:
        """Subpath keys are always ``/``-joined regardless of ``os.sep`` so
        store keys round-trip across platforms."""
        projects_dir = str(tmp_path)
        file_path = str(
            tmp_path
            / "proj"
            / SESSION_ID
            / "subagents"
            / "workflows"
            / "run-1"
            / "agent-abc.jsonl"
        )
        key = file_path_to_session_key(file_path, projects_dir)
        assert key is not None
        assert key["project_key"] == "proj"
        assert key["session_id"] == SESSION_ID
        # Forward-slash even when os.sep == "\\"
        assert key["subpath"] == "subagents/workflows/run-1/agent-abc"
        assert "\\" not in key["subpath"]

    def test_file_outside_projects_dir_returns_none(self, tmp_path: Path) -> None:
        projects_dir = str(tmp_path / "projects")
        outside = str(tmp_path / "elsewhere" / f"{SESSION_ID}.jsonl")
        assert file_path_to_session_key(outside, projects_dir) is None

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="os.path.relpath only raises ValueError for cross-drive on Windows",
    )
    def test_different_drive_returns_none_on_windows(self) -> None:
        """Windows ``relpath`` raises when paths are on different drives — the
        function must catch it and return ``None`` instead of propagating."""
        key = file_path_to_session_key(
            r"D:\somewhere\proj\sid.jsonl", r"C:\Users\x\.claude\projects"
        )
        assert key is None

    def test_relpath_valueerror_is_caught(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Directly simulate the Windows cross-drive ``ValueError`` so the
        guard is exercised on every platform."""

        def boom(a: str, b: str) -> str:
            raise ValueError("path is on mount 'D:', start on mount 'C:'")

        monkeypatch.setattr(os.path, "relpath", boom)
        assert file_path_to_session_key("D:\\x\\y.jsonl", "C:\\projects") is None

    def test_unrecognized_shape_returns_none(self, tmp_path: Path) -> None:
        # Three parts but not a subagent layout (>=4) and not a main (==2).
        projects_dir = str(tmp_path)
        file_path = str(tmp_path / "proj" / SESSION_ID / "stray.jsonl")
        assert file_path_to_session_key(file_path, projects_dir) is None


# ---------------------------------------------------------------------------
# _is_safe_subpath — traversal rejection regardless of separator
# ---------------------------------------------------------------------------


class TestIsSafeSubpath:
    @pytest.fixture
    def session_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "session"
        d.mkdir()
        return d

    def test_plain_forward_slash_subpath_allowed(self, session_dir: Path) -> None:
        assert _is_safe_subpath("subagents/agent-1", session_dir) is True

    def test_backslash_dotdot_traversal_rejected(self, session_dir: Path) -> None:
        """Backslash-separated ``..`` must be rejected on every OS — the
        store may have been written by a Windows host."""
        assert _is_safe_subpath("subagents\\..\\..\\escape", session_dir) is False

    def test_forward_slash_dotdot_traversal_rejected(self, session_dir: Path) -> None:
        assert _is_safe_subpath("subagents/../../escape", session_dir) is False

    def test_mixed_separator_dotdot_rejected(self, session_dir: Path) -> None:
        assert _is_safe_subpath("subagents/..\\escape", session_dir) is False

    def test_unc_path_rejected(self, session_dir: Path) -> None:
        r"""``\\server\share`` is absolute on Windows and a traversal vector
        if naively joined — rejected everywhere via the leading-backslash
        check."""
        assert _is_safe_subpath("\\\\server\\share", session_dir) is False

    def test_leading_backslash_rejected(self, session_dir: Path) -> None:
        assert _is_safe_subpath("\\abs", session_dir) is False

    def test_leading_forward_slash_rejected(self, session_dir: Path) -> None:
        assert _is_safe_subpath("/etc/passwd", session_dir) is False

    def test_empty_rejected(self, session_dir: Path) -> None:
        assert _is_safe_subpath("", session_dir) is False

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="C:\\abs is only an absolute path on Windows",
    )
    def test_drive_letter_absolute_rejected_on_windows(self, session_dir: Path) -> None:
        assert _is_safe_subpath("C:\\Windows\\System32", session_dir) is False

    def test_drive_relative_rejected_everywhere(self, session_dir: Path) -> None:
        """``C:foo`` is drive-relative on Windows (``is_absolute()`` is False)
        and a literal filename on POSIX. Either way it's never a legitimate
        store subkey, so reject it on every platform — a Windows consumer
        must be protected even if the store was populated on POSIX."""
        assert _is_safe_subpath("C:escape", session_dir) is False

    def test_drive_absolute_rejected_everywhere(self, session_dir: Path) -> None:
        r"""``C:\abs`` is rejected on every platform via the explicit
        ``ntpath.splitdrive`` guard — defense in depth for cross-platform
        store consumers."""
        assert _is_safe_subpath("C:\\abs", session_dir) is False


# ---------------------------------------------------------------------------
# project_key_for_directory / _sanitize_path — path-string sanitization
# ---------------------------------------------------------------------------


class TestProjectKeySanitization:
    """``_sanitize_path`` is a pure string transform, so feeding it
    Windows/UNC/Unicode strings is valid on any host."""

    def test_windows_drive_path_sanitized(self) -> None:
        key = _sanitize_path(r"C:\Users\alice\project")
        # ':' and '\' are non-alphanumeric → '-'
        assert key == "C--Users-alice-project"

    def test_unc_path_sanitized(self) -> None:
        key = _sanitize_path(r"\\server\share\repo")
        assert key == "--server-share-repo"

    def test_mixed_separators_sanitized(self) -> None:
        key = _sanitize_path("C:/Users\\alice/project")
        assert key == "C--Users-alice-project"

    def test_posix_path_sanitized(self) -> None:
        assert _sanitize_path("/home/alice/project") == "-home-alice-project"

    def test_long_path_truncated_with_hash(self) -> None:
        long = "/" + ("a" * 250)
        key = _sanitize_path(long)
        assert len(key) <= 200 + 1 + 8  # prefix + '-' + base36 hash
        assert key.startswith("-" + "a" * 199)
        # Hash suffix is deterministic — same input → same key.
        assert key == _sanitize_path(long)
        assert _sanitize_path("/" + ("b" * 250)) != key

    def test_unicode_nfc_vs_nfd_produce_different_keys(self) -> None:
        """macOS HFS+ stores NFD; user input is typically NFC. The sanitizer
        sees them as distinct byte sequences. ``_canonicalize_path`` (NFC
        normalization) is the layer responsible for reconciling them — this
        test pins the sanitizer's raw behavior so a silent change is caught."""
        nfc = unicodedata.normalize("NFC", "/tmp/café")  # é = U+00E9
        nfd = unicodedata.normalize("NFD", "/tmp/café")  # e + U+0301
        assert nfc != nfd
        key_nfc = _sanitize_path(nfc)
        key_nfd = _sanitize_path(nfd)
        # NFC: 'é' (1 char) → '-'. NFD: 'e' kept, combining accent → '-'.
        assert key_nfc == "-tmp-caf-"
        assert key_nfd == "-tmp-cafe-"
        assert key_nfc != key_nfd

    def test_project_key_for_directory_uses_resolved_absolute_path(
        self, tmp_path: Path
    ) -> None:
        key = project_key_for_directory(tmp_path)
        # Result must be filesystem-safe (alnum + '-') on every platform.
        assert all(c.isalnum() or c == "-" for c in key)
        # Stable across calls.
        assert key == project_key_for_directory(tmp_path)
        assert key == project_key_for_directory(str(tmp_path))


# ---------------------------------------------------------------------------
# macOS Keychain credential lookup
# ---------------------------------------------------------------------------


class TestKeychainCredentials:
    def test_non_darwin_short_circuits_without_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        called = Mock()
        monkeypatch.setattr(subprocess, "run", called)
        assert _read_keychain_credentials() is None
        called.assert_not_called()

    def test_windows_short_circuits_without_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        called = Mock()
        monkeypatch.setattr(subprocess, "run", called)
        assert _read_keychain_credentials() is None
        called.assert_not_called()

    def test_darwin_success_returns_stdout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setenv("USER", "alice")
        creds = json.dumps({"claudeAiOauth": {"accessToken": "tok"}})
        recorded: dict[str, Any] = {}

        def fake_run(argv: list[str], **kw: Any) -> Any:
            recorded["argv"] = argv
            return Mock(returncode=0, stdout=creds + "\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        out = _read_keychain_credentials()
        assert out == creds
        assert recorded["argv"][0] == "security"
        assert "find-generic-password" in recorded["argv"]
        assert "alice" in recorded["argv"]

    def test_darwin_nonzero_exit_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: Mock(returncode=44, stdout=""),
        )
        assert _read_keychain_credentials() is None

    def test_darwin_subprocess_exception_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")

        def boom(*a: Any, **k: Any) -> Any:
            raise FileNotFoundError("security: command not found")

        monkeypatch.setattr(subprocess, "run", boom)
        assert _read_keychain_credentials() is None

    def test_darwin_empty_stdout_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: Mock(returncode=0, stdout="   \n")
        )
        assert _read_keychain_credentials() is None


# ---------------------------------------------------------------------------
# _copy_auth_files — config-dir resolution and Keychain integration
# ---------------------------------------------------------------------------


class TestCopyAuthFiles:
    def test_home_fallback_finds_credentials_and_claude_json(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no ``CLAUDE_CONFIG_DIR``, ``.credentials.json`` comes from
        ``~/.claude/`` and ``.claude.json`` from ``~`` — verified via a
        mocked ``Path.home()`` so it holds on every OS."""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        (isolated_home / ".claude").mkdir()
        (isolated_home / ".claude" / ".credentials.json").write_text(
            '{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"}}'
        )
        (isolated_home / ".claude.json").write_text('{"hasSeenWelcome":true}')

        dest = tmp_path / "dest"
        dest.mkdir()
        _copy_auth_files(dest, opt_env={})

        written = json.loads((dest / ".credentials.json").read_text())
        assert written["claudeAiOauth"]["accessToken"] == "a"
        assert "refreshToken" not in written["claudeAiOauth"]
        assert (dest / ".claude.json").read_text() == '{"hasSeenWelcome":true}'

    def test_options_env_beats_process_env(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        proc_dir = tmp_path / "from_process_env"
        proc_dir.mkdir()
        (proc_dir / ".credentials.json").write_text('{"src":"process"}')
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(proc_dir))

        opt_dir = tmp_path / "from_options_env"
        opt_dir.mkdir()
        (opt_dir / ".credentials.json").write_text('{"src":"options"}')

        dest = tmp_path / "dest"
        dest.mkdir()
        _copy_auth_files(dest, opt_env={"CLAUDE_CONFIG_DIR": str(opt_dir)})
        assert json.loads((dest / ".credentials.json").read_text()) == {
            "src": "options"
        }

    def test_keychain_used_on_darwin_when_no_file_and_refresh_token_redacted(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """macOS default: no ``.credentials.json`` on disk, creds in Keychain.
        ``_copy_auth_files`` must pull from Keychain and strip the refresh
        token before writing."""
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        keychain_blob = json.dumps(
            {"claudeAiOauth": {"accessToken": "kc-tok", "refreshToken": "kc-refresh"}}
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: Mock(returncode=0, stdout=keychain_blob),
        )

        dest = tmp_path / "dest"
        dest.mkdir()
        _copy_auth_files(dest, opt_env={})

        written = json.loads((dest / ".credentials.json").read_text())
        assert written["claudeAiOauth"]["accessToken"] == "kc-tok"
        assert "refreshToken" not in written["claudeAiOauth"]

    def test_keychain_miss_on_darwin_falls_back_gracefully(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: Mock(returncode=44, stdout="")
        )
        dest = tmp_path / "dest"
        dest.mkdir()
        # No file, no keychain → nothing written, no exception.
        _copy_auth_files(dest, opt_env={})
        assert not (dest / ".credentials.json").exists()

    def test_keychain_skipped_when_api_key_in_options_env(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        called = Mock()
        monkeypatch.setattr(subprocess, "run", called)
        dest = tmp_path / "dest"
        dest.mkdir()
        _copy_auth_files(dest, opt_env={"ANTHROPIC_API_KEY": "sk-ant-xxx"})
        called.assert_not_called()

    def test_keychain_skipped_when_custom_config_dir_set(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        called = Mock()
        monkeypatch.setattr(subprocess, "run", called)
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        _copy_auth_files(dest, opt_env={"CLAUDE_CONFIG_DIR": str(cfg)})
        called.assert_not_called()


# ---------------------------------------------------------------------------
# materialize_resume_session — temp-dir portability
# ---------------------------------------------------------------------------


class TestMaterializeTempDir:
    @pytest.mark.asyncio
    async def test_config_dir_is_under_system_tempdir_not_hardcoded(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``tempfile.mkdtemp`` location varies (``/tmp``, ``/var/folders``,
        ``%TEMP%``). The materialized dir must live under whatever
        ``tempfile.gettempdir()`` returns and be removed by ``cleanup``."""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        cwd = tmp_path / "proj"
        cwd.mkdir()
        store = InMemorySessionStore()
        pk = project_key_for_directory(cwd)
        await store.append(
            {"project_key": pk, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1", "message": {"content": "hi"}}],
        )
        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        result = await materialize_resume_session(opts)
        assert result is not None
        try:
            sys_tmp = Path(tempfile.gettempdir()).resolve()
            assert result.config_dir.resolve().is_relative_to(sys_tmp), (
                f"{result.config_dir} not under {sys_tmp}"
            )
            # Transcript path is built with Path / — readable on this OS.
            jsonl = result.config_dir / "projects" / pk / f"{SESSION_ID}.jsonl"
            assert jsonl.is_file()
        finally:
            await result.cleanup()
        assert not result.config_dir.exists()

    @pytest.mark.asyncio
    async def test_round_trip_subpath_key_across_separator(
        self, tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A subagent written via the native ``os.sep`` path must materialize
        and then re-derive to the same forward-slash subpath key."""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        cwd = tmp_path / "proj"
        cwd.mkdir()
        pk = project_key_for_directory(cwd)
        store = InMemorySessionStore()
        await store.append(
            {"project_key": pk, "session_id": SESSION_ID},
            [{"type": "user", "uuid": "u1", "message": {"content": "hi"}}],
        )
        await store.append(
            {
                "project_key": pk,
                "session_id": SESSION_ID,
                "subpath": "subagents/agent-abc",
            },
            [{"type": "user", "uuid": "s1", "message": {"content": "sub"}}],
        )
        opts = ClaudeAgentOptions(cwd=cwd, session_store=store, resume=SESSION_ID)
        result = await materialize_resume_session(opts)
        assert result is not None
        try:
            sub_file = (
                result.config_dir
                / "projects"
                / pk
                / SESSION_ID
                / "subagents"
                / "agent-abc.jsonl"
            )
            assert sub_file.is_file()
            # Re-derive the key from the on-disk path → forward-slash subpath.
            derived = file_path_to_session_key(
                str(sub_file), str(result.config_dir / "projects")
            )
            assert derived == {
                "project_key": pk,
                "session_id": SESSION_ID,
                "subpath": "subagents/agent-abc",
            }
        finally:
            await result.cleanup()
