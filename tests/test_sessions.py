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
    get_session_info,
    get_session_messages,
    get_subagent_messages,
    list_sessions,
    list_subagents,
)
from claude_agent_sdk._internal.sessions import (
    _build_conversation_chain,
    _extract_first_prompt_from_head,
    _extract_json_string_field,
    _extract_last_json_string_field,
    _parse_session_info_from_lite,
    _read_session_lite,
    _sanitize_path,
    _simple_hash,
    _validate_uuid,
)

# Matches the CLI's on-disk JSONL format (JSON.stringify / json.dumps with
# separators). Tag extraction scopes to '{"type":"tag"' (no space after colon)
# at column 0 to avoid matching tool_use inputs — fixtures must use this form.
_COMPACT = {"separators": (",", ":")}

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

    def test_offset_pagination(self, claude_config_dir: Path, tmp_path: Path):
        """Offset skips sessions for pagination."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )

        for i in range(5):
            _make_session_file(
                project_dir, first_prompt=f"prompt {i}", mtime=1000.0 + i
            )

        # Get page 1 (first 2)
        page1 = list_sessions(
            directory=project_path, limit=2, offset=0, include_worktrees=False
        )
        assert len(page1) == 2

        # Get page 2 (next 2)
        page2 = list_sessions(
            directory=project_path, limit=2, offset=2, include_worktrees=False
        )
        assert len(page2) == 2

        # Pages should have different sessions
        page1_ids = {s.session_id for s in page1}
        page2_ids = {s.session_id for s in page2}
        assert page1_ids.isdisjoint(page2_ids)

        # Page 1 should be newer than page 2
        assert page1[0].last_modified > page2[0].last_modified

        # Offset beyond available returns empty
        page_empty = list_sessions(
            directory=project_path, offset=100, include_worktrees=False
        )
        assert len(page_empty) == 0

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


# ---------------------------------------------------------------------------
# Tag extraction tests (Branch A additions)
# ---------------------------------------------------------------------------


class TestTagExtraction:
    """Tests for tag field extraction in SDKSessionInfo."""

    def test_tag_extracted_from_tail(self, claude_config_dir: Path, tmp_path: Path):
        """Tag is extracted from the last {type:'tag'} entry in the tail."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "hello"}}),
            json.dumps({"type": "tag", "tag": "my-tag", "sessionId": sid}, **_COMPACT),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].tag == "my-tag"

    def test_tag_last_wins(self, claude_config_dir: Path, tmp_path: Path):
        """When multiple tag entries exist, the last one wins."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "hello"}}),
            json.dumps(
                {"type": "tag", "tag": "first-tag", "sessionId": sid}, **_COMPACT
            ),
            json.dumps(
                {"type": "tag", "tag": "second-tag", "sessionId": sid}, **_COMPACT
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].tag == "second-tag"

    def test_tag_empty_string_is_none(self, claude_config_dir: Path, tmp_path: Path):
        """Empty-string tag (clear marker) resolves to None via 'or None'."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "hello"}}),
            json.dumps({"type": "tag", "tag": "old-tag", "sessionId": sid}, **_COMPACT),
            json.dumps({"type": "tag", "tag": "", "sessionId": sid}, **_COMPACT),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].tag is None

    def test_tag_absent(self, claude_config_dir: Path, tmp_path: Path):
        """Sessions without a tag entry have tag=None."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        _make_session_file(project_dir, first_prompt="hello")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].tag is None

    def test_tag_ignores_tool_use_inputs(self, claude_config_dir: Path, tmp_path: Path):
        """Tag extraction is scoped to {type:'tag'} lines — ignores "tag" fields
        in tool_use inputs (git tag, Docker tags, cloud resource tags).

        Mirrors TS listSessionsImpl.ts:132 / sessionStorage.ts:629.
        """
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "tag this v1.0"}}),
            json.dumps(
                {"type": "tag", "tag": "real-tag", "sessionId": sid}, **_COMPACT
            ),
            # A tool_use entry with a "tag" key in its input — must NOT match.
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "mcp__docker__build",
                                "input": {"tag": "myapp:v2", "context": "."},
                            }
                        ],
                    },
                }
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].tag == "real-tag"  # NOT "myapp:v2"

    def test_tag_none_when_only_tool_use_tag(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Session with no {type:'tag'} entry but tool_use input has tag — returns None."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "build docker"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "input": {"tag": "prod"},
                            }
                        ],
                    },
                }
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].tag is None  # NOT "prod"

    def test_parse_session_info_from_lite_helper(self, tmp_path: Path):
        """Direct test of the refactored _parse_session_info_from_lite helper."""
        sid = str(uuid.uuid4())
        file_path = tmp_path / f"{sid}.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "test prompt"},
                    "cwd": "/workspace",
                }
            ),
            json.dumps(
                {"type": "tag", "tag": "experiment", "sessionId": sid}, **_COMPACT
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        lite = _read_session_lite(file_path)
        assert lite is not None
        info = _parse_session_info_from_lite(sid, lite, "/fallback")
        assert info is not None
        assert info.session_id == sid
        assert info.summary == "test prompt"
        assert info.tag == "experiment"
        assert info.cwd == "/workspace"  # head cwd wins over fallback


class TestCreatedAtExtraction:
    """Tests for created_at field extraction from first entry timestamp."""

    def test_created_at_from_iso_timestamp(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """created_at is parsed from ISO timestamp in first entry (epoch ms)."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        # 2026-01-15T10:30:00.000Z → epoch 1768473000000 ms
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "hello"},
                    "timestamp": "2026-01-15T10:30:00.000Z",
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": "hi"},
                    "timestamp": "2026-01-15T10:35:00.000Z",
                }
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        # 2026-01-15T10:30:00Z = 1768473000 seconds = 1768473000000 ms
        assert sessions[0].created_at == 1768473000000
        assert isinstance(sessions[0].created_at, int)

    def test_created_at_leq_last_modified(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """created_at <= last_modified (creation precedes mtime)."""
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
                    "timestamp": "2026-01-01T00:00:00.000Z",
                }
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")
        # Set mtime to Feb 2026 (well after the Jan timestamp)
        os.utime(file_path, (1769904000, 1769904000))  # 2026-02-01 UTC

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].created_at is not None
        assert sessions[0].created_at <= sessions[0].last_modified

    def test_created_at_none_when_missing(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """created_at is None when first entry lacks a timestamp field."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        # _make_session_file doesn't add a timestamp field
        _make_session_file(project_dir, first_prompt="no timestamp")

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].created_at is None

    def test_created_at_none_on_invalid_format(self, tmp_path: Path):
        """Invalid ISO string results in created_at=None (no exception)."""
        sid = str(uuid.uuid4())
        file_path = tmp_path / f"{sid}.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "hello"},
                    "timestamp": "not-a-valid-iso-date",
                }
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        lite = _read_session_lite(file_path)
        assert lite is not None
        info = _parse_session_info_from_lite(sid, lite)
        assert info is not None
        assert info.created_at is None

    def test_created_at_without_z_suffix(self, tmp_path: Path):
        """ISO timestamp without Z suffix (with explicit offset) also works."""
        sid = str(uuid.uuid4())
        file_path = tmp_path / f"{sid}.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "hello"},
                    "timestamp": "2026-01-15T10:30:00+00:00",
                }
            ),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        lite = _read_session_lite(file_path)
        assert lite is not None
        info = _parse_session_info_from_lite(sid, lite)
        assert info is not None
        assert info.created_at == 1768473000000
        assert isinstance(info.created_at, int)

    def test_sdksessioninfo_created_at_default(self):
        """SDKSessionInfo has created_at defaulting to None."""
        info = SDKSessionInfo(
            session_id="abc",
            summary="test",
            last_modified=1000,
            file_size=42,
        )
        assert info.created_at is None


# ---------------------------------------------------------------------------
# get_session_info() tests
# ---------------------------------------------------------------------------


class TestGetSessionInfo:
    """Tests for the get_session_info() single-session lookup."""

    def test_invalid_session_id(self, claude_config_dir: Path):
        """Non-UUID session_id returns None."""
        assert get_session_info("not-a-uuid") is None
        assert get_session_info("") is None

    def test_nonexistent_session(self, claude_config_dir: Path):
        """Session file not found returns None."""
        sid = str(uuid.uuid4())
        assert get_session_info(sid) is None

    def test_no_config_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Missing config dir returns None."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
        sid = str(uuid.uuid4())
        assert get_session_info(sid) is None

    def test_found_with_directory(self, claude_config_dir: Path, tmp_path: Path):
        """Session found in a specific project directory."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(
            project_dir, first_prompt="hello", git_branch="main"
        )

        info = get_session_info(sid, directory=project_path)
        assert info is not None
        assert info.session_id == sid
        assert info.summary == "hello"
        assert info.git_branch == "main"

    def test_found_without_directory(self, claude_config_dir: Path):
        """Session found by searching all project directories."""
        project_dir = _make_project_dir(claude_config_dir, "/some/project")
        sid, _ = _make_session_file(project_dir, first_prompt="search all")

        info = get_session_info(sid)
        assert info is not None
        assert info.session_id == sid
        assert info.summary == "search all"

    def test_returns_none_for_sidechain(self, claude_config_dir: Path, tmp_path: Path):
        """Sidechain sessions return None (filtered by parse helper)."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(
            project_dir, first_prompt="sidechain", is_sidechain=True
        )

        assert get_session_info(sid, directory=project_path) is None

    def test_directory_not_containing_session(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Returns None when directory provided but session not in it."""
        project_a = str(tmp_path / "proj-a")
        project_b = str(tmp_path / "proj-b")
        Path(project_a).mkdir(parents=True)
        Path(project_b).mkdir(parents=True)
        dir_a = _make_project_dir(claude_config_dir, os.path.realpath(project_a))
        _make_project_dir(claude_config_dir, os.path.realpath(project_b))
        sid, _ = _make_session_file(dir_a, first_prompt="in A only")

        # Session exists in A but we look in B — should return None
        # (no worktree relationship between them)
        assert get_session_info(sid, directory=project_b) is None
        # But searching all projects finds it
        assert get_session_info(sid) is not None

    def test_includes_tag(self, claude_config_dir: Path, tmp_path: Path):
        """get_session_info includes the new tag field."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "hello"}}),
            json.dumps({"type": "tag", "tag": "urgent", "sessionId": sid}, **_COMPACT),
        ]
        file_path.write_text("\n".join(lines) + "\n")

        info = get_session_info(sid, directory=project_path)
        assert info is not None
        assert info.tag == "urgent"

    def test_sdksessioninfo_new_fields_defaults(self):
        """SDKSessionInfo has tag defaulting to None."""
        info = SDKSessionInfo(
            session_id="abc",
            summary="test",
            last_modified=1000,
            file_size=42,
        )
        assert info.tag is None


# ---------------------------------------------------------------------------
# list_subagents() / get_subagent_messages() helpers
# ---------------------------------------------------------------------------


def _make_session_with_subagents(
    config_dir: Path,
    project_path: str,
    agent_ids: list[str] | None = None,
) -> tuple[str, Path]:
    """Creates a session with a subagents directory.

    Returns (session_id, subagents_dir).
    """
    project_dir = _make_project_dir(config_dir, os.path.realpath(project_path))
    sid, _ = _make_session_file(project_dir)
    subagents_dir = project_dir / sid / "subagents"
    subagents_dir.mkdir(parents=True)
    for agent_id in agent_ids or []:
        (subagents_dir / f"agent-{agent_id}.jsonl").write_text(
            json.dumps({"type": "user", "uuid": "u", "parentUuid": None}) + "\n"
        )
    return sid, subagents_dir


# ---------------------------------------------------------------------------
# list_subagents() tests
# ---------------------------------------------------------------------------


class TestListSubagents:
    """Tests for list_subagents()."""

    def test_invalid_session_id(self, claude_config_dir: Path):
        """Non-UUID session_id returns empty list."""
        assert list_subagents("not-a-uuid") == []
        assert list_subagents("") == []

    def test_nonexistent_session(self, claude_config_dir: Path):
        """Session file not found returns empty list."""
        sid = str(uuid.uuid4())
        assert list_subagents(sid) == []

    def test_session_exists_no_subagents_dir(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Session exists but has no subagents directory → empty list."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(project_dir)

        assert list_subagents(sid, directory=project_path) == []

    def test_empty_subagents_dir(self, claude_config_dir: Path, tmp_path: Path):
        """Subagents directory exists but is empty → empty list."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, _ = _make_session_with_subagents(
            claude_config_dir, project_path, agent_ids=[]
        )

        assert list_subagents(sid, directory=project_path) == []

    def test_happy_path(self, claude_config_dir: Path, tmp_path: Path):
        """Returns agent IDs from agent-*.jsonl filenames."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, _ = _make_session_with_subagents(
            claude_config_dir, project_path, agent_ids=["abc123", "def456"]
        )

        result = list_subagents(sid, directory=project_path)
        assert sorted(result) == ["abc123", "def456"]

    def test_ignores_non_agent_files(self, claude_config_dir: Path, tmp_path: Path):
        """Non-matching files (.meta.json, other JSONL) are ignored."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, subagents_dir = _make_session_with_subagents(
            claude_config_dir, project_path, agent_ids=["keep"]
        )
        (subagents_dir / "agent-keep.meta.json").write_text("{}")
        (subagents_dir / "other.jsonl").write_text("{}\n")
        (subagents_dir / "agent-noext").write_text("{}")

        result = list_subagents(sid, directory=project_path)
        assert result == ["keep"]

    def test_recurses_into_subdirectories(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Agent files in nested subdirectories are found."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, subagents_dir = _make_session_with_subagents(
            claude_config_dir, project_path, agent_ids=["top"]
        )
        nested = subagents_dir / "workflows" / "run-1"
        nested.mkdir(parents=True)
        (nested / "agent-nested.jsonl").write_text("{}\n")

        result = list_subagents(sid, directory=project_path)
        assert sorted(result) == ["nested", "top"]

    def test_searches_all_projects_without_directory(self, claude_config_dir: Path):
        """When directory is omitted, all project directories are searched."""
        project_dir = _make_project_dir(claude_config_dir, "/some/project")
        sid, _ = _make_session_file(project_dir)
        subagents_dir = project_dir / sid / "subagents"
        subagents_dir.mkdir(parents=True)
        (subagents_dir / "agent-x.jsonl").write_text("{}\n")

        assert list_subagents(sid) == ["x"]


# ---------------------------------------------------------------------------
# get_subagent_messages() tests
# ---------------------------------------------------------------------------


class TestGetSubagentMessages:
    """Tests for get_subagent_messages()."""

    def test_invalid_session_id(self, claude_config_dir: Path):
        """Non-UUID session_id returns empty list."""
        assert get_subagent_messages("not-a-uuid", "abc") == []
        assert get_subagent_messages("", "abc") == []

    def test_empty_agent_id(self, claude_config_dir: Path):
        """Empty agent_id returns empty list."""
        sid = str(uuid.uuid4())
        assert get_subagent_messages(sid, "") == []

    def test_nonexistent_session(self, claude_config_dir: Path):
        """Session file not found returns empty list."""
        sid = str(uuid.uuid4())
        assert get_subagent_messages(sid, "abc") == []

    def test_nonexistent_agent(self, claude_config_dir: Path, tmp_path: Path):
        """Agent file not found returns empty list."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, _ = _make_session_with_subagents(
            claude_config_dir, project_path, agent_ids=["other"]
        )

        assert get_subagent_messages(sid, "missing", directory=project_path) == []

    def test_simple_chain(self, claude_config_dir: Path, tmp_path: Path):
        """Basic user → assistant chain from a subagent transcript."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, subagents_dir = _make_session_with_subagents(
            claude_config_dir, project_path
        )

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        a2 = str(uuid.uuid4())
        entries = [
            _make_transcript_entry("user", u1, None, sid, content="task"),
            _make_transcript_entry("assistant", a1, u1, sid, content="working"),
            _make_transcript_entry("user", u2, a1, sid, content="continue"),
            _make_transcript_entry("assistant", a2, u2, sid, content="done"),
        ]
        agent_file = subagents_dir / "agent-abc.jsonl"
        agent_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        messages = get_subagent_messages(sid, "abc", directory=project_path)
        assert len(messages) == 4
        assert all(isinstance(m, SessionMessage) for m in messages)
        assert [m.uuid for m in messages] == [u1, a1, u2, a2]
        assert messages[0].type == "user"
        assert messages[0].session_id == sid
        assert messages[0].message == {"role": "user", "content": "task"}
        assert messages[0].parent_tool_use_id is None
        assert messages[3].type == "assistant"

    def test_finds_agent_in_nested_subdirectory(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Agent file in a nested subdirectory is found by ID."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, subagents_dir = _make_session_with_subagents(
            claude_config_dir, project_path
        )
        nested = subagents_dir / "workflows" / "run-1"
        nested.mkdir(parents=True)

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())
        entries = [
            _make_transcript_entry("user", u1, None, sid, content="hi"),
            _make_transcript_entry("assistant", a1, u1, sid, content="hello"),
        ]
        (nested / "agent-deep.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )

        messages = get_subagent_messages(sid, "deep", directory=project_path)
        assert len(messages) == 2
        assert messages[0].uuid == u1
        assert messages[1].uuid == a1

    def test_skips_corrupt_lines(self, claude_config_dir: Path, tmp_path: Path):
        """Corrupt JSONL lines are skipped."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, subagents_dir = _make_session_with_subagents(
            claude_config_dir, project_path
        )

        u1 = str(uuid.uuid4())
        a1 = str(uuid.uuid4())
        lines = [
            json.dumps(_make_transcript_entry("user", u1, None, sid, content="hi")),
            "not valid json {",
            "",
            json.dumps(_make_transcript_entry("assistant", a1, u1, sid, content="ok")),
        ]
        (subagents_dir / "agent-x.jsonl").write_text("\n".join(lines) + "\n")

        messages = get_subagent_messages(sid, "x", directory=project_path)
        assert [m.uuid for m in messages] == [u1, a1]

    def test_limit_and_offset(self, claude_config_dir: Path, tmp_path: Path):
        """Limit and offset pagination."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, subagents_dir = _make_session_with_subagents(
            claude_config_dir, project_path
        )

        uuids = [str(uuid.uuid4()) for _ in range(6)]
        entries = []
        for i, uid in enumerate(uuids):
            parent = uuids[i - 1] if i > 0 else None
            entry_type = "user" if i % 2 == 0 else "assistant"
            entries.append(
                _make_transcript_entry(entry_type, uid, parent, sid, content=f"m{i}")
            )
        (subagents_dir / "agent-p.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )

        all_msgs = get_subagent_messages(sid, "p", directory=project_path)
        assert len(all_msgs) == 6

        page = get_subagent_messages(sid, "p", directory=project_path, limit=2)
        assert [m.uuid for m in page] == uuids[:2]

        page = get_subagent_messages(
            sid, "p", directory=project_path, limit=2, offset=2
        )
        assert [m.uuid for m in page] == uuids[2:4]

        page = get_subagent_messages(sid, "p", directory=project_path, offset=4)
        assert [m.uuid for m in page] == uuids[4:]

        page = get_subagent_messages(sid, "p", directory=project_path, limit=0)
        assert len(page) == 6

    def test_empty_agent_file(self, claude_config_dir: Path, tmp_path: Path):
        """Empty agent transcript file returns empty list."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        sid, subagents_dir = _make_session_with_subagents(
            claude_config_dir, project_path
        )
        (subagents_dir / "agent-empty.jsonl").write_text("")

        assert get_subagent_messages(sid, "empty", directory=project_path) == []
