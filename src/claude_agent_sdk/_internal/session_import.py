"""Replay a local on-disk session transcript into a :class:`SessionStore`.

This is the inverse of :mod:`session_resume` — where ``materialize_resume_session``
reads a store and writes a temp ``~/.claude`` tree, ``import_session_to_store``
reads the local ``~/.claude/projects/<dir>/<sessionId>.jsonl`` (plus subagent
transcripts) and replays each line into ``store.append()``.

Mirrors the TypeScript SDK's ``importSessionToStore``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from ..types import SessionKey, SessionStore, SessionStoreEntry
from .sessions import (
    _read_agent_metadata_sidecar,
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

    Every transcript is checked for valid JSON before the first append, so a
    corrupt or truncated file leaves the store unchanged.

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
        ValueError: If ``session_id`` is not a valid UUID, or a transcript
            contains invalid JSON (the error names the file and line).
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
    subagents_dir = resolved.with_suffix("") / "subagents"
    to_check = [resolved]
    if include_subagents:
        to_check.extend(_collect_jsonl_files(subagents_dir))
    # Import only the bytes that were validated, so lines a live writer appends
    # in between are left for the next catch-up instead of failing mid-import.
    validated = {file_path: _validate_jsonl_file(file_path) for file_path in to_check}

    await _append_jsonl_file_in_batches(
        resolved, main_key, store, batch_size, validated[resolved]
    )

    if not include_subagents:
        return

    # Subagent transcripts live at <projectDir>/<sessionId>/subagents/**.
    session_dir = resolved.with_suffix("")
    for file_path in to_check[1:]:
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
        await _append_jsonl_file_in_batches(
            file_path, sub_key, store, batch_size, validated[file_path]
        )

        # The on-disk .jsonl does NOT contain agent_metadata entries — those
        # are only sent to live mirrors and persisted in the .meta.json
        # sidecar. Import the sidecar so materialize_resume_session() can
        # recreate it and resumed subagents keep their agentType/worktreePath.
        # A missing, corrupt, or non-object sidecar is treated as absent (the
        # transcript is still imported); other read errors propagate.
        meta = _read_agent_metadata_sidecar(file_path)
        if meta is not None:
            # Synthetic discriminator last so a stray "type" key in the
            # CLI-owned sidecar can never shadow it.
            meta_entry = cast(SessionStoreEntry, {**meta, "type": "agent_metadata"})
            await store.append(sub_key, [meta_entry])


def _validate_jsonl_file(file_path: Path) -> int:
    """Raise ``ValueError`` naming the file and line if any non-blank line of
    ``file_path`` is not valid JSON. Returns the number of bytes validated."""
    size = 0
    with file_path.open("rb") as f:
        for line_no, raw in enumerate(f, start=1):
            size += len(raw)
            line = raw.rstrip(b"\r\n")
            if not line:
                continue
            try:
                json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ValueError(
                    f"Invalid JSON in {file_path} at line {line_no}"
                ) from e
    return size


async def _append_jsonl_file_in_batches(
    file_path: Path,
    key: SessionKey,
    store: SessionStore,
    batch_size: int,
    limit: int,
) -> None:
    """Stream-read the first ``limit`` bytes of a JSONL file line-by-line,
    parsing each line and flushing to ``store.append()`` in batches of
    ``batch_size`` entries (or ``MAX_PENDING_BYTES`` of line text, whichever
    comes first). Skips blank lines."""
    batch: list[SessionStoreEntry] = []
    nbytes = 0
    consumed = 0
    with file_path.open("rb") as f:
        for raw in f:
            if consumed >= limit:
                break
            raw = raw[: limit - consumed]
            consumed += len(raw)
            line = raw.rstrip(b"\r\n").decode("utf-8")
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
