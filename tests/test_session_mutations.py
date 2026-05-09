"""Tests for session mutation functions (rename, tag, delete, fork)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from claude_agent_sdk import (
    delete_session,
    fork_session,
    list_sessions,
    rename_session,
    tag_session,
)
from claude_agent_sdk._internal.session_mutations import (
    _sanitize_unicode,
    _try_append,
)
from claude_agent_sdk._internal.sessions import _sanitize_path, get_session_messages

# ---------------------------------------------------------------------------
# Fixtures (shared with test_sessions.py patterns)
# ---------------------------------------------------------------------------


@pytest.fixture
def claude_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Creates a temporary ~/.claude directory and points CLAUDE_CONFIG_DIR at it."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


def _make_project_dir(config_dir: Path, project_path: str) -> Path:
    """Creates a sanitized project directory for the given path."""
    sanitized = _sanitize_path(project_path)
    project_dir = config_dir / "projects" / sanitized
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def _make_session_file(
    project_dir: Path,
    session_id: str | None = None,
    first_prompt: str = "Hello Claude",
) -> tuple[str, Path]:
    """Creates a .jsonl session file. Returns (session_id, file_path)."""
    sid = session_id or str(uuid.uuid4())
    file_path = project_dir / f"{sid}.jsonl"
    lines = [
        json.dumps(
            {"type": "user", "message": {"role": "user", "content": first_prompt}}
        ),
        json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "content": "Hi!"}}
        ),
    ]
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sid, file_path


# ---------------------------------------------------------------------------
# _try_append() tests
# ---------------------------------------------------------------------------


class TestTryAppend:
    """Tests for the low-level _try_append helper."""

    def test_append_to_existing_file(self, tmp_path: Path):
        """Appends to an existing non-empty file."""
        f = tmp_path / "test.jsonl"
        f.write_text("line1\n")
        result = _try_append(f, "line2\n")
        assert result is True
        assert f.read_text() == "line1\nline2\n"

    def test_missing_file_returns_false(self, tmp_path: Path):
        """ENOENT returns False (not an error)."""
        f = tmp_path / "nonexistent.jsonl"
        result = _try_append(f, "data\n")
        assert result is False
        assert not f.exists()  # did NOT create the file

    def test_missing_parent_dir_returns_false(self, tmp_path: Path):
        """ENOTDIR/ENOENT on parent dir returns False."""
        f = tmp_path / "nonexistent" / "file.jsonl"
        result = _try_append(f, "data\n")
        assert result is False

    def test_zero_byte_file_returns_false(self, tmp_path: Path):
        """0-byte stub returns False (keep searching)."""
        f = tmp_path / "stub.jsonl"
        f.write_text("")
        result = _try_append(f, "data\n")
        assert result is False
        assert f.read_text() == ""  # not modified

    def test_multiple_appends(self, tmp_path: Path):
        """Multiple appends land in order at EOF (O_APPEND semantics)."""
        f = tmp_path / "test.jsonl"
        f.write_text("line1\n")
        _try_append(f, "line2\n")
        _try_append(f, "line3\n")
        assert f.read_text() == "line1\nline2\nline3\n"


# ---------------------------------------------------------------------------
# rename_session() tests
# ---------------------------------------------------------------------------


class TestRenameSession:
    """Tests for rename_session()."""

    def test_invalid_session_id_raises(self, claude_config_dir: Path):
        """Non-UUID session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            rename_session("not-a-uuid", "title")
        with pytest.raises(ValueError, match="Invalid session_id"):
            rename_session("", "title")

    def test_empty_title_raises(self, claude_config_dir: Path, tmp_path: Path):
        """Empty or whitespace-only title raises ValueError."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(project_dir)

        with pytest.raises(ValueError, match="title must be non-empty"):
            rename_session(sid, "", directory=project_path)
        with pytest.raises(ValueError, match="title must be non-empty"):
            rename_session(sid, "   ", directory=project_path)
        with pytest.raises(ValueError, match="title must be non-empty"):
            rename_session(sid, "\n\t", directory=project_path)

    def test_session_not_found_raises(self, claude_config_dir: Path, tmp_path: Path):
        """Session not found raises FileNotFoundError."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        _make_project_dir(claude_config_dir, os.path.realpath(project_path))

        sid = str(uuid.uuid4())
        with pytest.raises(FileNotFoundError):
            rename_session(sid, "title", directory=project_path)

    def test_no_projects_dir_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Missing projects dir raises FileNotFoundError."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nonexistent"))
        sid = str(uuid.uuid4())
        with pytest.raises(FileNotFoundError, match="no projects directory"):
            rename_session(sid, "title")

    def test_appends_custom_title_entry(self, claude_config_dir: Path, tmp_path: Path):
        """rename_session appends a {type:'custom-title'} JSON line."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        rename_session(sid, "My New Title", directory=project_path)

        content = file_path.read_text()
        lines = content.strip().split("\n")
        # Last line should be the custom-title entry
        entry = json.loads(lines[-1])
        assert entry["type"] == "custom-title"
        assert entry["customTitle"] == "My New Title"
        assert entry["sessionId"] == sid

    def test_title_trimmed_before_storing(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Leading/trailing whitespace is stripped from title."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        rename_session(sid, "  Trimmed Title  ", directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["customTitle"] == "Trimmed Title"

    def test_last_wins_via_list_sessions(self, claude_config_dir: Path, tmp_path: Path):
        """Multiple renames — list_sessions sees the last one."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(project_dir, first_prompt="original")

        rename_session(sid, "First Title", directory=project_path)
        rename_session(sid, "Second Title", directory=project_path)
        rename_session(sid, "Final Title", directory=project_path)

        sessions = list_sessions(directory=project_path, include_worktrees=False)
        assert len(sessions) == 1
        assert sessions[0].custom_title == "Final Title"
        assert sessions[0].summary == "Final Title"

    def test_search_all_projects(self, claude_config_dir: Path):
        """When no directory given, searches all project directories."""
        project_dir = _make_project_dir(claude_config_dir, "/some/project")
        sid, file_path = _make_session_file(project_dir)

        rename_session(sid, "Found Without Dir")

        lines = file_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["customTitle"] == "Found Without Dir"

    def test_skips_zero_byte_stub(self, claude_config_dir: Path):
        """0-byte stub in earlier dir is skipped; real file in later dir is found."""
        # Create two project dirs — alphabetical order matters for iteration
        proj_a = _make_project_dir(claude_config_dir, "/aaa/project")
        proj_z = _make_project_dir(claude_config_dir, "/zzz/project")

        sid = str(uuid.uuid4())
        # 0-byte stub in first dir
        (proj_a / f"{sid}.jsonl").write_text("")
        # Real file in second dir
        _make_session_file(proj_z, session_id=sid, first_prompt="real")

        rename_session(sid, "New Title")

        # Stub untouched
        assert (proj_a / f"{sid}.jsonl").read_text() == ""
        # Real file has the entry
        real_content = (proj_z / f"{sid}.jsonl").read_text()
        assert '"customTitle":"New Title"' in real_content

    def test_compact_json_format(self, claude_config_dir: Path, tmp_path: Path):
        """Appended JSON uses compact separators (no spaces) matching CLI."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        rename_session(sid, "Title", directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        # Compact JSON: no spaces after : or ,
        assert lines[-1] == (
            f'{{"type":"custom-title","customTitle":"Title","sessionId":"{sid}"}}'
        )


# ---------------------------------------------------------------------------
# tag_session() tests
# ---------------------------------------------------------------------------


class TestTagSession:
    """Tests for tag_session()."""

    def test_invalid_session_id_raises(self, claude_config_dir: Path):
        """Non-UUID session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            tag_session("not-a-uuid", "tag")
        with pytest.raises(ValueError, match="Invalid session_id"):
            tag_session("", "tag")

    def test_empty_tag_raises(self, claude_config_dir: Path, tmp_path: Path):
        """Empty or whitespace-only tag raises ValueError."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(project_dir)

        with pytest.raises(ValueError, match="tag must be non-empty"):
            tag_session(sid, "", directory=project_path)
        with pytest.raises(ValueError, match="tag must be non-empty"):
            tag_session(sid, "   ", directory=project_path)

    def test_session_not_found_raises(self, claude_config_dir: Path, tmp_path: Path):
        """Session not found raises FileNotFoundError."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        _make_project_dir(claude_config_dir, os.path.realpath(project_path))

        sid = str(uuid.uuid4())
        with pytest.raises(FileNotFoundError):
            tag_session(sid, "tag", directory=project_path)

    def test_appends_tag_entry(self, claude_config_dir: Path, tmp_path: Path):
        """tag_session appends a {type:'tag'} JSON line."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        tag_session(sid, "experiment", directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["type"] == "tag"
        assert entry["tag"] == "experiment"
        assert entry["sessionId"] == sid

    def test_tag_trimmed(self, claude_config_dir: Path, tmp_path: Path):
        """Leading/trailing whitespace is stripped from tag."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        tag_session(sid, "  my-tag  ", directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["tag"] == "my-tag"

    def test_none_clears_tag(self, claude_config_dir: Path, tmp_path: Path):
        """Passing None appends an empty-string tag entry (clears tag)."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        tag_session(sid, "original-tag", directory=project_path)
        tag_session(sid, None, directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        # Last entry is the clear
        entry = json.loads(lines[-1])
        assert entry["type"] == "tag"
        assert entry["tag"] == ""
        assert entry["sessionId"] == sid

    def test_last_wins(self, claude_config_dir: Path, tmp_path: Path):
        """Multiple tag calls — last one lands at EOF."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        tag_session(sid, "first", directory=project_path)
        tag_session(sid, "second", directory=project_path)
        tag_session(sid, "third", directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["tag"] == "third"
        # All three tag entries present in file
        tag_lines = [
            json.loads(line) for line in lines if json.loads(line).get("type") == "tag"
        ]
        assert len(tag_lines) == 3

    def test_compact_json_format(self, claude_config_dir: Path, tmp_path: Path):
        """Appended JSON uses compact separators matching CLI."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        tag_session(sid, "mytag", directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        assert lines[-1] == f'{{"type":"tag","tag":"mytag","sessionId":"{sid}"}}'

    def test_unicode_sanitization(self, claude_config_dir: Path, tmp_path: Path):
        """Tag is sanitized: zero-width chars stripped."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)

        # Tag with zero-width space and BOM embedded
        dirty_tag = "clean\u200btag\ufeff"
        tag_session(sid, dirty_tag, directory=project_path)

        lines = file_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["tag"] == "cleantag"

    def test_sanitization_rejects_pure_invisible(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Tag that is only zero-width chars is rejected."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(project_dir)

        with pytest.raises(ValueError, match="tag must be non-empty"):
            tag_session(sid, "\u200b\u200c\ufeff", directory=project_path)


class TestSanitizeUnicode:
    """Tests for the _sanitize_unicode helper."""

    def test_passthrough_clean_string(self):
        """Clean strings pass through unchanged."""
        assert _sanitize_unicode("hello") == "hello"
        assert _sanitize_unicode("tag-with-dashes_123") == "tag-with-dashes_123"

    def test_strips_zero_width(self):
        """Zero-width spaces/joiners are stripped."""
        assert _sanitize_unicode("a\u200bb") == "ab"
        assert _sanitize_unicode("a\u200cb") == "ab"  # zero-width non-joiner
        assert _sanitize_unicode("a\u200db") == "ab"  # zero-width joiner

    def test_strips_bom(self):
        """Byte order mark is stripped."""
        assert _sanitize_unicode("\ufeffhello") == "hello"

    def test_strips_directional_marks(self):
        """LTR/RTL marks and isolates are stripped."""
        assert _sanitize_unicode("a\u202ab\u202cc") == "abc"
        assert _sanitize_unicode("a\u2066b\u2069c") == "abc"

    def test_strips_private_use(self):
        """Private use area characters are stripped."""
        assert _sanitize_unicode("a\ue000b") == "ab"
        assert _sanitize_unicode("a\uf8ffb") == "ab"

    def test_nfkc_normalization(self):
        """NFKC normalization is applied (composed chars)."""
        # Fullwidth 'A' → ASCII 'A'
        assert _sanitize_unicode("\uff21") == "A"

    def test_iterative_converges(self):
        """Handles multi-pass cases safely (max 10 iterations)."""
        # A string that needs multiple passes still converges
        result = _sanitize_unicode("a" + "\u200b" * 20 + "b")
        assert result == "ab"


# ---------------------------------------------------------------------------
# delete_session() tests
# ---------------------------------------------------------------------------


class TestDeleteSession:
    """Tests for delete_session()."""

    def test_invalid_session_id_raises(self, claude_config_dir: Path):
        with pytest.raises(ValueError, match="Invalid session_id"):
            delete_session("not-a-uuid")

    def test_session_not_found_raises(self, claude_config_dir: Path):
        sid = str(uuid.uuid4())
        with pytest.raises(FileNotFoundError, match="not found"):
            delete_session(sid)

    def test_deletes_session_file(self, claude_config_dir: Path, tmp_path: Path):
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)
        assert file_path.exists()
        delete_session(sid, directory=project_path)
        assert not file_path.exists()

    def test_removes_subagent_transcript_dir(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Cascades the sibling {sid}/ subagent dir alongside the .jsonl."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, file_path = _make_session_file(project_dir)
        subagent_dir = project_dir / sid
        subagent_dir.mkdir()
        (subagent_dir / f"{uuid.uuid4()}.jsonl").write_text("{}\n")

        delete_session(sid, directory=project_path)

        assert not file_path.exists()
        assert not subagent_dir.exists()

    def test_deletes_without_directory(self, claude_config_dir: Path):
        """Searches all project directories when no directory is given."""
        project_dir = _make_project_dir(claude_config_dir, "/any/project")
        sid, file_path = _make_session_file(project_dir)
        assert file_path.exists()
        delete_session(sid)
        assert not file_path.exists()

    def test_no_longer_in_list_sessions(self, claude_config_dir: Path, tmp_path: Path):
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _ = _make_session_file(project_dir)
        sessions = list_sessions(directory=project_path)
        assert any(s.session_id == sid for s in sessions)
        delete_session(sid, directory=project_path)
        sessions = list_sessions(directory=project_path)
        assert not any(s.session_id == sid for s in sessions)


# ---------------------------------------------------------------------------
# Helpers for fork tests — need transcript entries with uuid + parentUuid
# ---------------------------------------------------------------------------


def _make_transcript_session(
    project_dir: Path,
    session_id: str | None = None,
    num_turns: int = 2,
) -> tuple[str, Path, list[str]]:
    """Creates a session file with proper uuid/parentUuid chain.

    Returns (session_id, file_path, list_of_message_uuids).
    """
    sid = session_id or str(uuid.uuid4())
    file_path = project_dir / f"{sid}.jsonl"
    uuids: list[str] = []
    lines: list[str] = []
    parent_uuid: str | None = None

    for i in range(num_turns):
        # User message
        user_uuid = str(uuid.uuid4())
        uuids.append(user_uuid)
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "uuid": user_uuid,
                    "parentUuid": parent_uuid,
                    "sessionId": sid,
                    "timestamp": "2026-03-01T00:00:00Z",
                    "message": {
                        "role": "user",
                        "content": f"Turn {i + 1} question",
                    },
                }
            )
        )
        parent_uuid = user_uuid

        # Assistant message
        asst_uuid = str(uuid.uuid4())
        uuids.append(asst_uuid)
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "uuid": asst_uuid,
                    "parentUuid": parent_uuid,
                    "sessionId": sid,
                    "timestamp": "2026-03-01T00:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"Turn {i + 1} answer"}],
                    },
                }
            )
        )
        parent_uuid = asst_uuid

    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sid, file_path, uuids


# ---------------------------------------------------------------------------
# fork_session() tests
# ---------------------------------------------------------------------------


class TestForkSession:
    """Tests for fork_session()."""

    def test_invalid_session_id_raises(self, claude_config_dir: Path):
        with pytest.raises(ValueError, match="Invalid session_id"):
            fork_session("not-a-uuid")

    def test_session_not_found_raises(self, claude_config_dir: Path):
        sid = str(uuid.uuid4())
        with pytest.raises(FileNotFoundError, match="not found"):
            fork_session(sid)

    def test_invalid_up_to_message_id_raises(self, claude_config_dir: Path):
        with pytest.raises(ValueError, match="Invalid up_to_message_id"):
            fork_session(str(uuid.uuid4()), up_to_message_id="not-valid")

    def test_fork_creates_new_session(self, claude_config_dir: Path, tmp_path: Path):
        """Fork creates a new session file with a different session_id."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, _ = _make_transcript_session(project_dir)

        result = fork_session(sid, directory=project_path)
        assert result.session_id != sid

        # New file exists
        fork_path = project_dir / f"{result.session_id}.jsonl"
        assert fork_path.exists()

    def test_fork_remaps_uuids(self, claude_config_dir: Path, tmp_path: Path):
        """uuid and parentUuid fields are remapped; originals only appear in forkedFrom."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, original_uuids = _make_transcript_session(project_dir)

        result = fork_session(sid, directory=project_path)
        fork_path = project_dir / f"{result.session_id}.jsonl"

        for line in fork_path.read_text().strip().split("\n"):
            entry = json.loads(line)
            if entry.get("type") in ("user", "assistant"):
                assert entry["uuid"] not in original_uuids
                if entry["parentUuid"] is not None:
                    assert entry["parentUuid"] not in original_uuids

    def test_fork_preserves_message_count(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Fork has the same number of user/assistant messages."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, _ = _make_transcript_session(project_dir, num_turns=3)

        result = fork_session(sid, directory=project_path)

        original_msgs = get_session_messages(sid, directory=project_path)
        fork_msgs = get_session_messages(result.session_id, directory=project_path)
        # Fork has same visible messages + 1 custom-title entry (not visible)
        assert len(fork_msgs) == len(original_msgs)

    def test_fork_up_to_message_id(self, claude_config_dir: Path, tmp_path: Path):
        """Fork slices at up_to_message_id (inclusive)."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, uuids = _make_transcript_session(project_dir, num_turns=3)

        # Fork up to the first assistant response (uuid index 1)
        cutoff_uuid = uuids[1]
        result = fork_session(
            sid,
            directory=project_path,
            up_to_message_id=cutoff_uuid,
        )

        fork_msgs = get_session_messages(result.session_id, directory=project_path)
        # Should have 2 messages (1 user + 1 assistant from first turn)
        assert len(fork_msgs) == 2

    def test_fork_up_to_message_id_not_found_raises(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, _ = _make_transcript_session(project_dir)

        fake_uuid = str(uuid.uuid4())
        with pytest.raises(ValueError, match="not found in session"):
            fork_session(
                sid,
                directory=project_path,
                up_to_message_id=fake_uuid,
            )

    def test_fork_custom_title(self, claude_config_dir: Path, tmp_path: Path):
        """Custom title is used when provided."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, _ = _make_transcript_session(project_dir)

        result = fork_session(sid, directory=project_path, title="My Fork")

        sessions = list_sessions(directory=project_path)
        fork_info = next(s for s in sessions if s.session_id == result.session_id)
        assert fork_info.custom_title == "My Fork"

    def test_fork_default_title_has_suffix(
        self, claude_config_dir: Path, tmp_path: Path
    ):
        """Default fork title is derived from original + ' (fork)'."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, _ = _make_transcript_session(project_dir)

        result = fork_session(sid, directory=project_path)

        sessions = list_sessions(directory=project_path)
        fork_info = next(s for s in sessions if s.session_id == result.session_id)
        assert fork_info.custom_title is not None
        assert fork_info.custom_title.endswith("(fork)")

    def test_fork_session_id_in_entries(self, claude_config_dir: Path, tmp_path: Path):
        """Every entry in the fork has the new session ID."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, _ = _make_transcript_session(project_dir)

        result = fork_session(sid, directory=project_path)
        fork_path = project_dir / f"{result.session_id}.jsonl"

        for line in fork_path.read_text().strip().split("\n"):
            entry = json.loads(line)
            assert entry.get("sessionId") == result.session_id

    def test_fork_forked_from_field(self, claude_config_dir: Path, tmp_path: Path):
        """Transcript entries have a forkedFrom field pointing to the source."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid, _, _ = _make_transcript_session(project_dir)

        result = fork_session(sid, directory=project_path)
        fork_path = project_dir / f"{result.session_id}.jsonl"

        for line in fork_path.read_text().strip().split("\n"):
            entry = json.loads(line)
            if entry.get("type") in ("user", "assistant"):
                assert entry["forkedFrom"]["sessionId"] == sid

    def test_fork_without_directory(self, claude_config_dir: Path):
        """Searches all project directories when no directory is given."""
        project_dir = _make_project_dir(claude_config_dir, "/any/project")
        sid, _, _ = _make_transcript_session(project_dir)

        result = fork_session(sid)
        fork_path = project_dir / f"{result.session_id}.jsonl"
        assert fork_path.exists()

    def test_fork_clears_stale_fields(self, claude_config_dir: Path, tmp_path: Path):
        """teamName, agentName, slug are removed from forked entries."""
        project_path = str(tmp_path / "proj")
        Path(project_path).mkdir(parents=True)
        project_dir = _make_project_dir(
            claude_config_dir, os.path.realpath(project_path)
        )
        sid = str(uuid.uuid4())
        file_path = project_dir / f"{sid}.jsonl"
        entry = {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "parentUuid": None,
            "sessionId": sid,
            "timestamp": "2026-03-01T00:00:00Z",
            "teamName": "test-team",
            "agentName": "test-agent",
            "slug": "test-slug",
            "message": {"role": "user", "content": "Hello"},
        }
        file_path.write_text(json.dumps(entry) + "\n")

        result = fork_session(sid, directory=project_path)
        fork_path = project_dir / f"{result.session_id}.jsonl"

        for line in fork_path.read_text().strip().split("\n"):
            e = json.loads(line)
            if e.get("type") == "user":
                assert "teamName" not in e
                assert "agentName" not in e
                assert "slug" not in e
