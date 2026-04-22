"""Replay a local on-disk session transcript into a :class:`SessionStore`.

This is the inverse of :mod:`session_resume` — where ``materialize_resume_session``
reads a store and writes a temp ``~/.claude`` tree, ``import_session_to_store``
reads the local ``~/.claude/projects/<dir>/<sessionId>.jsonl`` (plus subagent
transcripts) and replays each line into ``store.append()``.

Mirrors the TypeScript SDK's ``importSessionToStore``.
"""

from __future__ import annotations

import errno
import json
from collections.abc import Iterator
from pathlib import Path

from ..types import SessionKey, SessionStore, SessionStoreEntry
from .sessions import (
    _resolve_session_file_path,
    _validate_uuid,
)
from .transcript_mirror_batcher import MAX_PENDING_BYTES, MAX_PENDING_ENTRIES

__all__ = ["import_session_to_store"]


async def import_session_to_store(
    session_id: str,
    store: SessionStore,
    *,
    directory: str | None = None,
    include_subagents: bool = True,
    batch_size: int = MAX_PENDING_ENTRIES,
) -> None:
    """Replay a local session transcript into a :class:`SessionStore`.

    Streams the on-disk JSONL line-by-line and calls ``store.append(key, batch)``
    every ``batch_size`` entries (or 1 MiB of line bytes, whichever comes
    first). Useful for migrating existing local sessions to a remote store, or
    for catching a store up after a :class:`MirrorErrorMessage` indicated a
    live-mirror gap. Adapters should treat ``entry["uuid"]`` as an idempotency
    key so re-import is duplicate-safe.

    The destination ``project_key`` is the name of the on-disk project
    directory the session file was found in — the same key
    :func:`file_path_to_session_key` (and thus ``TranscriptMirrorBatcher``)
    would have produced for the same file — so an imported session is
    indistinguishable from a live-mirrored one and resumable via
    ``query(options=ClaudeAgentOptions(session_store=store, resume=session_id))``
    from the original ``cwd``.

    Args:
        session_id: UUID of the session to import.
        store: Destination :class:`SessionStore`.
        directory: Project directory path (same semantics as
            :func:`list_sessions`). When omitted, all project directories are
            searched for the session file.
        include_subagents: If ``True`` (default), also import subagent
            transcripts under ``<sessionId>/subagents/**`` and their
            ``.meta.json`` sidecars.
        batch_size: Maximum entries per ``store.append()`` call. Default 500.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID.
        FileNotFoundError: If the session JSONL cannot be found on disk.
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")

    resolved = _resolve_session_file_path(session_id, directory)
    if resolved is None:
        raise FileNotFoundError(f"Session {session_id} not found")

    # Key under the on-disk project directory name — matches
    # file_path_to_session_key() / TranscriptMirrorBatcher even when the
    # resolver's search (directory=None) or worktree fallback found the file
    # somewhere other than `directory`.
    project_key = resolved.parent.name
    if batch_size <= 0:
        batch_size = MAX_PENDING_ENTRIES

    main_key: SessionKey = {"project_key": project_key, "session_id": session_id}
    await _append_jsonl_file_in_batches(resolved, main_key, store, batch_size)

    if not include_subagents:
        return

    # Subagent transcripts live at <projectDir>/<sessionId>/subagents/**.
    session_dir = resolved.with_suffix("")
    subagents_dir = session_dir / "subagents"
    for file_path in _collect_jsonl_files(subagents_dir):
        # subpath is the path relative to session_dir, '/'-joined, sans .jsonl —
        # e.g. subagents/agent-abc or subagents/workflows/run-1/agent-def.
        # Matches file_path_to_session_key() so list_subkeys() and
        # get_subagent_messages_from_store() round-trip.
        rel_parts = list(file_path.relative_to(session_dir).parts)
        rel_parts[-1] = rel_parts[-1][: -len(".jsonl")]
        sub_key: SessionKey = {
            "project_key": project_key,
            "session_id": session_id,
            "subpath": "/".join(rel_parts),
        }
        await _append_jsonl_file_in_batches(file_path, sub_key, store, batch_size)

        # The on-disk .jsonl does NOT contain agent_metadata entries — those
        # are only sent to live mirrors and persisted in the .meta.json
        # sidecar. Import the sidecar so materialize_resume_session() can
        # recreate it and resumed subagents keep their agentType/worktreePath.
        meta_path = file_path.with_name(file_path.name[: -len(".jsonl")] + ".meta.json")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        else:
            meta_entry: SessionStoreEntry = {"type": "agent_metadata"}
            meta_entry.update(meta)
            await store.append(sub_key, [meta_entry])


async def _append_jsonl_file_in_batches(
    file_path: Path,
    key: SessionKey,
    store: SessionStore,
    batch_size: int,
) -> None:
    """Stream-read a JSONL file line-by-line, parsing each line and flushing to
    ``store.append()`` in batches of ``batch_size`` entries (or
    ``MAX_PENDING_BYTES`` of line text, whichever comes first). Skips blank
    lines."""
    batch: list[SessionStoreEntry] = []
    nbytes = 0
    with file_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            batch.append(json.loads(line))
            nbytes += len(line)
            if len(batch) >= batch_size or nbytes >= MAX_PENDING_BYTES:
                await store.append(key, batch)
                batch = []
                nbytes = 0
    if batch:
        await store.append(key, batch)


def _collect_jsonl_files(base_dir: Path) -> Iterator[Path]:
    """Recursively yield all ``*.jsonl`` file paths under ``base_dir``.

    Yields nothing if ``base_dir`` does not exist. Sorted per directory so
    import order is deterministic across platforms.
    """
    try:
        dirents = sorted(base_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return
    for entry in dirents:
        if entry.is_dir():
            yield from _collect_jsonl_files(entry)
        elif entry.is_file() and entry.name.endswith(".jsonl"):
            yield entry
