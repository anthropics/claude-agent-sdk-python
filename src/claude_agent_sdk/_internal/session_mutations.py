"""Portable session mutation functions for the Agent SDK.

Ported from TypeScript SDK (sessionMutationsImpl.ts).

Rename/tag append typed metadata entries to the session's JSONL (matching
the CLI pattern); delete removes the JSONL and the per-session directory.
Safe to call from any SDK host process — see concurrent-writer note below.

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
import unicodedata
from pathlib import Path

from .sessions import (
    _canonicalize_path,
    _find_project_dir,
    _get_projects_dir,
    _get_worktree_paths,
    _validate_uuid,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    Unlike the TS SDK's Bun/Windows workaround, CPython handles this correctly
    so no explicit-position fallback is needed.
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
# Unicode sanitization — ported from TS sanitization.ts
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

    Ported from TS ``partiallySanitizeUnicode``. Iteratively applies NFKC
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
