"""Session listing implementation.

Ported from TypeScript SDK (listSessionsImpl.ts + sessionStoragePortable.ts).
Scans ~/.claude/projects/<sanitized-cwd>/ for .jsonl session files and
extracts metadata from stat + head/tail reads without full JSONL parsing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from ..types import SDKSessionInfo, SessionMessage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Size of the head/tail buffer for lite metadata reads.
LITE_READ_BUF_SIZE = 65536

# Maximum length for a single filesystem path component. Most filesystems
# limit individual components to 255 bytes. We use 200 to leave room for
# the hash suffix and separator.
MAX_SANITIZED_LENGTH = 200

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Pattern matching auto-generated or system messages that should be skipped
# when looking for the first meaningful user prompt.
_SKIP_FIRST_PROMPT_PATTERN = re.compile(
    r"^(?:<local-command-stdout>|<session-start-hook>|<tick>|<goal>|"
    r"\[Request interrupted by user[^\]]*\]|"
    r"\s*<ide_opened_file>[\s\S]*</ide_opened_file>\s*$|"
    r"\s*<ide_selection>[\s\S]*</ide_selection>\s*$)"
)

_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>")

_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9]")


# ---------------------------------------------------------------------------
# UUID validation
# ---------------------------------------------------------------------------


def _validate_uuid(maybe_uuid: str) -> str | None:
    """Returns the string if it is a valid UUID, else None."""
    if _UUID_RE.match(maybe_uuid):
        return maybe_uuid
    return None


# ---------------------------------------------------------------------------
# Path sanitization
# ---------------------------------------------------------------------------


def _simple_hash(s: str) -> str:
    """Port of the JS simpleHash function (32-bit integer hash, base36).

    Uses the same algorithm as the TS fallback so directory names match
    when the CLI was running under Node.js (not Bun).
    """
    h = 0
    for ch in s:
        char = ord(ch)
        h = (h << 5) - h + char
        # Emulate JS `hash |= 0` (coerce to 32-bit signed int)
        h = h & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    h = abs(h)
    # JS toString(36)
    if h == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    n = h
    while n > 0:
        out.append(digits[n % 36])
        n //= 36
    return "".join(reversed(out))


def _sanitize_path(name: str) -> str:
    """Makes a string safe for use as a directory name.

    Replaces all non-alphanumeric characters with hyphens. For paths
    exceeding MAX_SANITIZED_LENGTH, truncates and appends a hash suffix.
    """
    sanitized = _SANITIZE_RE.sub("-", name)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return sanitized
    h = _simple_hash(name)
    return f"{sanitized[:MAX_SANITIZED_LENGTH]}-{h}"


# ---------------------------------------------------------------------------
# Config directories
# ---------------------------------------------------------------------------


def _get_claude_config_home_dir() -> Path:
    """Returns the Claude config directory (respects CLAUDE_CONFIG_DIR)."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(unicodedata.normalize("NFC", config_dir))
    return Path(unicodedata.normalize("NFC", str(Path.home() / ".claude")))


def _get_projects_dir() -> Path:
    return _get_claude_config_home_dir() / "projects"


def _get_project_dir(project_path: str) -> Path:
    return _get_projects_dir() / _sanitize_path(project_path)


def _canonicalize_path(d: str) -> str:
    """Resolves a directory path to its canonical form using realpath + NFC."""
    try:
        resolved = os.path.realpath(d)
        return unicodedata.normalize("NFC", resolved)
    except OSError:
        return unicodedata.normalize("NFC", d)


def _find_project_dir(project_path: str) -> Path | None:
    """Finds the project directory for a given path.

    Tolerates hash mismatches for long paths (>200 chars). The CLI uses
    Bun.hash while the SDK under Node.js uses simpleHash — for paths that
    exceed MAX_SANITIZED_LENGTH, these produce different directory suffixes.
    This function falls back to prefix-based scanning when the exact match
    doesn't exist.
    """
    exact = _get_project_dir(project_path)
    if exact.is_dir():
        return exact

    # Exact match failed — for short paths this means no sessions exist.
    # For long paths, try prefix matching to handle hash mismatches.
    sanitized = _sanitize_path(project_path)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return None

    prefix = sanitized[:MAX_SANITIZED_LENGTH]
    projects_dir = _get_projects_dir()
    try:
        for entry in projects_dir.iterdir():
            if entry.is_dir() and entry.name.startswith(prefix + "-"):
                return entry
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# JSON string field extraction — no full parse, works on truncated lines
# ---------------------------------------------------------------------------


def _unescape_json_string(raw: str) -> str:
    """Unescape a JSON string value extracted as raw text."""
    if "\\" not in raw:
        return raw
    try:
        result = json.loads(f'"{raw}"')
        if isinstance(result, str):
            return result
        return raw
    except (json.JSONDecodeError, ValueError):
        return raw


def _extract_json_string_field(text: str, key: str) -> str | None:
    """Extracts a simple JSON string field value without full parsing.

    Looks for "key":"value" or "key": "value" patterns. Returns the first
    match, or None if not found.
    """
    patterns = [f'"{key}":"', f'"{key}": "']
    for pattern in patterns:
        idx = text.find(pattern)
        if idx < 0:
            continue

        value_start = idx + len(pattern)
        i = value_start
        while i < len(text):
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == '"':
                return _unescape_json_string(text[value_start:i])
            i += 1
    return None


def _extract_last_json_string_field(text: str, key: str) -> str | None:
    """Like _extract_json_string_field but finds the LAST occurrence."""
    patterns = [f'"{key}":"', f'"{key}": "']
    last_value: str | None = None
    for pattern in patterns:
        search_from = 0
        while True:
            idx = text.find(pattern, search_from)
            if idx < 0:
                break

            value_start = idx + len(pattern)
            i = value_start
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    last_value = _unescape_json_string(text[value_start:i])
                    break
                i += 1
            search_from = i + 1
    return last_value


# ---------------------------------------------------------------------------
# First prompt extraction from head chunk
# ---------------------------------------------------------------------------


def _extract_first_prompt_from_head(head: str) -> str:
    """Extracts the first meaningful user prompt from a JSONL head chunk.

    Skips tool_result messages, isMeta, isCompactSummary, command-name
    messages, and auto-generated patterns. Truncates to 200 chars.
    """
    start = 0
    command_fallback = ""
    head_len = len(head)

    while start < head_len:
        newline_idx = head.find("\n", start)
        if newline_idx >= 0:
            line = head[start:newline_idx]
            start = newline_idx + 1
        else:
            line = head[start:]
            start = head_len

        if '"type":"user"' not in line and '"type": "user"' not in line:
            continue
        if '"tool_result"' in line:
            continue
        if '"isMeta":true' in line or '"isMeta": true' in line:
            continue
        if '"isCompactSummary":true' in line or '"isCompactSummary": true' in line:
            continue

        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue

        message = entry.get("message")
        if not isinstance(message, dict):
            continue

        content = message.get("content")
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    texts.append(block["text"])

        for raw in texts:
            result = raw.replace("\n", " ").strip()
            if not result:
                continue

            # Skip slash-command messages but remember first as fallback
            cmd_match = _COMMAND_NAME_RE.search(result)
            if cmd_match:
                if not command_fallback:
                    command_fallback = cmd_match.group(1)
                continue

            if _SKIP_FIRST_PROMPT_PATTERN.match(result):
                continue

            if len(result) > 200:
                result = result[:200].rstrip() + "\u2026"
            return result

    if command_fallback:
        return command_fallback
    return ""


# ---------------------------------------------------------------------------
# File I/O — read head and tail of a file
# ---------------------------------------------------------------------------


class _LiteSessionFile:
    """Result of reading a session file's head, tail, mtime and size."""

    __slots__ = ("mtime", "size", "head", "tail")

    def __init__(self, mtime: int, size: int, head: str, tail: str) -> None:
        self.mtime = mtime
        self.size = size
        self.head = head
        self.tail = tail


def _read_session_lite(file_path: Path) -> _LiteSessionFile | None:
    """Opens a session file, stats it, and reads head + tail.

    Returns None on any error or if file is empty.
    """
    try:
        with file_path.open("rb") as f:
            stat = os.fstat(f.fileno())
            size = stat.st_size
            mtime = int(stat.st_mtime * 1000)

            head_bytes = f.read(LITE_READ_BUF_SIZE)
            if not head_bytes:
                return None

            head = head_bytes.decode("utf-8", errors="replace")

            tail_offset = max(0, size - LITE_READ_BUF_SIZE)
            if tail_offset == 0:
                tail = head
            else:
                f.seek(tail_offset)
                tail_bytes = f.read(LITE_READ_BUF_SIZE)
                tail = tail_bytes.decode("utf-8", errors="replace")

            return _LiteSessionFile(mtime=mtime, size=size, head=head, tail=tail)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Git worktree detection
# ---------------------------------------------------------------------------


def _get_worktree_paths(cwd: str) -> list[str]:
    """Returns absolute worktree paths for the git repo containing cwd.

    Returns empty list if git is unavailable or cwd is not in a repo.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0 or not result.stdout:
        return []

    paths = []
    for line in result.stdout.split("\n"):
        if line.startswith("worktree "):
            path = unicodedata.normalize("NFC", line[len("worktree ") :])
            paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Field extraction — shared by list_sessions and get_session_info
# ---------------------------------------------------------------------------


def _parse_session_info_from_lite(
    session_id: str,
    lite: _LiteSessionFile,
    project_path: str | None = None,
) -> SDKSessionInfo | None:
    """Parses SDKSessionInfo fields from a lite session read (head/tail/stat).

    Returns None for sidechain sessions or metadata-only sessions with no
    extractable summary.

    Shared by list_sessions and get_session_info.
    """
    head, tail, mtime, size = lite.head, lite.tail, lite.mtime, lite.size

    # Check first line for sidechain sessions
    first_newline = head.find("\n")
    first_line = head[:first_newline] if first_newline >= 0 else head
    if '"isSidechain":true' in first_line or '"isSidechain": true' in first_line:
        return None

    # User-set title (customTitle) wins over AI-generated title (aiTitle).
    # Head fallback covers short sessions where the title entry may not be in tail.
    custom_title = (
        _extract_last_json_string_field(tail, "customTitle")
        or _extract_last_json_string_field(head, "customTitle")
        or _extract_last_json_string_field(tail, "aiTitle")
        or _extract_last_json_string_field(head, "aiTitle")
        or None
    )
    first_prompt = _extract_first_prompt_from_head(head) or None
    # lastPrompt tail entry shows what the user was most recently doing.
    summary = (
        custom_title
        or _extract_last_json_string_field(tail, "lastPrompt")
        or _extract_last_json_string_field(tail, "summary")
        or first_prompt
    )

    # Skip metadata-only sessions (no title, no summary, no prompt)
    if not summary:
        return None

    git_branch = (
        _extract_last_json_string_field(tail, "gitBranch")
        or _extract_json_string_field(head, "gitBranch")
        or None
    )
    session_cwd = _extract_json_string_field(head, "cwd") or project_path or None
    # Scope tag extraction to {"type":"tag"} lines — a bare tail scan for
    # "tag" would match tool_use inputs (git tag, Docker tags, cloud resource
    # tags). Mirrors TS listSessionsImpl.ts / sessionStorage.ts:629.
    tag_line = next(
        (ln for ln in reversed(tail.split("\n")) if ln.startswith('{"type":"tag"')),
        None,
    )
    tag = (
        (_extract_last_json_string_field(tag_line, "tag") or None) if tag_line else None
    )

    # created_at from first entry's ISO timestamp (epoch ms). More reliable
    # than stat().birthtime which is unsupported on some filesystems.
    created_at: int | None = None
    first_timestamp = _extract_json_string_field(first_line, "timestamp")
    if first_timestamp:
        try:
            # Python 3.10's fromisoformat doesn't support trailing 'Z'
            ts = (
                first_timestamp.replace("Z", "+00:00")
                if first_timestamp.endswith("Z")
                else first_timestamp
            )
            created_at = int(datetime.fromisoformat(ts).timestamp() * 1000)
        except ValueError:
            pass

    return SDKSessionInfo(
        session_id=session_id,
        summary=summary,
        last_modified=mtime,
        file_size=size,
        custom_title=custom_title,
        first_prompt=first_prompt,
        git_branch=git_branch,
        cwd=session_cwd,
        tag=tag,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Core implementation
# ---------------------------------------------------------------------------


def _read_sessions_from_dir(
    project_dir: Path, project_path: str | None = None
) -> list[SDKSessionInfo]:
    """Reads session files from a single project directory.

    Each file gets a stat + head/tail read. Filters out sidechain sessions
    and metadata-only sessions (no title/summary/prompt).
    """
    try:
        entries = list(project_dir.iterdir())
    except OSError:
        return []

    results: list[SDKSessionInfo] = []

    for entry in entries:
        name = entry.name
        if not name.endswith(".jsonl"):
            continue
        session_id = _validate_uuid(name[:-6])
        if not session_id:
            continue

        lite = _read_session_lite(entry)
        if lite is None:
            continue

        info = _parse_session_info_from_lite(session_id, lite, project_path)
        if info is not None:
            results.append(info)

    return results


def _deduplicate_by_session_id(
    sessions: list[SDKSessionInfo],
) -> list[SDKSessionInfo]:
    """Deduplicates by session_id, keeping the newest last_modified."""
    by_id: dict[str, SDKSessionInfo] = {}
    for s in sessions:
        existing = by_id.get(s.session_id)
        if existing is None or s.last_modified > existing.last_modified:
            by_id[s.session_id] = s
    return list(by_id.values())


def _apply_sort_and_limit(
    sessions: list[SDKSessionInfo], limit: int | None
) -> list[SDKSessionInfo]:
    """Sorts sessions by last_modified descending and applies optional limit."""
    sessions.sort(key=lambda s: s.last_modified, reverse=True)
    if limit is not None and limit > 0:
        return sessions[:limit]
    return sessions


def _list_sessions_for_project(
    directory: str, limit: int | None, include_worktrees: bool
) -> list[SDKSessionInfo]:
    """Lists sessions for a specific project directory (and its worktrees)."""
    canonical_dir = _canonicalize_path(directory)

    if include_worktrees:
        try:
            worktree_paths = _get_worktree_paths(canonical_dir)
        except Exception:
            worktree_paths = []
    else:
        worktree_paths = []

    # No worktrees (or git not available / scanning disabled) —
    # just scan the single project dir
    if len(worktree_paths) <= 1:
        project_dir = _find_project_dir(canonical_dir)
        if project_dir is None:
            return []
        sessions = _read_sessions_from_dir(project_dir, canonical_dir)
        return _apply_sort_and_limit(sessions, limit)

    # Worktree-aware scanning: find all project dirs matching any worktree
    projects_dir = _get_projects_dir()
    case_insensitive = sys.platform == "win32"

    # Sort worktree paths by sanitized prefix length (longest first) so
    # more specific matches take priority over shorter ones
    indexed = []
    for wt in worktree_paths:
        sanitized = _sanitize_path(wt)
        prefix = sanitized.lower() if case_insensitive else sanitized
        indexed.append((wt, prefix))
    indexed.sort(key=lambda x: len(x[1]), reverse=True)

    try:
        all_dirents = [e for e in projects_dir.iterdir() if e.is_dir()]
    except OSError:
        # Fall back to single project dir
        project_dir = _find_project_dir(canonical_dir)
        if project_dir is None:
            return _apply_sort_and_limit([], limit)
        sessions = _read_sessions_from_dir(project_dir, canonical_dir)
        return _apply_sort_and_limit(sessions, limit)

    all_sessions: list[SDKSessionInfo] = []
    seen_dirs: set[str] = set()

    # Always include the user's actual directory (handles subdirectories
    # like /repo/packages/my-app that won't match worktree root prefixes)
    canonical_project_dir = _find_project_dir(canonical_dir)
    if canonical_project_dir is not None:
        dir_base = canonical_project_dir.name
        seen_dirs.add(dir_base.lower() if case_insensitive else dir_base)
        sessions = _read_sessions_from_dir(canonical_project_dir, canonical_dir)
        all_sessions.extend(sessions)

    for entry in all_dirents:
        dir_name = entry.name.lower() if case_insensitive else entry.name
        if dir_name in seen_dirs:
            continue

        for wt_path, prefix in indexed:
            # Only use startswith for truncated paths (>MAX_SANITIZED_LENGTH)
            # where a hash suffix follows. For short paths, require exact match
            # to avoid /root/project matching /root/project-foo.
            is_match = dir_name == prefix or (
                len(prefix) >= MAX_SANITIZED_LENGTH
                and dir_name.startswith(prefix + "-")
            )
            if is_match:
                seen_dirs.add(dir_name)
                sessions = _read_sessions_from_dir(entry, wt_path)
                all_sessions.extend(sessions)
                break

    deduped = _deduplicate_by_session_id(all_sessions)
    return _apply_sort_and_limit(deduped, limit)


def _list_all_sessions(limit: int | None) -> list[SDKSessionInfo]:
    """Lists sessions across all project directories."""
    projects_dir = _get_projects_dir()

    try:
        project_dirs = [e for e in projects_dir.iterdir() if e.is_dir()]
    except OSError:
        return []

    all_sessions: list[SDKSessionInfo] = []
    for project_dir in project_dirs:
        all_sessions.extend(_read_sessions_from_dir(project_dir))

    deduped = _deduplicate_by_session_id(all_sessions)
    return _apply_sort_and_limit(deduped, limit)


def list_sessions(
    directory: str | None = None,
    limit: int | None = None,
    include_worktrees: bool = True,
) -> list[SDKSessionInfo]:
    """Lists sessions with metadata extracted from stat + head/tail reads.

    When ``directory`` is provided, returns sessions for that project
    directory and its git worktrees. When omitted, returns sessions
    across all projects.

    Args:
        directory: Directory to list sessions for. When provided, returns
            sessions for this project directory (and optionally its git
            worktrees). When omitted, returns sessions across all projects.
        limit: Maximum number of sessions to return.
        include_worktrees: When ``directory`` is provided and the directory
            is inside a git repository, include sessions from all git
            worktree paths. Defaults to ``True``.

    Returns:
        List of ``SDKSessionInfo`` sorted by ``last_modified`` descending.

    Example:
        List sessions for a specific project::

            sessions = list_sessions(directory="/path/to/project")

        List all sessions across all projects::

            all_sessions = list_sessions()

        List sessions without scanning git worktrees::

            sessions = list_sessions(
                directory="/path/to/project",
                include_worktrees=False,
            )
    """
    if directory:
        return _list_sessions_for_project(directory, limit, include_worktrees)
    return _list_all_sessions(limit)


# ---------------------------------------------------------------------------
# get_session_info — single-session metadata lookup
# ---------------------------------------------------------------------------


def get_session_info(
    session_id: str,
    directory: str | None = None,
) -> SDKSessionInfo | None:
    """Reads metadata for a single session by ID.

    Wraps ``_read_session_lite`` for one file — no O(n) directory scan.
    Directory resolution matches ``get_session_messages``: ``directory`` is
    the project path; when omitted, all project directories are searched for
    the session file.

    Args:
        session_id: UUID of the session to look up.
        directory: Project directory path (same semantics as
            ``list_sessions(directory=...)``). When omitted, all project
            directories are searched for the session file.

    Returns:
        ``SDKSessionInfo`` for the session, or ``None`` if the session file
        is not found, is a sidechain session, or has no extractable summary.

    Example:
        Look up a session in a specific project::

            info = get_session_info(
                "550e8400-e29b-41d4-a716-446655440000",
                directory="/path/to/project",
            )
            if info:
                print(info.summary)

        Search all projects for a session::

            info = get_session_info("550e8400-e29b-41d4-a716-446655440000")
    """
    uuid = _validate_uuid(session_id)
    if not uuid:
        return None
    file_name = f"{uuid}.jsonl"

    if directory:
        canonical = _canonicalize_path(directory)
        project_dir = _find_project_dir(canonical)
        if project_dir is not None:
            lite = _read_session_lite(project_dir / file_name)
            if lite is not None:
                return _parse_session_info_from_lite(uuid, lite, canonical)

        # Worktree fallback — matches get_session_messages semantics.
        # Sessions may live under a different worktree root.
        try:
            worktree_paths = _get_worktree_paths(canonical)
        except Exception:
            worktree_paths = []
        for wt in worktree_paths:
            if wt == canonical:
                continue
            wt_project_dir = _find_project_dir(wt)
            if wt_project_dir is not None:
                lite = _read_session_lite(wt_project_dir / file_name)
                if lite is not None:
                    return _parse_session_info_from_lite(uuid, lite, wt)

        return None

    # No directory — search all project directories for the session file.
    projects_dir = _get_projects_dir()
    try:
        dirents = [e for e in projects_dir.iterdir() if e.is_dir()]
    except OSError:
        return None
    for entry in dirents:
        lite = _read_session_lite(entry / file_name)
        if lite is not None:
            return _parse_session_info_from_lite(uuid, lite)
    return None


# ---------------------------------------------------------------------------
# get_session_messages — full transcript reconstruction
# ---------------------------------------------------------------------------

# Transcript entry types that carry uuid + parentUuid chain links.
_TRANSCRIPT_ENTRY_TYPES = frozenset(
    {"user", "assistant", "progress", "system", "attachment"}
)

# Internal type for parsed JSONL transcript entries — mirrors the TS
# TranscriptEntry type but as a loose dict (fields: type, uuid, parentUuid,
# sessionId, message, isSidechain, isMeta, isCompactSummary, teamName).
_TranscriptEntry = dict[str, Any]


def _try_read_session_file(project_dir: Path, file_name: str) -> str | None:
    """Tries to read a session JSONL file from a project directory."""
    try:
        return (project_dir / file_name).read_text(encoding="utf-8")
    except OSError:
        return None


def _read_session_file(session_id: str, directory: str | None) -> str | None:
    """Finds and reads the session JSONL file.

    If directory is provided, looks in that project directory and its git
    worktrees (with prefix-fallback for Bun/Node hash mismatches on long
    paths). Otherwise, searches all project directories.

    Returns the file content, or None if not found.
    """
    file_name = f"{session_id}.jsonl"

    if directory:
        canonical_dir = _canonicalize_path(directory)

        # Try the exact/prefix-matched project directory first
        project_dir = _find_project_dir(canonical_dir)
        if project_dir is not None:
            content = _try_read_session_file(project_dir, file_name)
            if content:
                return content

        # Try worktree paths — sessions may live under a different worktree root
        try:
            worktree_paths = _get_worktree_paths(canonical_dir)
        except Exception:
            worktree_paths = []

        for wt in worktree_paths:
            if wt == canonical_dir:
                continue  # already tried above
            wt_project_dir = _find_project_dir(wt)
            if wt_project_dir is not None:
                content = _try_read_session_file(wt_project_dir, file_name)
                if content:
                    return content

        return None

    # No directory provided — search all project directories
    projects_dir = _get_projects_dir()
    try:
        dirents = list(projects_dir.iterdir())
    except OSError:
        return None

    for entry in dirents:
        content = _try_read_session_file(entry, file_name)
        if content:
            return content

    return None


def _parse_transcript_entries(content: str) -> list[_TranscriptEntry]:
    """Parses JSONL content into transcript entries.

    Only keeps entries that have a uuid and are transcript message types
    (user/assistant/progress/system/attachment). Skips corrupt lines.
    """
    entries: list[_TranscriptEntry] = []
    start = 0
    length = len(content)

    while start < length:
        end = content.find("\n", start)
        if end == -1:
            end = length

        line = content[start:end].strip()
        start = end + 1
        if not line:
            continue

        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type in _TRANSCRIPT_ENTRY_TYPES and isinstance(entry.get("uuid"), str):
            entries.append(entry)

    return entries


def _build_conversation_chain(
    entries: list[_TranscriptEntry],
) -> list[_TranscriptEntry]:
    """Builds the conversation chain by finding the leaf and walking parentUuid.

    Returns messages in chronological order (root -> leaf).

    Note: logicalParentUuid (set on compact_boundary entries) is intentionally
    NOT followed. This matches VS Code IDE behavior — post-compaction, the
    isCompactSummary message replaces earlier messages, so following logical
    parents would duplicate content.
    """
    if not entries:
        return []

    # Index by uuid for O(1) parent lookup
    by_uuid: dict[str, _TranscriptEntry] = {}
    for entry in entries:
        by_uuid[entry["uuid"]] = entry

    # Build index of entry positions (file order) for tie-breaking
    entry_index: dict[str, int] = {}
    for i, entry in enumerate(entries):
        entry_index[entry["uuid"]] = i

    # Find terminal messages (no children point to them via parentUuid)
    parent_uuids: set[str] = set()
    for entry in entries:
        parent = entry.get("parentUuid")
        if parent:
            parent_uuids.add(parent)

    terminals = [e for e in entries if e["uuid"] not in parent_uuids]

    # From each terminal, walk back to find the nearest user/assistant leaf
    leaves: list[_TranscriptEntry] = []
    for terminal in terminals:
        walk_cur: _TranscriptEntry | None = terminal
        walk_seen: set[str] = set()
        while walk_cur is not None:
            uid = walk_cur["uuid"]
            if uid in walk_seen:
                break
            walk_seen.add(uid)
            if walk_cur.get("type") in ("user", "assistant"):
                leaves.append(walk_cur)
                break
            parent = walk_cur.get("parentUuid")
            walk_cur = by_uuid.get(parent) if parent else None

    if not leaves:
        return []

    # Pick the leaf from the main chain (not sidechain/team/meta), preferring
    # the highest position in the entries array (most recent in file)
    main_leaves = [
        leaf
        for leaf in leaves
        if not leaf.get("isSidechain")
        and not leaf.get("teamName")
        and not leaf.get("isMeta")
    ]

    def _pick_best(candidates: list[_TranscriptEntry]) -> _TranscriptEntry:
        best = candidates[0]
        best_idx = entry_index.get(best["uuid"], -1)
        for cur in candidates[1:]:
            cur_idx = entry_index.get(cur["uuid"], -1)
            if cur_idx > best_idx:
                best = cur
                best_idx = cur_idx
        return best

    leaf = _pick_best(main_leaves) if main_leaves else _pick_best(leaves)

    # Walk from leaf to root via parentUuid
    chain: list[_TranscriptEntry] = []
    chain_seen: set[str] = set()
    chain_cur: _TranscriptEntry | None = leaf
    while chain_cur is not None:
        uid = chain_cur["uuid"]
        if uid in chain_seen:
            break
        chain_seen.add(uid)
        chain.append(chain_cur)
        parent = chain_cur.get("parentUuid")
        chain_cur = by_uuid.get(parent) if parent else None

    chain.reverse()
    return chain


def _is_visible_message(entry: _TranscriptEntry) -> bool:
    """Returns True if the entry should be included in the returned messages."""
    entry_type = entry.get("type")
    if entry_type != "user" and entry_type != "assistant":
        return False
    if entry.get("isMeta"):
        return False
    if entry.get("isSidechain"):
        return False
    # Note: isCompactSummary messages are intentionally included. They contain
    # the summarized content from compacted conversations and are the only
    # representation of that content post-compaction. This matches VS Code IDE
    # behavior (transcriptToSessionMessage does not filter them).
    return not entry.get("teamName")


def _to_session_message(entry: _TranscriptEntry) -> SessionMessage:
    """Converts a transcript entry dict into a SessionMessage."""
    entry_type = entry.get("type")
    # Narrow to the Literal type — _is_visible_message already guarantees
    # this is "user" or "assistant".
    msg_type: str = "user" if entry_type == "user" else "assistant"
    return SessionMessage(
        type=msg_type,  # type: ignore[arg-type]
        uuid=entry.get("uuid", ""),
        session_id=entry.get("sessionId", ""),
        message=entry.get("message"),
        parent_tool_use_id=None,
    )


def get_session_messages(
    session_id: str,
    directory: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[SessionMessage]:
    """Reads a session's conversation messages from its JSONL transcript file.

    Parses the full JSONL, builds the conversation chain via ``parentUuid``
    links, and returns user/assistant messages in chronological order.

    Args:
        session_id: UUID of the session to read.
        directory: Project directory to find the session in. If omitted,
            searches all project directories under ``~/.claude/projects/``.
        limit: Maximum number of messages to return.
        offset: Number of messages to skip from the start.

    Returns:
        List of ``SessionMessage`` objects in chronological order. Returns
        an empty list if the session is not found, the session_id is not a
        valid UUID, or the transcript contains no visible messages.

    Example:
        Read all messages from a session::

            messages = get_session_messages(
                "550e8400-e29b-41d4-a716-446655440000",
                directory="/path/to/project",
            )
            for msg in messages:
                print(msg.type, msg.message)

        Read with pagination::

            page = get_session_messages(
                session_id, limit=10, offset=20
            )
    """
    if not _validate_uuid(session_id):
        return []

    content = _read_session_file(session_id, directory)
    if not content:
        return []

    entries = _parse_transcript_entries(content)
    chain = _build_conversation_chain(entries)
    visible = [e for e in chain if _is_visible_message(e)]
    messages = [_to_session_message(e) for e in visible]

    # Apply offset and limit
    if limit is not None and limit > 0:
        return messages[offset : offset + limit]
    if offset > 0:
        return messages[offset:]
    return messages
