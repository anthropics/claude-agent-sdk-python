"""Transcript timestamps without offsets must be stable across host timezones."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from claude_agent_sdk._internal import session_summary, sessions


class _UTCPlusThreeDatetime(datetime):
    """Simulate a host with UTC+03 for naive datetime.timestamp()."""

    def timestamp(self) -> float:
        if self.tzinfo is None:
            return datetime.timestamp(self.replace(tzinfo=timezone(timedelta(hours=3))))
        return datetime.timestamp(self)


@pytest.mark.parametrize(
    "iso",
    [
        "2024-01-01T00:00:00",
        "2024-01-01T00:00:00Z",
        "2024-01-01T03:00:00+03:00",
    ],
)
def test_summary_timestamps_do_not_depend_on_local_timezone(
    monkeypatch: pytest.MonkeyPatch, iso: str
) -> None:
    monkeypatch.setattr(session_summary, "datetime", _UTCPlusThreeDatetime)
    assert session_summary._iso_to_epoch_ms(iso) == 1704067200000


@pytest.mark.parametrize(
    "iso",
    [
        "2024-01-01T00:00:00",
        "2024-01-01T00:00:00Z",
        "2024-01-01T03:00:00+03:00",
    ],
)
def test_transcript_tail_mtime_do_not_depend_on_local_timezone(
    monkeypatch: pytest.MonkeyPatch, iso: str
) -> None:
    monkeypatch.setattr(sessions, "datetime", _UTCPlusThreeDatetime)
    text = json.dumps({"type": "user", "timestamp": iso})
    assert sessions._mtime_from_jsonl_tail(text) == 1704067200000


def test_disk_session_created_at_matches_utc_store_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions, "datetime", _UTCPlusThreeDatetime)
    monkeypatch.setattr(session_summary, "datetime", _UTCPlusThreeDatetime)
    config_dir = tmp_path / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    directory = tmp_path / "project"
    directory.mkdir()
    project_dir = (
        config_dir / "projects" / sessions._sanitize_path(os.path.realpath(directory))
    )
    project_dir.mkdir(parents=True)
    session_id = str(uuid.uuid4())
    entry = {
        "type": "user",
        "uuid": str(uuid.uuid4()),
        "sessionId": session_id,
        "timestamp": "2024-01-01T00:00:00",
        "message": {"role": "user", "content": "Hello"},
    }
    (project_dir / f"{session_id}.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    found = sessions.list_sessions(directory=str(directory), include_worktrees=False)
    assert len(found) == 1
    assert found[0].created_at == 1704067200000
    folded = session_summary.fold_session_summary(
        None, {"project_key": "proj", "session_id": session_id}, [entry]
    )
    assert folded["data"]["created_at"] == found[0].created_at
