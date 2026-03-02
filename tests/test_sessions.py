"""Tests for list_sessions()."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from claude_agent_sdk import (
    SDKSessionInfo,
    SessionMessage,
    get_session_messages,
    list_sessions,
)
from claude_agent_sdk._internal.sessions import (
    _build_conversation_chain,
    _extract_first_prompt_from_head,
    _extract_json_string_field,
    _extract_last_json_string_field,
    _sanitize_path,
    _simple_hash,
    _validate_uuid,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def claude_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Creates a temporary ~/.claude directory and points CLAUDE_CONFIG_DIR at it."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


def _make_session_file(
    project_dir: Path,
    session_id: str | None = None,
    *,
    first_prompt: str = "Hello Claude",
    summary: str | None = None,
    custom_title: str | None = None,
    git_branch: str | None = None,
    cwd: str | None = None,
    is_sidechain: bool = False,
    is_meta_only: bool = False,
    mtime: float | None = None,
) -> tuple[str, Path]:
    """Creates a .jsonl session file with the given metadata.

    Returns (session_id, file_path).
    """
    sid = session_id or str(uuid.uuid4())
    file_path = project_dir / f"{sid}.jsonl"

    lines: list[str] = []

    # First line: user message (or meta/sidechain)
    first_entry: dict = {
        "type": "user",
        "message": {"role": "user", "content": first_prompt},
    }
    if cwd is not None:
        first_entry["cwd"] = cwd
    if git_branch is not None:
        first_entry["gitBranch"] = git_branch
    if is_sidechain:
        first_entry["isSidechain"] = True
    if is_meta_only:
        first_entry["isMeta"] = True
    lines.append(json.dumps(first_entry))

    # Assistant response
    lines.append(
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": "Hi there!"},
            }
        )
    )

    # Tail metadata
    tail_entry: dict = {"type": "summary"}
    if summary is not None:
        tail_entry["summary"] = summary
    if custom_title is not None:
        tail_entry["customTitle"] = custom_title
    if git_branch is not None:
        tail_entry["gitBranch"] = git_branch
    lines.append(json.dumps(tail_entry))

    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if mtime is not None:
        os.utime(file_path, (mtime, mtime))

    return sid, file_path


def _make_project_dir(config_dir: Path, project_path: str) -> Path:
    """Creates a sanitized project directory for the given path."""
    sanitized = _sanitize_path(project_path)
    project_dir = config_dir / "projects" / sanitized
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for internal helper functions."""

    def test_validate_uuid_valid(self):
        assert _validate_uuid("550e8400-e29b-41d4-a716-446655440000")
        assert _validate_uuid("550E8400-E29B-41D4-A716-446655440000")

    def test_validate_uuid_invalid(self):
        assert _validate_uuid("not-a-uuid") is None
        assert _validate_uuid("") is None
        assert _validate_uuid("550e8400-e29b-41d4-a716") is None

    def test_sanitize_path_basic(self):
        assert _sanitize_path("/Users/foo/my-project") == "-Users-foo-my-project"
        assert _sanitize_path("plugin:name:server") == "plugin-name-server"

    def test_sanitize_path_long(self):
        """Long paths get truncated with a hash suffix."""
        long_path = "/x" * 150  # 300 chars
        result = _sanitize_path(long_path)
        assert len(result) > 200  # truncated + hash
        assert result.startswith("-x-x")
        # The hash suffix is appended after the 200-char prefix
        assert "-" in result[200:]

    def test_simple_hash_deterministic(self):
        assert _simple_hash("hello") == _simple_hash("hello")
        assert _simple_hash("hello") != _simple_hash("world")

    def test_simple_hash_zero(self):
        # Empty string should produce "0"
        assert _simple_hash("") == "0"

    def test_extract_json_string_field_simple(self):
        text = '{"foo":"bar","baz":"qux"}'
        assert _extract_json_string_field(text, "foo") == "bar"
        assert _extract_json_string_field(text, "baz") == "qux"
        assert _extract_json_string_field(text, "missing") is None

    def test_extract_json_string_field_with_space(self):
        text = '{"foo": "bar"}'
        assert _extract_json_string_field(text, "foo") == "bar"

    def test_extract_json_string_field_escaped(self):
        text = '{"foo":"bar\\"baz"}'
        result = _extract_json_string_field(text, "foo")
        assert result == 'bar"baz'

    def test_extract_last_json_string_field(self):
        text = '{"summary":"first"}\n{"summary":"second"}\n{"summary":"third"}'
        assert _extract_last_json_string_field(text, "summary") == "third"

    def test_extract_first_prompt_simple(self):
        head = json.dumps({"type": "user", "message": {"content": "Hello!"}}) + "\n"
        assert _extract_first_prompt_from_head(head) == "Hello!"

    def test_extract_first_prompt_skips_meta(self):
        head = (
            json.dumps({"type": "user", "isMeta": True, "message": {"content": "meta"}})
            + "\n"
            + json.dumps({"type": "user", "message": {"content": "real prompt"}})
            + "\n"
        )
        assert _extract_first_prompt_from_head(head) == "real prompt"

    def test_extract_first_prompt_skips_tool_result(self):
        head = (
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": "x"}]},
                }
            )
            + "\n"
            + json.dumps({"type": "user", "message": {"content": "actual prompt"}})
            + "\n"
        )
        assert _extract_first_prompt_from_head(head) == "actual prompt"

    def test_extract_first_prompt_content_blocks(self):
        head = (
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": [{"type": "text", "text": "block prompt"}]},
                }
            )
            + "\n"
        )
        assert _extract_first_prompt_from_head(head) == "block prompt"

    def test_extract_first_prompt_truncates(self):
        long_prompt = "x" * 300
        head = json.dumps({"type": "user", "message": {"content": long_prompt}}) + "\n"
        result = _extract_first_prompt_from_head(head)
        assert len(result) <= 201  # 200 chars + ellipsis
        assert result.endswith("\u2026")

    def test_extract_first_prompt_command_fallback(self):
        """If only slash-commands are found, use first command name."""
        head = (
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "<command-name>/help</command-name>stuff"},
                }
            )
            + "\n"
        )
        assert _extract_first_prompt_from_head(head) == "/help"

    def test_extract_first_prompt_empty(self):
        assert _extract_first_prompt_from_head("") == ""
        assert _extract_first_prompt_from_head('{"type":"assistant"}\n') == ""


# ---------------------------------------------------------------------------
# list_sessions() integration tests
# ---------------------------------------------------------------------------


class TestListSessions:
    """Tests for the list_sessions() function."""

    def test_empty_projects_dir(self, claude_config_dir: Path):
        """No sessions when projects dir is empty."""
        assert list_sessions() == []

    def test_no_config_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Gracefully handles missing config dir."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
        assert list_sessions() == []

    def test_single_session(self, claude_config_dir: Path, tmp_path: Path):
        """Single session with basic metadata."""
        project_path = str(tmp_path / "my-project")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(
            project_dir,
            first_prompt="What is 2+2?",
            git_branch="main",
            cwd=project_path,
        )

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        s = sessions[0]
        assert isinstance(s, SDKSessionInfo)
        assert s.session_id == sid
        assert s.first_prompt == "What is 2+2?"
        assert s.summary == "What is 2+2?"  # no custom title or summary → first prompt
        assert s.git_branch == "main"
        assert s.cwd == project_path
        assert s.file_size > 0
        assert s.last_modified > 0
        assert s.custom_title is None

    def test_custom_title_wins_summary(self, claude_config_dir: Path, tmp_path: Path):
        """custom_title takes precedence over summary and first_prompt."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        _make_session_file(
            project_dir,
            first_prompt="original question",
            summary="auto summary",
            custom_title="My Custom Title",
        )

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].summary == "My Custom Title"
        assert sessions[0].custom_title == "My Custom Title"
        assert sessions[0].first_prompt == "original question"

    def test_summary_wins_first_prompt(self, claude_config_dir: Path, tmp_path: Path):
        """Explicit summary takes precedence over first_prompt when no custom_title."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        _make_session_file(
            project_dir, first_prompt="question", summary="better summary"
        )

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].summary == "better summary"
        assert sessions[0].custom_title is None

    def test_multiple_sessions_sorted_by_mtime(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Sessions are sorted by last_modified descending."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        sid_old, _ = _make_session_file(project_dir, first_prompt="old", mtime=1000.0)
        sid_new, _ = _make_session_file(project_dir, first_prompt="new", mtime=3000.0)
        sid_mid, _ = _make_session_file(project_dir, first_prompt="mid", mtime=2000.0)

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 3
        assert [s.session_id for s in sessions] == [sid_new, sid_mid, sid_old]
        # Verify mtime conversion to milliseconds
        assert sessions[0].last_modified == 3_000_000
        assert sessions[1].last_modified == 2_000_000
        assert sessions[2].last_modified == 1_000_000

    def test_limit(self, claude_config_dir: Path, tmp_path: Path):
        """Limit option restricts number of results."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        for i in range(5):
            _make_session_file(
                project_dir, first_prompt=f"prompt {i}", mtime=1000.0 + i
            )

        sessions = list_sessions(
            directory=project_path, limit=2, include_worktrees=False
        )
        assert len(sessions) == 2
        # Should be the 2 newest
        assert sessions[0].last_modified >= sessions[1].last_modified

    def test_filters_sidechain_sessions(self, claude_config_dir: Path, tmp_path: Path):
        """Sessions with isSidechain:true are filtered out."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        _make_session_file(project_dir, first_prompt="normal")
        _make_session_file(project_dir, first_prompt="sidechain", is_sidechain=True)

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "normal"

    def test_filters_empty_sessions(self, claude_config_dir: Path, tmp_path: Path):
        """Sessions with no summary/title/prompt are filtered (no '(session)' placeholder)."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        # A session with only meta messages → no first_prompt, no summary
        _make_session_file(project_dir, first_prompt="ignored meta", is_meta_only=True)
        _make_session_file(project_dir, first_prompt="real content")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "real content"

    def test_filters_non_uuid_filenames(self, claude_config_dir: Path, tmp_path: Path):
        """Non-UUID .jsonl files are ignored."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        # Create a non-UUID .jsonl file
        (project_dir / "not-a-uuid.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "x"}}) + "\n"
        )
        _make_session_file(project_dir, first_prompt="valid session")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "valid session"

    def test_ignores_non_jsonl_files(self, claude_config_dir: Path, tmp_path: Path):
        """Files not ending in .jsonl are ignored."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        (project_dir / "README.md").write_text("not a session")
        _make_session_file(project_dir, first_prompt="session")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1

    def test_list_all_sessions(self, claude_config_dir: Path):
        """When no directory is given, lists across all projects."""
        proj1 = _make_project_dir(claude_config_dir, "/some/path/one")
        proj2 = _make_project_dir(claude_config_dir, "/some/path/two")

        _make_session_file(proj1, first_prompt="from proj1", mtime=1000.0)
        _make_session_file(proj2, first_prompt="from proj2", mtime=2000.0)

        sessions = list_sessions()
        assert len(sessions) == 2
        # Sorted newest first
        assert sessions[0].first_prompt == "from proj2"
        assert sessions[1].first_prompt == "from proj1"

    def test_list_all_sessions_dedupes(self, claude_config_dir: Path):
        """Duplicate session IDs across projects keep the newest."""
        proj1 = _make_project_dir(claude_config_dir, "/path/one")
        proj2 = _make_project_dir(claude_config_dir, "/path/two")

        shared_sid = str(uuid.uuid4())
        _make_session_file(
            proj1, session_id=shared_sid, first_prompt="older", mtime=1000.0
        )
        _make_session_file(
            proj2, session_id=shared_sid, first_prompt="newer", mtime=2000.0
        )

        sessions = list_sessions()
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "newer"
        assert sessions[0].last_modified == 2_000_000

    def test_nonexistent_project_dir(self, claude_config_dir: Path, tmp_path: Path):
        """Returns empty list when project has no session directory."""
        project_path = str(tmp_path / "never-used")
        Path(project_path).mkdir(parents=True)
        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert sessions == []

    def test_empty_file_filtered(self, claude_config_dir: Path, tmp_path: Path):
        """Empty session files are filtered out."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        sid = str(uuid.uuid4())
        (project_dir / f"{sid}.jsonl").write_text("")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert sessions == []

    def test_include_worktrees_disabled(self, claude_config_dir: Path, tmp_path: Path):
        """include_worktrees=False only scans the given directory."""
        # Create a real directory so realpath works
        project_path = str(tmp_path / "main-proj")
        Path(project_path).mkdir(parents=True)
        canonical = os.path.realpath(project_path)

        main_dir = _make_project_dir(claude_config_dir, canonical)
        _make_session_file(main_dir, first_prompt="main session")

        # Create another "worktree-like" project dir that should NOT be scanned
        other_dir = _make_project_dir(claude_config_dir, canonical + "-worktree")
        _make_session_file(other_dir, first_prompt="worktree session")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "main session"

    def test_limit_zero_returns_all(self, claude_config_dir: Path, tmp_path: Path):
        """limit=0 or negative returns all sessions (TS: limit > 0 check)."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        for i in range(3):
            _make_session_file(project_dir, first_prompt=f"p{i}")

        sessions = list_sessions(
            directory=project_path, limit=0, include_worktrees=False
        )
        assert len(sessions) == 3

    def test_cwd_from_head_fallback_to_project_path(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """cwd falls back to project path when not in head."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        canonical = os.path.realpath(project_path)
        project_dir = _make_project_dir(claude_config_dir, canonical)

        # Session without cwd field
        _make_session_file(project_dir, first_prompt="no cwd field")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].cwd == canonical

    def test_git_branch_from_tail_preferred(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """gitBranch from tail is preferred over head."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"

        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "hello"},
                    "gitBranch": "old-branch",
                }
            ),
            json.dumps({"type": "summary", "gitBranch": "new-branch"}),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].git_branch == "new-branch"


class TestSDKSessionInfoType:
    """Tests for the SDKSessionInfo dataclass."""

    def test_creation_required_fields(self):
        info = SDKSessionInfo(
            session_id="abc",
            summary="test",
            last_modified=1000,
            file_size=42,
        )
        assert info.session_id == "abc"
        assert info.summary == "test"
        assert info.last_modified == 1000
        assert info.file_size == 42
        assert info.custom_title is None
        assert info.first_prompt is None
        assert info.git_branch is None
        assert info.cwd is None

    def test_creation_all_fields(self):
        info = SDKSessionInfo(
            session_id="abc",
            summary="test",
            last_modified=1000,
            file_size=42,
            custom_title="title",
            first_prompt="prompt",
            git_branch="main",
            cwd="/foo",
        )
        assert info.custom_title == "title"
        assert info.first_prompt == "prompt"
        assert info.git_branch == "main"
        assert info.cwd == "/foo"


# ---------------------------------------------------------------------------
# get_session_messages() helpers
# ---------------------------------------------------------------------------


def _make_transcript_entry(
    entry_type: str,
    entry_uuid: str,
    parent_uuid: str | None,
    session_id: str,
    content: str | list | None = None,
    **extras,
) -> dict:
    """Builds a transcript entry dict matching the CLI's JSONL format."""
    entry: dict = {
        "type": entry_type,
        "uuid": entry_uuid,
        "parentUuid": parent_uuid,
        "sessionId": session_id,
    }
    if content is not None:
        role = entry_type if entry_type in ("user", "assistant") else "user"
        entry["message"] = {"role": role, "content": content}
    entry.update(extras)
    return entry


def _write_transcript(project_dir: Path, session_id: str, entries: list[dict]) -> Path:
    """Writes a JSONL transcript file."""
    file_path = project_dir / f"{session_id}.jsonl"
    lines = [json.dumps(e) for e in entries]
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# get_session_messages() tests
# ---------------------------------------------------------------------------


class TestGetSessionMessages:
    """Tests for get_session_messages()."""

    def test_invalid_session_id(self, claude_config_dir: Path):
        """Non-UUID session_id returns empty list."""
        assert get_session_messages("not-a-uuid") == []
        assert get_session_messages("") == []

    def test_nonexistent_session(self, claude_config_dir: Path):
        """Session file not found returns empty list."""
        sid = str(uuid.uuid4())
        assert get_session_messages(sid) == []

    def test_no_config_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Missing config dir returns empty list."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
        sid = str(uuid.uuid4())
        assert get_session_messages(sid) == []

    def test_simple_chain(self, claude_config_dir: Path, tmp_path: Path):
        """Basic user → assistant → user → assistant chain."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        a2 = str(uuid.uuid4())

        entries = [
            _make_transcript_entry("user", u1, None, sid, content="hello"),
            _make_transcript_entry("assistant", a1, u1, sid, content="hi!"),
            _make_transcript_entry("user", u2, a1, sid, content="thanks"),
            _make_transcript_entry("assistant", a2, u2, sid, content="welcome"),
        ]
        _write_transcript(project_dir, sid, entries)

        messages = get_session_messages(sid, directory=project_path)
        assert len(messages) == 4

        # Chronological order: root → leaf
        assert messages[0].type == "user"
        assert messages[0].uuid == u1
        assert messages[0].session_id == sid
        assert messages[0].message == {"role": "user", "content": "hello"}
        assert messages[0].parent_tool_use_id is None

        assert messages[1].type == "assistant"
        assert messages[1].uuid == a1
        assert messages[1].message == {"role": "assistant", "content": "hi!"}

        assert messages[2].type == "user"
        assert messages[2].uuid == u2

        assert messages[3].type == "assistant"
        assert messages[3].uuid == a2

        # All SessionMessage instances
        assert all(isinstance(m, SessionMessage) for m in messages)

    def test_filters_meta_messages(self, claude_config_dir: Path, tmp_path: Path):
        """isMeta entries in the chain are filtered from output."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        meta = str(uuid.uuid4())
        a1 = str(uuid.uuid4())

        entries = [
            _make_transcript_entry("user", u1, None, sid, content="hello"),
            # Meta user message in the chain — should be walked through but
            # filtered from output
            _make_transcript_entry("user", meta, u1, sid, content="meta", isMeta=True),
            _make_transcript_entry("assistant", a1, meta, sid, content="hi"),
        ]
        _write_transcript(project_dir, sid, entries)

        messages = get_session_messages(sid, directory=project_path)
        # Only u1 and a1 visible (meta filtered out)
        assert len(messages) == 2
        assert messages[0].uuid == u1
        assert messages[1].uuid == a1

    def test_filters_non_user_assistant_from_chain(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Progress/system entries in chain are filtered from output."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        prog = str(uuid.uuid4())
        a1 = str(uuid.uuid4())

        entries = [
            _make_transcript_entry("user", u1, None, sid, content="hello"),
            # Progress entry in the chain
            _make_transcript_entry("progress", prog, u1, sid),
            _make_transcript_entry("assistant", a1, prog, sid, content="hi"),
        ]
        _write_transcript(project_dir, sid, entries)

        messages = get_session_messages(sid, directory=project_path)
        # progress is walked through the chain but filtered from output
        assert len(messages) == 2
        assert messages[0].uuid == u1
        assert messages[1].uuid == a1

    def test_keeps_compact_summary(self, claude_config_dir: Path, tmp_path: Path):
        """isCompactSummary messages are kept (they represent compacted content)."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())

        entries = [
            _make_transcript_entry(
                "user",
                u1,
                None,
                sid,
                content="compact summary",
                isCompactSummary=True,
            ),
            _make_transcript_entry("assistant", a1, u1, sid, content="hi"),
        ]
        _write_transcript(project_dir, sid, entries)

        messages = get_session_messages(sid, directory=project_path)
        assert len(messages) == 2
        assert messages[0].uuid == u1  # compact summary kept

    def test_limit_and_offset(self, claude_config_dir: Path, tmp_path: Path):
        """Limit and offset pagination."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        # Build a chain of 6 messages: u→a→u→a→u→a
        uuids = [str(uuid.uuid4()) for _ in range(6)]
        entries = []
        for i, uid in enumerate(uuids):
            parent = uuids[i - 1] if i > 0 else None
            entry_type = "user" if i % 2 == 0 else "assistant"
            entries.append(
                _make_transcript_entry(entry_type, uid, parent, sid, content=f"m{i}")
            )
        _write_transcript(project_dir, sid, entries)

        # No limit/offset
        all_msgs = get_session_messages(sid, directory=project_path)
        assert len(all_msgs) == 6

        # limit=2
        page = get_session_messages(sid, directory=project_path, limit=2)
        assert len(page) == 2
        assert page[0].uuid == uuids[0]
        assert page[1].uuid == uuids[1]

        # offset=2, limit=2
        page = get_session_messages(sid, directory=project_path, limit=2, offset=2)
        assert len(page) == 2
        assert page[0].uuid == uuids[2]
        assert page[1].uuid == uuids[3]

        # offset only (no limit)
        page = get_session_messages(sid, directory=project_path, offset=4)
        assert len(page) == 2
        assert page[0].uuid == uuids[4]
        assert page[1].uuid == uuids[5]

        # limit=0 returns all (TS: limit > 0 check)
        page = get_session_messages(sid, directory=project_path, limit=0)
        assert len(page) == 6

        # offset beyond end
        page = get_session_messages(sid, directory=project_path, offset=100)
        assert page == []

    def test_picks_main_chain_over_sidechain(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """When multiple leaves exist, prefers non-sidechain main leaf."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        root = str(uuid.uuid4())
        main_leaf = str(uuid.uuid4())
        side_leaf = str(uuid.uuid4())

        entries = [
            _make_transcript_entry("user", root, None, sid, content="root"),
            # Main chain continuation
            _make_transcript_entry("assistant", main_leaf, root, sid, content="main"),
            # Sidechain branch (also from root) — should be ignored as leaf
            _make_transcript_entry(
                "assistant",
                side_leaf,
                root,
                sid,
                content="side",
                isSidechain=True,
            ),
        ]
        _write_transcript(project_dir, sid, entries)

        messages = get_session_messages(sid, directory=project_path)
        assert len(messages) == 2
        assert messages[0].uuid == root
        assert messages[1].uuid == main_leaf  # main leaf chosen, not sidechain

    def test_picks_latest_leaf_by_file_position(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """When multiple main leaves exist, picks the one latest in the file."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        root = str(uuid.uuid4())
        old_leaf = str(uuid.uuid4())
        new_leaf = str(uuid.uuid4())

        # Both leaves branch from root; new_leaf appears later in file
        entries = [
            _make_transcript_entry("user", root, None, sid, content="root"),
            _make_transcript_entry("assistant", old_leaf, root, sid, content="old"),
            _make_transcript_entry("assistant", new_leaf, root, sid, content="new"),
        ]
        _write_transcript(project_dir, sid, entries)

        messages = get_session_messages(sid, directory=project_path)
        assert len(messages) == 2
        assert messages[0].uuid == root
        # new_leaf has higher file position → chosen
        assert messages[1].uuid == new_leaf

    def test_terminal_non_message_walked_back(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """A terminal progress entry is walked back to find user/assistant leaf."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())
        prog = str(uuid.uuid4())  # terminal progress entry

        entries = [
            _make_transcript_entry("user", u1, None, sid, content="hi"),
            _make_transcript_entry("assistant", a1, u1, sid, content="hello"),
            # Terminal entry is progress type — should walk back to a1
            _make_transcript_entry("progress", prog, a1, sid),
        ]
        _write_transcript(project_dir, sid, entries)

        messages = get_session_messages(sid, directory=project_path)
        assert len(messages) == 2
        assert messages[0].uuid == u1
        assert messages[1].uuid == a1

    def test_corrupt_lines_skipped(self, claude_config_dir: Path, tmp_path: Path):
        """Corrupt JSON lines are skipped without failing."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())

        lines = [
            json.dumps(_make_transcript_entry("user", u1, None, sid, content="hi")),
            "not valid json {{{",
            "",
            json.dumps(
                _make_transcript_entry("assistant", a1, u1, sid, content="hello")
            ),
        ]
        (project_dir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")

        messages = get_session_messages(sid, directory=project_path)
        assert len(messages) == 2

    def test_search_all_projects_when_no_dir(self, claude_config_dir: Path):
        """When no directory given, searches all project directories."""
        proj1 = _make_project_dir(claude_config_dir, "/path/one")
        proj2 = _make_project_dir(claude_config_dir, "/path/two")

        sid = str(uuid.uuid4())
        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())

        # Session lives only in proj2
        entries = [
            _make_transcript_entry("user", u1, None, sid, content="hi"),
            _make_transcript_entry("assistant", a1, u1, sid, content="hello"),
        ]
        _write_transcript(proj2, sid, entries)

        # proj1 exists but doesn't have this session
        _ = proj1  # noqa: F841

        messages = get_session_messages(sid)  # no directory
        assert len(messages) == 2
        assert messages[0].uuid == u1

    def test_cycle_detection(self, claude_config_dir: Path, tmp_path: Path):
        """Cyclic parentUuid references don't cause infinite loop."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())

        # a1 → u1 → a1 (cycle!)
        entries = [
            _make_transcript_entry("user", u1, a1, sid, content="hi"),
            _make_transcript_entry("assistant", a1, u1, sid, content="hello"),
        ]
        _write_transcript(project_dir, sid, entries)

        # Should terminate without hanging. Both entries are parents of
        # each other → no terminals → empty chain.
        messages = get_session_messages(sid, directory=project_path)
        # No terminals found (both are parents) → returns empty
        assert messages == []

    def test_empty_transcript_file(self, claude_config_dir: Path, tmp_path: Path):
        """Empty file returns empty list."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        (project_dir / f"{sid}.jsonl").write_text("")
        assert get_session_messages(sid, directory=project_path) == []

    def test_ignores_non_transcript_types(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Lines with type=summary (no uuid/chain) are ignored during parsing."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())

        lines = [
            json.dumps(_make_transcript_entry("user", u1, None, sid, content="hi")),
            json.dumps({"type": "summary", "summary": "A nice chat"}),
            json.dumps(
                _make_transcript_entry("assistant", a1, u1, sid, content="hello")
            ),
        ]
        (project_dir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")

        messages = get_session_messages(sid, directory=project_path)
        assert len(messages) == 2


class TestBuildConversationChain:
    """Unit tests for the _build_conversation_chain helper."""

    def test_empty_input(self):
        assert _build_conversation_chain([]) == []

    def test_single_entry(self):
        entry = {"type": "user", "uuid": "a", "parentUuid": None}
        result = _build_conversation_chain([entry])
        assert result == [entry]

    def test_linear_chain(self):
        entries = [
            {"type": "user", "uuid": "a", "parentUuid": None},
            {"type": "assistant", "uuid": "b", "parentUuid": "a"},
            {"type": "user", "uuid": "c", "parentUuid": "b"},
        ]
        result = _build_conversation_chain(entries)
        assert [e["uuid"] for e in result] == ["a", "b", "c"]

    def test_only_progress_entries_returns_empty(self):
        """If no user/assistant entries, no leaves found → empty."""
        entries = [
            {"type": "progress", "uuid": "a", "parentUuid": None},
            {"type": "progress", "uuid": "b", "parentUuid": "a"},
        ]
        result = _build_conversation_chain(entries)
        assert result == []


class TestSessionMessageType:
    """Tests for the SessionMessage dataclass."""

    def test_creation(self):
        msg = SessionMessage(
            type="user",
            uuid="abc",
            session_id="sess",
            message={"role": "user", "content": "hi"},
        )
        assert msg.type == "user"
        assert msg.uuid == "abc"
        assert msg.session_id == "sess"
        assert msg.message == {"role": "user", "content": "hi"}
        assert msg.parent_tool_use_id is None
