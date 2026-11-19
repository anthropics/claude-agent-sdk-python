"""Bounded regression tests for progress-parent traversal in both fork APIs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

# Run in a child process: the regression is a synchronous infinite loop, so an
# async timeout cannot interrupt it. No CLI or network access is needed.
_FORK_SCRIPT = """
import json
import sys
from pathlib import Path

import anyio

from claude_agent_sdk import (
    InMemorySessionStore,
    fork_session,
    fork_session_via_store,
    project_key_for_directory,
)

storage, directory, session_id, source_path = sys.argv[1:]
source = Path(source_path)
entries = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]

async def fork_from_store():
    store = InMemorySessionStore()
    key = {"project_key": project_key_for_directory(directory), "session_id": session_id}
    await store.append(key, entries)
    before = json.dumps(await store.load(key))
    result = await fork_session_via_store(store, session_id, directory=directory)
    assert json.dumps(await store.load(key)) == before
    return await store.load({**key, "session_id": result.session_id})

if storage == "disk":
    result = fork_session(session_id, directory=directory)
    target = source.with_name(result.session_id + ".jsonl")
    forked = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
else:
    forked = anyio.run(fork_from_store)

print(json.dumps(forked))
"""


@pytest.mark.parametrize("storage", ["disk", "store"])
@pytest.mark.parametrize("chain", ["normal", "missing", "self_cycle", "two_node_cycle"])
def test_fork_progress_parent_chains(tmp_path: Path, storage: str, chain: str) -> None:
    from claude_agent_sdk import project_key_for_directory

    directory = tmp_path / "project"
    directory.mkdir()
    config = tmp_path / "config"
    project_dir = config / "projects" / project_key_for_directory(directory)
    project_dir.mkdir(parents=True)
    sid, root, progress1, progress2, answer1, answer2 = (
        str(uuid.uuid4()) for _ in range(6)
    )
    first_parent = {
        "normal": root,
        "missing": str(uuid.uuid4()),
        "self_cycle": progress1,
        "two_node_cycle": progress2,
    }[chain]
    entries = [
        {"type": "user", "uuid": root, "parentUuid": None},
        {"type": "progress", "uuid": progress1, "parentUuid": first_parent},
        {"type": "progress", "uuid": progress2, "parentUuid": progress1},
        # Two messages share the same progress ancestors. A visited set must
        # be scoped to each walk, or the second valid parent is lost.
        {"type": "assistant", "uuid": answer1, "parentUuid": progress2},
        {"type": "assistant", "uuid": answer2, "parentUuid": progress2},
    ]
    for entry in entries:
        entry["sessionId"] = sid
        if entry["type"] != "progress":
            entry["message"] = {"role": entry["type"], "content": "Hello"}
    source = project_dir / f"{sid}.jsonl"
    original = "\n".join(json.dumps(entry) for entry in entries) + "\n"
    source.write_text(original, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-c", _FORK_SCRIPT, storage, str(directory), sid, str(source)],
        env={
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(config),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    forked = json.loads(completed.stdout)
    messages = [entry for entry in forked if entry["type"] in ("user", "assistant")]
    assert len(messages) == 3
    assert all(entry["type"] != "progress" for entry in forked)
    assert messages[0]["parentUuid"] is None
    expected_parent = messages[0]["uuid"] if chain == "normal" else None
    assert all(entry["parentUuid"] == expected_parent for entry in messages[1:])
    for original_message, forked_message in zip(
        (entries[0], entries[3], entries[4]), messages, strict=True
    ):
        assert forked_message["message"] == original_message["message"]
        assert forked_message["uuid"] != original_message["uuid"]
        assert forked_message["sessionId"] != sid
    assert source.read_text(encoding="utf-8") == original
