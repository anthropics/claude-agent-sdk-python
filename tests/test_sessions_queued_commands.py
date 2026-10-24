"""Queued user commands must surface as user messages.

The CLI records a message the user queued while the agent was working as an
``attachment`` entry with ``attachment.type == "queued_command"`` (prompt in
``attachment.prompt``, the command's id in ``attachment.source_uuid``). The
TypeScript SDK's ``getSessionMessages`` converts it into a user message; the
Python reader kept only the attachment — not a visible type — so the user's
message silently disappeared from the conversation history.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sessions import (  # noqa: E402
    _make_project_dir,
    _make_transcript_entry,
    _write_transcript,
)

from claude_agent_sdk import get_session_messages  # noqa: E402


@pytest.fixture
def claude_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Creates a temporary ~/.claude directory and points CLAUDE_CONFIG_DIR at it."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


def _queued_attachment(
    entry_uuid: str,
    parent_uuid: str | None,
    sid: str,
    prompt: str,
    source_uuid: str,
    **extra: object,
) -> dict:
    attachment: dict = {
        "type": "queued_command",
        "prompt": prompt,
        "source_uuid": source_uuid,
    }
    entry: dict = {
        "type": "attachment",
        "uuid": entry_uuid,
        "parentUuid": parent_uuid,
        "sessionId": sid,
        "isSidechain": False,
        "attachment": attachment,
    }
    entry.update(extra)
    return entry


def test_absorbed_queued_command_is_returned_in_place(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    """A queued command the conversation continued past sits on the chain."""
    project_path = str(tmp_path / "proj")
    Path(project_path).mkdir(parents=True)
    project_dir = _make_project_dir(
        claude_config_dir, str(Path(project_path).resolve())
    )
    sid = str(uuid.uuid4())

    entries = [
        _make_transcript_entry(
            "user", "prompt", None, sid, content=[{"type": "text", "text": "go"}]
        ),
        _make_transcript_entry(
            "assistant",
            "reply",
            "prompt",
            sid,
            content=[{"type": "text", "text": "working on it"}],
        ),
        _make_transcript_entry(
            "user",
            "followup",
            "reply",
            sid,
            content=[{"type": "text", "text": "keep going"}],
        ),
        _queued_attachment(
            "attach-1", "followup", sid, "what CLI did you uninstall?", "queued-1"
        ),
        _make_transcript_entry(
            "assistant",
            "reply2",
            "attach-1",
            sid,
            content=[{"type": "text", "text": "it was claude v1"}],
        ),
    ]
    _write_transcript(project_dir, sid, entries)

    messages = get_session_messages(sid, directory=project_path)

    uuids = [m.uuid for m in messages]
    assert "queued-1" in uuids, f"queued user message missing; got {uuids}"
    queued = next(m for m in messages if m.uuid == "queued-1")
    assert queued.type == "user"
    content = queued.message["content"]
    text = content if isinstance(content, str) else content[0]["text"]
    assert text == "what CLI did you uninstall?"
    # It sits between the turn it followed and the reply that absorbed it.
    assert uuids.index("queued-1") == uuids.index("followup") + 1
    assert uuids[-1] == "reply2"


def test_unabsorbed_queued_command_is_returned_at_the_end(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    """A queued command never absorbed (turn never finished) still surfaces."""
    project_path = str(tmp_path / "proj")
    Path(project_path).mkdir(parents=True)
    project_dir = _make_project_dir(
        claude_config_dir, str(Path(project_path).resolve())
    )
    sid = str(uuid.uuid4())

    entries = [
        _make_transcript_entry(
            "user", "prompt", None, sid, content=[{"type": "text", "text": "go"}]
        ),
        _make_transcript_entry(
            "assistant",
            "reply",
            "prompt",
            sid,
            content=[{"type": "text", "text": "working on it"}],
        ),
        _queued_attachment("attach-1", "reply", sid, "please stop", "queued-1"),
    ]
    _write_transcript(project_dir, sid, entries)

    messages = get_session_messages(sid, directory=project_path)

    uuids = [m.uuid for m in messages]
    assert uuids[-1] == "queued-1", f"queued tail missing; got {uuids}"
    assert messages[-1].type == "user"


def test_rotated_in_queued_command_is_not_duplicated(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    """A command already rotated in as a real user entry (same source_uuid)
    must not be returned a second time."""
    project_path = str(tmp_path / "proj")
    Path(project_path).mkdir(parents=True)
    project_dir = _make_project_dir(
        claude_config_dir, str(Path(project_path).resolve())
    )
    sid = str(uuid.uuid4())

    entries = [
        _make_transcript_entry(
            "user", "prompt", None, sid, content=[{"type": "text", "text": "go"}]
        ),
        _queued_attachment("attach-1", "prompt", sid, "rotated", "queued-1"),
        # The CLI re-emitted the queued command as a real user turn reusing
        # the command's uuid, then the conversation continued from it.
        _make_transcript_entry(
            "user",
            "queued-1",
            "attach-1",
            sid,
            content=[{"type": "text", "text": "rotated"}],
        ),
        _make_transcript_entry(
            "assistant",
            "reply",
            "queued-1",
            sid,
            content=[{"type": "text", "text": "done"}],
        ),
    ]
    _write_transcript(project_dir, sid, entries)

    messages = get_session_messages(sid, directory=project_path)

    uuids = [m.uuid for m in messages]
    assert uuids.count("queued-1") == 1, f"duplicated queued command; got {uuids}"


def test_meta_and_sidechain_queued_commands_stay_hidden(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    """Meta bookkeeping and sidechain queued commands are not surfaced."""
    project_path = str(tmp_path / "proj")
    Path(project_path).mkdir(parents=True)
    project_dir = _make_project_dir(
        claude_config_dir, str(Path(project_path).resolve())
    )
    sid = str(uuid.uuid4())

    entries = [
        _make_transcript_entry(
            "user", "prompt", None, sid, content=[{"type": "text", "text": "go"}]
        ),
        _queued_attachment(
            "attach-meta", "prompt", sid, "meta note", "q-meta", isMeta=True
        ),
        _queued_attachment(
            "attach-side",
            "prompt",
            sid,
            "side note",
            "q-side",
            isSidechain=True,
        ),
    ]
    _write_transcript(project_dir, sid, entries)

    messages = get_session_messages(sid, directory=project_path)

    uuids = [m.uuid for m in messages]
    assert uuids == ["prompt"], f"meta/sidechain commands leaked; got {uuids}"
