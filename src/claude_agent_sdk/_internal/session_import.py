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
from tempfile import TemporaryDirectory
from typing import cast

from ..types import SessionKey, SessionStore, SessionStoreEntry
from .sessions import (
    _read_agent_metadata_sidecar,
    _resolve_session_file_path,
    _validate_uuid,
)
from .transcript_mirror_batcher import MAX_PENDING_BYTES, MAX_PENDING_ENTRIES

__all__ = ["SessionImportError", "import_session_to_store"]


class SessionImportError(ValueError):
    """Raised when a transcript cannot be validated before import."""

    def __init__(self, file_path: Path, line_number: int, message: str) -> None:
        self.file_path = file_path
        self.line_number = line_number
        super().__init__(
            f"Invalid transcript {file_path} at line {line_number}: {message}"
        )


async def import_session_to_store(
    session_id: str,
    store: SessionStore,
    *,
    directory: str | None = None,
    include_subagents: bool = True,
    batch_size: int = MAX_PENDING_ENTRIES,
    atomic: bool = True,
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
        atomic: If ``True`` (default), validate every transcript before the
            first store write. This prevents a malformed later line from
            leaving earlier batches in the destination. Set to ``False`` for
            the previous streaming behavior.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID.
        FileNotFoundError: If the session JSONL cannot be found on disk.
        SessionImportError: If a transcript contains invalid JSON in atomic
            mode.
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

    files: list[tuple[Path, SessionKey]] = []
    main_key: SessionKey = {"project_key": project_key, "session_id": session_id}
    files.append((resolved, main_key))

    if include_subagents:
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
            files.append((file_path, sub_key))

    if atomic:
        with TemporaryDirectory(prefix="claude-session-import-") as staging_dir:
            staged = [
                (
                    key,
                    _stage_jsonl_file(file_path, Path(staging_dir) / f"{index}.jsonl"),
                )
                for index, (file_path, key) in enumerate(files)
            ]
            staged_metadata: list[tuple[SessionKey, SessionStoreEntry]] = []
            if include_subagents:
                for file_path, sub_key in files[1:]:
                    meta = _read_agent_metadata_sidecar(file_path)
                    if meta is not None:
                        meta_entry = cast(
                            SessionStoreEntry, {**meta, "type": "agent_metadata"}
                        )
                        staged_metadata.append((sub_key, meta_entry))
            for key, staged_file in staged:
                await _append_jsonl_file_in_batches(staged_file, key, store, batch_size)
            for sub_key, meta_entry in staged_metadata:
                await store.append(sub_key, [meta_entry])
        return

    for file_path, sub_key in files:
        await _append_jsonl_file_in_batches(file_path, sub_key, store, batch_size)
        if file_path == resolved or not include_subagents:
            continue

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


def _stage_jsonl_file(file_path: Path, staged_path: Path) -> Path:
    """Validate a transcript and stage normalized entries before import."""
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        file_path.open(encoding="utf-8") as stream,
        staged_path.open("w", encoding="utf-8") as staged,
    ):
        for line_number, line in enumerate(stream, start=1):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise SessionImportError(file_path, line_number, error.msg) from error
            if not isinstance(entry, dict):
                raise SessionImportError(
                    file_path, line_number, "JSON entry must be an object"
                )
            staged.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return staged_path


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
