"""Portable session mutation functions for the Agent SDK.

Rename/tag append typed metadata entries to the session's JSONL (matching
the CLI pattern); delete removes the JSONL file; fork creates a new session
with UUID remapping. Safe to call from any SDK host process — see
concurrent-writer note below.

Directory resolution matches list_sessions / get_session_messages:
``directory`` is the project path (not the storage dir); when omitted, all
project directories are searched for the session file.

Concurrent writers: if the target session is currently open in a CLI
process, the CLI's reAppendSessionMetadata() tail-reads before re-appending
its cached metadata. If an SDK write (e.g. a custom-title entry) is in the
tail scan window, the CLI absorbs it into its cache and re-appends the SDK
value — not the stale CLI value.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import unicodedata
import uuid as uuid_mod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from ..types import SessionKey, SessionStore, SessionStoreEntry
from .session_store_validation import _store_implements
from .sessions import (
    LITE_READ_BUF_SIZE,
    _canonicalize_path,
    _extract_first_prompt_from_head,
    _extract_last_json_string_field,
    _find_project_dir,
    _get_projects_dir,
    _get_worktree_paths,
    _validate_uuid,
    project_key_for_directory,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rename_session(
    session_id: str,
    title: str,
    directory: str | None = None,
) -> None:
    """Rename a session by appending a custom-title entry.

    ``list_sessions`` reads the LAST custom-title from the file tail, so
    repeated calls are safe — the most recent wins.

    Args:
        session_id: UUID of the session to rename.
        title: New session title. Leading/trailing whitespace is stripped.
            Must be non-empty after stripping.
        directory: Project directory path (same semantics as
            ``list_sessions(directory=...)``). When omitted, all project
            directories are searched for the session file.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID, or if ``title``
            is empty/whitespace-only.
        FileNotFoundError: If the session file cannot be found.

    See Also:
        :func:`rename_session_via_store` for the :class:`SessionStore`-backed
        async variant.

    Example:
        Rename a session in a specific project::

            rename_session(
                "550e8400-e29b-41d4-a716-446655440000",
                "My refactoring session",
                directory="/path/to/project",
            )
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    # Matches CLI guard — empty/whitespace titles are rejected rather than
    # overloaded as "clear title".
    stripped = title.strip()
    if not stripped:
        raise ValueError("title must be non-empty")

    data = (
        json.dumps(
            {
                "type": "custom-title",
                "customTitle": stripped,
                "sessionId": session_id,
            },
            separators=(",", ":"),
        )
        + "\n"
    )

    _append_to_session(session_id, data, directory)


def tag_session(
    session_id: str,
    tag: str | None,
    directory: str | None = None,
) -> None:
    """Tag a session. Pass ``None`` to clear the tag.

    Appends a ``{type:'tag',tag:<tag>,sessionId:<id>}`` JSONL entry.
    ``list_sessions`` reads the LAST tag from the file tail — most recent
    wins. Passing ``None`` appends an empty-string tag entry which
    ``list_sessions`` treats as ``None`` (cleared).

    Tags are Unicode-sanitized before storing (removes zero-width chars,
    directional marks, private-use characters, etc.) for CLI filter
    compatibility.

    Args:
        session_id: UUID of the session to tag.
        tag: Tag string, or ``None`` to clear. Leading/trailing whitespace
            is stripped. Must be non-empty after sanitization and stripping
            (unless ``None``).
        directory: Project directory path (same semantics as
            ``list_sessions(directory=...)``). When omitted, all project
            directories are searched for the session file.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID, or if ``tag`` is
            empty/whitespace-only after sanitization.
        FileNotFoundError: If the session file cannot be found.

    See Also:
        :func:`tag_session_via_store` for the :class:`SessionStore`-backed
        async variant.

    Example:
        Tag a session::

            tag_session(
                "550e8400-e29b-41d4-a716-446655440000",
                "experiment",
                directory="/path/to/project",
            )

        Clear a tag::

            tag_session(session_id, None)
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    if tag is not None:
        sanitized = _sanitize_unicode(tag).strip()
        if not sanitized:
            raise ValueError("tag must be non-empty (use None to clear)")
        tag = sanitized

    data = (
        json.dumps(
            {
                "type": "tag",
                "tag": tag if tag is not None else "",
                "sessionId": session_id,
            },
            separators=(",", ":"),
        )
        + "\n"
    )

    _append_to_session(session_id, data, directory)


def delete_session(
    session_id: str,
    directory: str | None = None,
) -> None:
    """Delete a session by removing its JSONL file and subagent transcripts.

    This is a hard delete — the ``{session_id}.jsonl`` file is removed
    permanently, along with the sibling ``{session_id}/`` subdirectory that
    holds subagent transcripts (if it exists). SDK users who need soft-delete
    semantics can use ``tag_session(id, '__hidden')`` and filter on listing
    instead.

    Args:
        session_id: UUID of the session to delete.
        directory: Project directory path (same semantics as
            ``list_sessions(directory=...)``). When omitted, all project
            directories are searched for the session file.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID.
        FileNotFoundError: If the session file cannot be found.

    See Also:
        :func:`delete_session_via_store` for the :class:`SessionStore`-backed
        async variant.

    Example:
        Delete a session::

            delete_session("550e8400-e29b-41d4-a716-446655440000")
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")

    path = _find_session_file(session_id, directory)
    if path is None:
        raise FileNotFoundError(
            f"Session {session_id} not found"
            + (f" in project directory for {directory}" if directory else "")
        )
    try:
        path.unlink()
    except OSError as e:
        if e.errno == errno.ENOENT:
            raise FileNotFoundError(f"Session {session_id} not found") from e
        raise
    # Subagent transcripts live in a sibling {session_id}/ dir; often absent.
    shutil.rmtree(path.parent / session_id, ignore_errors=True)


@dataclass
class ForkSessionResult:
    """Result of a fork operation."""

    session_id: str
    """UUID of the new forked session."""


def fork_session(
    session_id: str,
    directory: str | None = None,
    up_to_message_id: str | None = None,
    title: str | None = None,
) -> ForkSessionResult:
    """Fork a session into a new branch with fresh UUIDs.

    Copies transcript messages from the source session into a new session
    file, remapping every message UUID and preserving the ``parentUuid``
    chain. Supports ``up_to_message_id`` for branching from a specific
    point in the conversation.

    Forked sessions start without undo history (file-history snapshots are
    not copied).

    Args:
        session_id: UUID of the source session to fork.
        directory: Project directory path (same semantics as
            ``list_sessions(directory=...)``). When omitted, all project
            directories are searched for the session file.
        up_to_message_id: Slice transcript up to this message UUID
            (inclusive). If omitted, copies the full transcript.
        title: Custom title for the fork. If omitted, derives from
            the original title + " (fork)".

    Returns:
        ``ForkSessionResult`` with the new session's UUID.

    Raises:
        ValueError: If ``session_id`` or ``up_to_message_id`` is not a
            valid UUID.
        FileNotFoundError: If the source session file cannot be found.
        ValueError: If the session has no messages to fork, or if
            ``up_to_message_id`` is not found in the transcript.

    See Also:
        :func:`fork_session_via_store` for the :class:`SessionStore`-backed
        async variant.

    Example:
        Fork a session::

            result = fork_session("550e8400-e29b-41d4-a716-446655440000")
            print(result.session_id)

        Fork from a specific point::

            result = fork_session(
                "550e8400-e29b-41d4-a716-446655440000",
                up_to_message_id="660e8400-e29b-41d4-a716-446655440001",
            )
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    if up_to_message_id and not _validate_uuid(up_to_message_id):
        raise ValueError(f"Invalid up_to_message_id: {up_to_message_id}")

    source = _find_session_file_with_dir(session_id, directory)
    if source is None:
        raise FileNotFoundError(
            f"Session {session_id} not found"
            + (f" in project directory for {directory}" if directory else "")
        )
    file_path, project_dir = source

    content = file_path.read_bytes()
    if not content:
        raise ValueError(f"Session {session_id} has no messages to fork")

    transcript, content_replacements = _parse_fork_transcript(content, session_id)

    def _derive_title() -> str | None:
        buf_len = len(content)
        head = content[: min(buf_len, LITE_READ_BUF_SIZE)].decode(
            "utf-8", errors="replace"
        )
        tail = content[max(0, buf_len - LITE_READ_BUF_SIZE) :].decode(
            "utf-8", errors="replace"
        )
        return (
            _extract_last_json_string_field(tail, "customTitle")
            or _extract_last_json_string_field(head, "customTitle")
            or _extract_last_json_string_field(tail, "aiTitle")
            or _extract_last_json_string_field(head, "aiTitle")
            or _extract_first_prompt_from_head(head)
            or None
        )

    forked_session_id, lines = _build_fork_lines(
        transcript,
        content_replacements,
        session_id,
        up_to_message_id,
        title,
        _derive_title,
    )

    fork_path = project_dir / f"{forked_session_id}.jsonl"
    fd = os.open(fork_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, ("\n".join(lines) + "\n").encode("utf-8"))
    finally:
        os.close(fd)

    return ForkSessionResult(session_id=forked_session_id)


def _build_fork_lines(
    transcript: list[dict[str, Any]],
    content_replacements: list[Any],
    session_id: str,
    up_to_message_id: str | None,
    title: str | None,
    derive_title: Callable[[], str | None],
) -> tuple[str, list[str]]:
    """Core fork transform — remap UUIDs and produce serialized JSONL lines.

    Shared by the filesystem and SessionStore-backed paths. Returns
    ``(forked_session_id, lines)`` where each line is a compact JSON string
    without a trailing newline.

    ``derive_title`` is invoked only when no explicit ``title`` is given,
    so the disk path's head/tail byte scan and the store path's entry scan
    only run when needed.
    """
    # Filter out sidechains (subagent sessions with separate parentUuid
    # graphs). Keep isMeta entries — they're interleaved in the main chain.
    transcript = [e for e in transcript if not e.get("isSidechain")]

    if not transcript:
        raise ValueError(f"Session {session_id} has no messages to fork")

    if up_to_message_id:
        cutoff = -1
        for i, entry in enumerate(transcript):
            if entry.get("uuid") == up_to_message_id:
                cutoff = i
                break
        if cutoff == -1:
            raise ValueError(
                f"Message {up_to_message_id} not found in session {session_id}"
            )
        transcript = transcript[: cutoff + 1]

    # Include progress entries in the mapping — needed for parentUuid chain walk.
    uuid_mapping: dict[str, str] = {}
    for entry in transcript:
        uuid_mapping[entry["uuid"]] = str(uuid_mod.uuid4())

    # Filter out progress messages from written output. They're UI-only
    # chain links; not needed in a fresh fork.
    writable = [e for e in transcript if e.get("type") != "progress"]
    if not writable:
        raise ValueError(f"Session {session_id} has no messages to fork")

    by_uuid: dict[str, dict[str, Any]] = {}
    for entry in transcript:
        by_uuid[entry["uuid"]] = entry

    forked_session_id = str(uuid_mod.uuid4())

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lines: list[str] = []

    for i, original in enumerate(writable):
        new_uuid = uuid_mapping[original["uuid"]]

        # Resolve parentUuid, skipping progress ancestors.
        new_parent_uuid: str | None = None
        parent_id: str | None = original.get("parentUuid")
        while parent_id:
            parent = by_uuid.get(parent_id)
            if not parent:
                break
            if parent.get("type") != "progress":
                new_parent_uuid = uuid_mapping.get(parent_id)
                break
            parent_id = parent.get("parentUuid")

        # Only update timestamp on the last message (leaf detection on resume).
        timestamp = now if i == len(writable) - 1 else original.get("timestamp", now)

        # Remap logicalParentUuid (compact-boundary backpointer).
        logical_parent = original.get("logicalParentUuid")
        new_logical_parent = (
            uuid_mapping.get(logical_parent) if logical_parent else logical_parent
        )

        forked = {
            **original,
            "uuid": new_uuid,
            "parentUuid": new_parent_uuid,
            "logicalParentUuid": new_logical_parent,
            "sessionId": forked_session_id,
            "timestamp": timestamp,
            # Clear session-specific fields from the spread
            "isSidechain": False,
            "forkedFrom": {
                "sessionId": session_id,
                "messageUuid": original["uuid"],
            },
        }
        # Remove fields that would leak state from the source session
        for key in ("teamName", "agentName", "slug", "sourceToolAssistantUUID"):
            forked.pop(key, None)

        lines.append(json.dumps(forked, separators=(",", ":")))

    # Append content-replacement entry (if any) with the fork's sessionId.
    if content_replacements:
        lines.append(
            json.dumps(
                {
                    "type": "content-replacement",
                    "sessionId": forked_session_id,
                    "replacements": content_replacements,
                    "uuid": str(uuid_mod.uuid4()),
                    "timestamp": now,
                },
                separators=(",", ":"),
            )
        )

    # Derive title: explicit > original customTitle > original aiTitle > first
    # prompt. Suffix with " (fork)" for derived titles. listSessions reads the
    # LAST custom-title from the tail, so this entry is what surfaces.
    fork_title = title.strip() if title else None
    if not fork_title:
        fork_title = f"{derive_title() or 'Forked session'} (fork)"

    lines.append(
        json.dumps(
            {
                "type": "custom-title",
                "sessionId": forked_session_id,
                "customTitle": fork_title,
                "uuid": str(uuid_mod.uuid4()),
                "timestamp": now,
            },
            separators=(",", ":"),
        )
    )

    return forked_session_id, lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_session_file(
    session_id: str,
    directory: str | None,
) -> Path | None:
    """Find the path to a session's JSONL file.

    Returns the path if found, None otherwise.
    """
    result = _find_session_file_with_dir(session_id, directory)
    return result[0] if result else None


def _find_session_file_with_dir(
    session_id: str,
    directory: str | None,
) -> tuple[Path, Path] | None:
    """Find a session file and its containing project directory.

    Returns ``(file_path, project_dir)`` or None. The fork operation
    needs the project dir to write the new file adjacent to the source.
    """
    file_name = f"{session_id}.jsonl"

    def _try_dir(project_dir: Path) -> tuple[Path, Path] | None:
        path = project_dir / file_name
        try:
            st = path.stat()
            if st.st_size > 0:
                return (path, project_dir)
        except OSError:
            pass
        return None

    if directory:
        canonical = _canonicalize_path(directory)
        project_dir = _find_project_dir(canonical)
        if project_dir is not None:
            result = _try_dir(project_dir)
            if result:
                return result

        try:
            worktree_paths = _get_worktree_paths(canonical)
        except Exception:
            worktree_paths = []
        for wt in worktree_paths:
            if wt == canonical:
                continue
            wt_project_dir = _find_project_dir(wt)
            if wt_project_dir is not None:
                result = _try_dir(wt_project_dir)
                if result:
                    return result
        return None

    projects_dir = _get_projects_dir()
    try:
        dirents = list(projects_dir.iterdir())
    except OSError:
        return None
    for entry in dirents:
        result = _try_dir(entry)
        if result:
            return result
    return None


_TRANSCRIPT_TYPES = frozenset({"user", "assistant", "attachment", "system", "progress"})


def _derive_title_from_entries(raw: list[Any]) -> str | None:
    """Mirror the disk path's head/tail title scan over parsed entry objects.

    Precedence matches ``_extract_last_json_string_field`` semantics: last
    occurrence wins for both ``customTitle`` and ``aiTitle``; ``customTitle``
    beats ``aiTitle``; first user prompt is the final fallback.
    """
    custom: str | None = None
    ai: str | None = None
    for e in raw:
        if not isinstance(e, dict):
            continue
        ct = e.get("customTitle")
        if isinstance(ct, str) and ct:
            custom = ct
        at = e.get("aiTitle")
        if isinstance(at, str) and at:
            ai = at
    if custom:
        return custom
    if ai:
        return ai
    # First-prompt fallback — reuse the head extractor over a re-serialized
    # JSONL string so skip-patterns/truncation match the disk path exactly.
    jsonl = "\n".join(json.dumps(e, separators=(",", ":")) for e in raw) + "\n"
    return _extract_first_prompt_from_head(jsonl) or None


def _parse_fork_transcript(
    content: bytes, session_id: str
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Parse JSONL content into transcript entries + content-replacement records.

    Only keeps entries that have a uuid and are transcript message types.
    Content-replacement entries are collected for re-emission in the fork.
    """
    transcript: list[dict[str, Any]] = []
    content_replacements: list[Any] = []

    for line in content.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type in _TRANSCRIPT_TYPES and isinstance(entry.get("uuid"), str):
            transcript.append(entry)
        elif (
            entry_type == "content-replacement"
            and entry.get("sessionId") == session_id
            and isinstance(entry.get("replacements"), list)
        ):
            content_replacements.extend(entry["replacements"])

    return transcript, content_replacements


def _append_to_session(
    session_id: str,
    data: str,
    directory: str | None,
) -> None:
    """Append data to an existing session file.

    Searches candidate paths and tries the append directly — no existence
    check. Uses O_WRONLY | O_APPEND (without O_CREAT) so the open fails with
    ENOENT for missing files, avoiding TOCTOU.
    """
    file_name = f"{session_id}.jsonl"

    if directory:
        canonical = _canonicalize_path(directory)

        # Try the exact/prefix-matched project directory first.
        project_dir = _find_project_dir(canonical)
        if project_dir is not None and _try_append(project_dir / file_name, data):
            return

        # Worktree fallback — matches list_sessions/get_session_messages.
        # Sessions may live under a different worktree root.
        try:
            worktree_paths = _get_worktree_paths(canonical)
        except Exception:
            worktree_paths = []
        for wt in worktree_paths:
            if wt == canonical:
                continue  # already tried above
            wt_project_dir = _find_project_dir(wt)
            if wt_project_dir is not None and _try_append(
                wt_project_dir / file_name, data
            ):
                return

        raise FileNotFoundError(
            f"Session {session_id} not found in project directory for {directory}"
        )

    # No directory — search all project directories by trying each directly.
    projects_dir = _get_projects_dir()
    try:
        dirents = list(projects_dir.iterdir())
    except OSError as e:
        raise FileNotFoundError(
            f"Session {session_id} not found (no projects directory)"
        ) from e
    for entry in dirents:
        if _try_append(entry / file_name, data):
            return
    raise FileNotFoundError(f"Session {session_id} not found in any project directory")


def _try_append(path: Path, data: str) -> bool:
    """Try appending to a path.

    Opens with O_WRONLY | O_APPEND (no O_CREAT), so the open fails with
    ENOENT if the file does not exist — no separate existence check.

    Returns ``True`` on successful write, ``False`` if the file does not
    exist (ENOENT/ENOTDIR) or is 0-byte. A 0-byte ``.jsonl`` is a "session
    not here, keep searching" signal that readers (``_read_session_lite``)
    already honor; without this guard the search would stop at an empty stub
    in one project dir while the real file lives in a worktree. Re-raises all
    other errors (ENOSPC, EACCES, EIO, etc.) so real write failures surface.

    O_APPEND semantics: Python's ``os.open`` with ``os.O_APPEND`` maps to the
    kernel's append mode on all platforms. On POSIX, O_APPEND makes the kernel
    atomically seek-to-EOF on every write (race-free). On Windows, CPython's
    ``os.open`` translates O_APPEND to ``FILE_APPEND_DATA`` (also atomic).
    CPython handles this correctly on all platforms, so no explicit-position
    fallback is needed.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError as e:
        if e.errno in (errno.ENOENT, errno.ENOTDIR):
            return False
        raise
    try:
        stat = os.fstat(fd)
        if stat.st_size == 0:
            return False
        os.write(fd, data.encode("utf-8"))
        return True
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Unicode sanitization
# ---------------------------------------------------------------------------

# Explicit ranges for dangerous Unicode characters. Python's regex supports
# Unicode categories via \p{} only in the third-party `regex` module, so we
# use explicit ranges here (matching the TS fallback paths).
_UNICODE_STRIP_RE = re.compile(
    "["
    "\u200b-\u200f"  # Zero-width spaces, LTR/RTL marks
    "\u202a-\u202e"  # Directional formatting characters
    "\u2066-\u2069"  # Directional isolates
    "\ufeff"  # Byte order mark
    "\ue000-\uf8ff"  # Basic Multilingual Plane private use
    "]"
)

# Format characters (Cf category) — the ones most commonly abused for
# injection. We check this per-character since Python's re module doesn't
# support \p{Cf} without the third-party regex module.
_FORMAT_CATEGORIES = frozenset({"Cf", "Co", "Cn"})


def _sanitize_unicode(value: str) -> str:
    """Sanitize a string by removing dangerous Unicode characters.

    Iteratively applies NFKC
    normalization and strips format/private-use/unassigned characters until
    no more changes occur (max 10 iterations).
    """
    current = value
    for _ in range(10):
        previous = current
        # Apply NFKC normalization to handle composed character sequences
        current = unicodedata.normalize("NFKC", current)
        # Strip Cf (format), Co (private use), Cn (unassigned) categories
        current = "".join(
            c for c in current if unicodedata.category(c) not in _FORMAT_CATEGORIES
        )
        # Explicit ranges (redundant with category check but matches TS)
        current = _UNICODE_STRIP_RE.sub("", current)
        if current == previous:
            break
    return current


# ---------------------------------------------------------------------------
# SessionStore-backed implementations
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def rename_session_via_store(
    session_store: SessionStore,
    session_id: str,
    title: str,
    directory: str | None = None,
) -> None:
    """Rename a session by appending a custom-title entry to a :class:`SessionStore`.

    Async, store-backed counterpart to :func:`rename_session`.

    Args:
        session_store: The store to write to.
        session_id: UUID of the session to rename.
        title: New session title. Leading/trailing whitespace is stripped.
            Must be non-empty after stripping.
        directory: Project directory used to compute the ``project_key``.
            Defaults to the current working directory.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID, or if ``title``
            is empty/whitespace-only.
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    stripped = title.strip()
    if not stripped:
        raise ValueError("title must be non-empty")
    project_key = project_key_for_directory(directory)
    key: SessionKey = {"project_key": project_key, "session_id": session_id}
    entry: dict[str, Any] = {
        "type": "custom-title",
        "customTitle": stripped,
        "sessionId": session_id,
        "uuid": str(uuid_mod.uuid4()),
        "timestamp": _iso_now(),
    }
    # SessionStoreEntry is a structural supertype ({type: str, ...}); the
    # extra fields are opaque pass-through for adapters.
    await session_store.append(key, [cast(SessionStoreEntry, entry)])


async def tag_session_via_store(
    session_store: SessionStore,
    session_id: str,
    tag: str | None,
    directory: str | None = None,
) -> None:
    """Tag a session by appending a tag entry to a :class:`SessionStore`.

    Async, store-backed counterpart to :func:`tag_session`. Pass ``None`` to
    clear the tag. Tags are Unicode-sanitized before storing.

    Args:
        session_store: The store to write to.
        session_id: UUID of the session to tag.
        tag: Tag string, or ``None`` to clear.
        directory: Project directory used to compute the ``project_key``.
            Defaults to the current working directory.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID, or if ``tag`` is
            empty/whitespace-only after sanitization.
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    if tag is not None:
        sanitized = _sanitize_unicode(tag).strip()
        if not sanitized:
            raise ValueError("tag must be non-empty (use None to clear)")
        tag = sanitized
    project_key = project_key_for_directory(directory)
    key: SessionKey = {"project_key": project_key, "session_id": session_id}
    entry: dict[str, Any] = {
        "type": "tag",
        "tag": tag if tag is not None else "",
        "sessionId": session_id,
        "uuid": str(uuid_mod.uuid4()),
        "timestamp": _iso_now(),
    }
    await session_store.append(key, [cast(SessionStoreEntry, entry)])


async def delete_session_via_store(
    session_store: SessionStore,
    session_id: str,
    directory: str | None = None,
) -> None:
    """Delete a session from a :class:`SessionStore`.

    Async, store-backed counterpart to :func:`delete_session`. If the store
    does not implement :meth:`SessionStore.delete`, deletion is a no-op
    (appropriate for WORM/append-only backends — matches the
    :class:`SessionStore` contract).

    Whether subagent transcripts under the session are also removed depends
    on the store's ``delete({session_id})`` semantics —
    :class:`InMemorySessionStore` cascades; custom stores may not.

    Args:
        session_store: The store to delete from.
        session_id: UUID of the session to delete.
        directory: Project directory used to compute the ``project_key``.
            Defaults to the current working directory.

    Raises:
        ValueError: If ``session_id`` is not a valid UUID.
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    if not _store_implements(session_store, "delete"):
        return
    project_key = project_key_for_directory(directory)
    key: SessionKey = {"project_key": project_key, "session_id": session_id}
    await session_store.delete(key)


async def fork_session_via_store(
    session_store: SessionStore,
    session_id: str,
    directory: str | None = None,
    up_to_message_id: str | None = None,
    title: str | None = None,
) -> ForkSessionResult:
    """Fork a session into a new branch with fresh UUIDs via a :class:`SessionStore`.

    Async, store-backed counterpart to :func:`fork_session`. Runs the fork
    transform directly over the objects returned by ``session_store.load()`` —
    no JSONL round-trip. A storage-layer copy (e.g. S3 CopyObject) is NOT
    sufficient: the transform remaps every UUID, rewrites ``sessionId`` on
    each entry, and stamps ``forkedFrom``, so the data must pass through
    this process once.

    Args:
        session_store: The store to read the source from and write the fork
            to.
        session_id: UUID of the source session to fork.
        directory: Project directory used to compute the ``project_key``.
            Defaults to the current working directory.
        up_to_message_id: Slice transcript up to this message UUID
            (inclusive). If omitted, copies the full transcript.
        title: Custom title for the fork. If omitted, derives from the
            original title + " (fork)".

    Returns:
        ``ForkSessionResult`` with the new session's UUID.

    Raises:
        ValueError: If ``session_id`` or ``up_to_message_id`` is not a
            valid UUID, or if the session has no messages to fork.
        FileNotFoundError: If the source session is not found in the store.
    """
    if not _validate_uuid(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    if up_to_message_id and not _validate_uuid(up_to_message_id):
        raise ValueError(f"Invalid up_to_message_id: {up_to_message_id}")
    project_key = project_key_for_directory(directory)
    src_key: SessionKey = {"project_key": project_key, "session_id": session_id}
    loaded = await session_store.load(src_key)
    if not loaded:
        raise FileNotFoundError(f"Session {session_id} not found")

    # Partition into transcript entries (with uuid) and content-replacement
    # records, mirroring _parse_fork_transcript for the already-parsed path.
    # SessionStoreEntry is a minimal structural supertype — widen to a plain
    # dict for field access.
    raw: list[dict[str, Any]] = cast("list[dict[str, Any]]", loaded)
    transcript: list[dict[str, Any]] = []
    content_replacements: list[Any] = []
    for entry in raw:
        entry_type = entry.get("type")
        if entry_type in _TRANSCRIPT_TYPES and isinstance(entry.get("uuid"), str):
            transcript.append(entry)
        elif (
            entry_type == "content-replacement"
            and entry.get("sessionId") == session_id
            and isinstance(entry.get("replacements"), list)
        ):
            content_replacements.extend(entry["replacements"])

    forked_session_id, lines = _build_fork_lines(
        transcript,
        content_replacements,
        session_id,
        up_to_message_id,
        title,
        lambda: _derive_title_from_entries(raw),
    )

    dst_key: SessionKey = {"project_key": project_key, "session_id": forked_session_id}
    # _build_fork_lines emits compact JSON strings; re-parse to objects so the
    # store receives the same shape it would from the mirror path. All entries
    # satisfy the SessionStoreEntry structural supertype ({type: str, ...}).
    await session_store.append(dst_key, [json.loads(line) for line in lines])
    return ForkSessionResult(session_id=forked_session_id)
