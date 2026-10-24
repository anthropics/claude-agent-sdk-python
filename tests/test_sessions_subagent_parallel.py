"""A subagent turn that ran tools in parallel must be returned whole.

The same CLI writes subagent transcripts, so a turn that ran its tools in
parallel forks the transcript exactly like a main-session turn: each
``tool_result`` is a terminal, and the ``parentUuid`` walk keeps only the
branch it happens to land on. The main-session chain builder recovers the
orphaned results (``_recover_orphaned_parallel_tool_results``, mirroring
the TypeScript SDK); the subagent builder did not, so a subagent that ran
parallel tools and was interrupted before its final answer reported an
incomplete history — the sibling tool results vanished from
``get_subagent_messages``.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sessions import (  # noqa: E402
    _make_project_dir,
    _make_session_file,
    _make_transcript_entry,
)

from claude_agent_sdk import get_subagent_messages  # noqa: E402


@pytest.fixture
def claude_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Creates a temporary ~/.claude directory and points CLAUDE_CONFIG_DIR at it."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "projects").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    return config_dir


def _write_subagent_transcript(
    config_dir: Path, project_path: Path, entries: list[dict]
) -> str:
    project_dir = _make_project_dir(config_dir, str(project_path.resolve()))
    sid, _ = _make_session_file(project_dir)
    subagents_dir = project_dir / sid / "subagents"
    subagents_dir.mkdir(parents=True)
    (subagents_dir / "agent-a1.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries)
    )
    return sid


def test_interrupted_parallel_tool_subagent_returns_all_results(
    claude_config_dir: Path, tmp_path: Path
) -> None:
    """A subagent interrupted after a parallel tool turn keeps every result."""
    project_path = tmp_path / "proj"
    project_path.mkdir(parents=True)
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
        # The subagent was interrupted before a final assistant answer.
    ]
    session_id = _write_subagent_transcript(claude_config_dir, project_path, entries)

    messages = get_subagent_messages(session_id, "a1", directory=str(project_path))

    uuids = [m.uuid for m in messages]
    assert "result-0" in uuids, f"orphaned parallel tool result dropped; got {uuids}"
    assert "result-1" in uuids
    assert "call-0" in uuids and "call-1" in uuids
