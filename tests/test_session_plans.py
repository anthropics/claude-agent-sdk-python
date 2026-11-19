"""Plan persistence across store-backed resumes on fresh hosts."""

import json
import os
import shutil
from unittest.mock import AsyncMock

import pytest

from claude_agent_sdk import ClaudeAgentOptions, InMemorySessionStore
from claude_agent_sdk._internal.session_import import import_session_to_store
from claude_agent_sdk._internal.session_plans import (
    MAX_PLAN_BYTES,
    PLAN_SNAPSHOT_TYPE,
    restore_plan_files,
    snapshot_plan_files,
)
from claude_agent_sdk._internal.session_resume import materialize_resume_session
from claude_agent_sdk._internal.sessions import project_key_for_directory
from claude_agent_sdk._internal.transcript_mirror_batcher import TranscriptMirrorBatcher

SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"
SLUG = "gentle-purple-river"
CONTENT = "# Plan\n\nVerify the fresh-host restore. 🌱\n"


@pytest.mark.anyio
@pytest.mark.parametrize("capture", ["mirror", "import"])
@pytest.mark.parametrize("continue_conversation", [False, True])
async def test_plan_round_trip_without_source_files(
    tmp_path, monkeypatch, capture, continue_conversation
):
    project = tmp_path / "project"
    project.mkdir()
    project_key = project_key_for_directory(project)
    source = tmp_path / "source"
    plans = source / "plans"
    plans.mkdir(parents=True)
    (plans / f"{SLUG}.md").write_text(CONTENT, encoding="utf-8")
    (plans / "unrelated.md").write_text("Other session", encoding="utf-8")
    transcript = source / "projects" / project_key / f"{SESSION_ID}.jsonl"
    transcript.parent.mkdir(parents=True)
    entry = {
        "type": "user",
        "uuid": "user-1",
        "slug": SLUG,
        "sessionId": SESSION_ID,
        "message": {"role": "user", "content": "Make a plan"},
    }
    transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    store = InMemorySessionStore()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(source))
    if capture == "mirror":
        on_error = AsyncMock()
        batcher = TranscriptMirrorBatcher(store, str(source / "projects"), on_error)
        batcher.enqueue(str(transcript), [entry])
        await batcher.flush()
        on_error.assert_not_awaited()
    else:
        await import_session_to_store(SESSION_ID, store, directory=str(project))
    shutil.rmtree(source)
    source.mkdir()
    materialized = await materialize_resume_session(
        ClaudeAgentOptions(
            cwd=project,
            session_store=store,
            resume=None if continue_conversation else SESSION_ID,
            continue_conversation=continue_conversation,
            env={"CLAUDE_CONFIG_DIR": str(source)},
        )
    )
    assert materialized is not None
    try:
        restored = materialized.config_dir / "plans" / f"{SLUG}.md"
        assert restored.read_text(encoding="utf-8") == CONTENT
        assert not (restored.parent / "unrelated.md").exists()
        restored_transcript = (
            materialized.config_dir / "projects" / project_key / transcript.name
        )
        assert [
            json.loads(line) for line in restored_transcript.read_text().splitlines()
        ] == [entry]
        if os.name != "nt":
            assert restored.stat().st_mode & 0o077 == 0
    finally:
        await materialized.cleanup()
    assert not materialized.config_dir.exists()


@pytest.mark.anyio
async def test_latest_plan_update_and_deletion_are_restored(tmp_path):
    plans = tmp_path / "source" / "plans"
    plans.mkdir(parents=True)
    plan = plans / f"{SLUG}.md"
    entries = [{"type": "user", "slug": SLUG}]
    plan.write_text("First", encoding="utf-8")
    captured = snapshot_plan_files(entries, plans.parent)
    plan.write_text("Second", encoding="utf-8")
    captured += snapshot_plan_files(entries, plans.parent)
    target = tmp_path / "updated"
    restore_plan_files(captured, target)
    assert (target / "plans" / plan.name).read_text() == "Second"
    plan.unlink()
    captured += snapshot_plan_files(entries, plans.parent)
    deleted_target = tmp_path / "deleted"
    restore_plan_files(captured, deleted_target)
    assert not (deleted_target / "plans" / plan.name).exists()


@pytest.mark.parametrize(
    "slug",
    [
        "../escape",
        "/absolute",
        "a/b",
        "a\\b",
        "..",
        "",
        "a" * 201,
        "bad\n",
        "CON",
        "nul",
        "LPT1",
    ],
)
def test_unsafe_plan_names_are_ignored(tmp_path, slug):
    entry = {"type": "user", "slug": slug}
    assert snapshot_plan_files([entry], tmp_path) == [entry]
    assert (
        restore_plan_files(
            [{"type": PLAN_SNAPSHOT_TYPE, "slug": slug, "content": CONTENT}], tmp_path
        )
        == []
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("data", [b"\xff", b"x" * (MAX_PLAN_BYTES + 1)])
def test_invalid_or_oversized_plan_not_captured(tmp_path, data):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / f"{SLUG}.md").write_bytes(data)
    entry = {"type": "user", "slug": SLUG}
    assert snapshot_plan_files([entry], tmp_path) == [entry]


def test_symlinked_plan_not_captured(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("Do not persist", encoding="utf-8")
    plans = tmp_path / "plans"
    plans.mkdir()
    try:
        (plans / f"{SLUG}.md").symlink_to(secret)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    entry = {"type": "user", "slug": SLUG}
    assert snapshot_plan_files([entry], tmp_path) == [entry]


def test_symlinked_plan_directory_not_captured(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / f"{SLUG}.md").write_text("Other session", encoding="utf-8")
    try:
        (tmp_path / "plans").symlink_to(other, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    entry = {"type": "user", "slug": SLUG}
    assert snapshot_plan_files([entry], tmp_path) == [entry]


def test_plan_read_failure_keeps_transcript_batch(tmp_path, monkeypatch):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / f"{SLUG}.md").write_text(CONTENT, encoding="utf-8")

    def fail_stat(descriptor):
        raise OSError("read failed")

    monkeypatch.setattr(os, "fstat", fail_stat)
    entry = {"type": "user", "slug": SLUG}
    assert snapshot_plan_files([entry], tmp_path) == [entry]


@pytest.mark.parametrize("content", [42, {}, [], "x" * (MAX_PLAN_BYTES + 1)])
def test_invalid_snapshot_content_not_restored(tmp_path, content):
    assert (
        restore_plan_files(
            [{"type": PLAN_SNAPSHOT_TYPE, "slug": SLUG, "content": content}], tmp_path
        )
        == []
    )
    assert not (tmp_path / "plans").exists()


def test_old_transcripts_remain_unchanged(tmp_path):
    entries = [{"type": "user", "message": {"role": "user", "content": "Hello"}}]
    assert restore_plan_files(entries, tmp_path) == entries
    assert not (tmp_path / "plans").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("batch_size", [1, 500])
async def test_subagent_plan_import_and_restore(tmp_path, monkeypatch, batch_size):
    project = tmp_path / "project"
    project.mkdir()
    project_key = project_key_for_directory(project)
    config = tmp_path / "config"
    project_dir = config / "projects" / project_key
    subagents = project_dir / SESSION_ID / "subagents"
    subagents.mkdir(parents=True)
    main = {
        "type": "user",
        "uuid": "main",
        "message": {"role": "user", "content": "Plan"},
    }
    agent = {"type": "assistant", "uuid": "agent", "slug": SLUG}
    (project_dir / f"{SESSION_ID}.jsonl").write_text(json.dumps(main) + "\n")
    (subagents / "agent-test.jsonl").write_text(json.dumps(agent) + "\n")
    plans = config / "plans"
    plans.mkdir()
    (plans / f"{SLUG}.md").write_text(CONTENT, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    store = InMemorySessionStore()
    await import_session_to_store(
        SESSION_ID, store, directory=str(project), batch_size=batch_size
    )
    shutil.rmtree(config)
    config.mkdir()
    materialized = await materialize_resume_session(
        ClaudeAgentOptions(
            cwd=project,
            session_store=store,
            resume=SESSION_ID,
            env={"CLAUDE_CONFIG_DIR": str(config)},
        )
    )
    assert materialized is not None
    try:
        assert (materialized.config_dir / "plans" / f"{SLUG}.md").read_text(
            encoding="utf-8"
        ) == CONTENT
        transcript = (
            materialized.config_dir
            / "projects"
            / project_key
            / SESSION_ID
            / "subagents"
            / "agent-test.jsonl"
        )
        assert [json.loads(line) for line in transcript.read_text().splitlines()] == [
            agent
        ]
    finally:
        await materialized.cleanup()


@pytest.mark.parametrize("content", ["\ud800", 1, {}])
def test_malformed_snapshot_does_not_replace_valid_plan(tmp_path, content):
    valid = {"type": PLAN_SNAPSHOT_TYPE, "slug": SLUG, "content": CONTENT}
    invalid = {"type": PLAN_SNAPSHOT_TYPE, "slug": SLUG, "content": content}
    restore_plan_files([valid, invalid], tmp_path)
    assert (tmp_path / "plans" / f"{SLUG}.md").read_text(encoding="utf-8") == CONTENT


def test_embedded_file_snapshot_and_slug_inheritance(tmp_path):
    entries = [
        {"type": "user", "slug": SLUG},
        {
            "type": "system",
            "subtype": "file_snapshot",
            "snapshotFiles": [
                {"key": "plan", "path": "/previous-host/plan.md", "content": CONTENT},
                {"key": "workshop", "content": "Not the plan"},
            ],
        },
    ]
    assert restore_plan_files(entries, tmp_path) == entries
    assert (tmp_path / "plans" / f"{SLUG}.md").read_text(encoding="utf-8") == CONTENT


def test_tombstone_removes_previously_restored_plan(tmp_path):
    restore_plan_files(
        [{"type": PLAN_SNAPSHOT_TYPE, "slug": SLUG, "content": CONTENT}], tmp_path
    )
    restore_plan_files(
        [{"type": PLAN_SNAPSHOT_TYPE, "slug": SLUG, "content": None}], tmp_path
    )
    assert not (tmp_path / "plans" / f"{SLUG}.md").exists()


def test_empty_plan_is_preserved(tmp_path):
    plans = tmp_path / "source" / "plans"
    plans.mkdir(parents=True)
    (plans / f"{SLUG}.md").write_text("")
    captured = snapshot_plan_files([{"type": "user", "slug": SLUG}], plans.parent)
    restore_plan_files(captured, tmp_path / "target")
    assert (tmp_path / "target" / "plans" / f"{SLUG}.md").read_text() == ""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Requires FIFO support")
def test_nonregular_plan_is_not_read(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    os.mkfifo(plans / f"{SLUG}.md")
    entry = {"type": "user", "slug": SLUG}
    assert snapshot_plan_files([entry], tmp_path) == [entry]


@pytest.mark.anyio
async def test_plan_restore_failure_cleans_temporary_directory(tmp_path, monkeypatch):
    from claude_agent_sdk._internal import session_resume

    project = tmp_path / "project"
    project.mkdir()
    store = InMemorySessionStore()
    await store.append(
        {"project_key": project_key_for_directory(project), "session_id": SESSION_ID},
        [{"type": PLAN_SNAPSHOT_TYPE, "slug": SLUG, "content": CONTENT}],
    )
    temporary = tmp_path / "materialized"
    temporary.mkdir()
    monkeypatch.setattr(
        session_resume.tempfile, "mkdtemp", lambda **kwargs: str(temporary)
    )

    def fail_restore(*args):
        (temporary / "partial-plan").write_text("partial")
        raise OSError("disk unavailable")

    monkeypatch.setattr(session_resume, "restore_plan_files", fail_restore)
    with pytest.raises(OSError, match="disk unavailable"):
        await materialize_resume_session(
            ClaudeAgentOptions(cwd=project, session_store=store, resume=SESSION_ID)
        )
    assert not temporary.exists()
