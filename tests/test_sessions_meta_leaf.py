"""A trailing meta entry must not swallow the conversation tail.

After a parallel tool turn the transcript forks and each tool result is a
terminal. When the session's real tail is only pointed at by a trailing
``isMeta`` bookkeeping entry, the terminal-to-leaf walk used to stop at that
meta entry (it has type ``user``), ``main_leaves`` then excluded it, and the
real tail never became a leaf candidate — an off-branch tool result won
instead and every message after the fork (e.g. the user's last question)
vanished from ``get_session_messages``.
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


def _setup(claude_config_dir: Path, tmp_path: Path) -> str:
    project_path = str(tmp_path / "proj")
    Path(project_path).mkdir(parents=True)
    _make_project_dir(claude_config_dir, str(Path(project_path).resolve()))
    return project_path


def test_meta_tail_does_not_drop_conversation_tail(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    project_path = _setup(claude_config_dir, tmp_path)
    sid = str(uuid.uuid4())

    def call(i: int, parent: str) -> dict:
        return _make_transcript_entry(
            "assistant",
            f"call-{i}",
            parent,
            sid,
            content=[
                {"type": "tool_use", "id": f"toolu_{i}", "name": "Read", "input": {}}
            ],
            timestamp=f"2026-01-01T00:00:01.00{i}Z",
        )

    call0 = call(0, "prompt")
    call1 = call(1, "call-0")
    call0["message"]["id"] = "msg_parallel"
    call1["message"]["id"] = "msg_parallel"

    def result(i: int) -> dict:
        return _make_transcript_entry(
            "user",
            f"result-{i}",
            f"call-{i}",
            sid,
            content=[
                {"type": "tool_result", "tool_use_id": f"toolu_{i}", "content": "ok"}
            ],
            timestamp=f"2026-01-01T00:00:02.00{i}Z",
            sourceToolAssistantUUID=f"call-{i}",
        )

    entries = [
        _make_transcript_entry(
            "user", "prompt", None, sid, content=[{"type": "text", "text": "go"}]
        ),
        call0,
        call1,
        result(0),
        result(1),
        # The conversation continued from result-1...
        _make_transcript_entry(
            "user",
            "last-question",
            "result-1",
            sid,
            content=[{"type": "text", "text": "one more thing"}],
            timestamp="2026-01-01T00:00:03Z",
        ),
        # ...and the transcript ends with a bookkeeping meta entry chained
        # onto it. Only this entry points at last-question.
        _make_transcript_entry(
            "user",
            "meta-tail",
            "last-question",
            sid,
            content=[{"type": "text", "text": "<bookkeeping/>"}],
            timestamp="2026-01-01T00:00:04Z",
            isMeta=True,
        ),
    ]
    _write_transcript(
        _make_project_dir(claude_config_dir, str(Path(project_path).resolve())),
        sid,
        entries,
    )

    messages = get_session_messages(sid, directory=project_path)

    uuids = [m.uuid for m in messages]
    assert "last-question" in uuids, f"conversation tail dropped; got {uuids}"
    assert uuids[-1] == "last-question"


def test_trailing_meta_without_fork_keeps_history(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    """A trailing meta entry on a linear transcript changes nothing."""
    project_path = _setup(claude_config_dir, tmp_path)
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
            content=[{"type": "text", "text": "done"}],
        ),
        _make_transcript_entry(
            "user",
            "meta-tail",
            "reply",
            sid,
            content=[{"type": "text", "text": "<bookkeeping/>"}],
            isMeta=True,
        ),
    ]
    _write_transcript(
        _make_project_dir(claude_config_dir, str(Path(project_path).resolve())),
        sid,
        entries,
    )

    messages = get_session_messages(sid, directory=project_path)

    assert [m.uuid for m in messages] == ["prompt", "reply"]


def test_meta_only_transcript_returns_no_messages(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    """A transcript whose only user entry is meta still yields nothing."""
    project_path = _setup(claude_config_dir, tmp_path)
    sid = str(uuid.uuid4())

    entries = [
        _make_transcript_entry(
            "user",
            "meta-only",
            None,
            sid,
            content=[{"type": "text", "text": "<bookkeeping/>"}],
            isMeta=True,
        ),
    ]
    _write_transcript(
        _make_project_dir(claude_config_dir, str(Path(project_path).resolve())),
        sid,
        entries,
    )

    assert get_session_messages(sid, directory=project_path) == []
