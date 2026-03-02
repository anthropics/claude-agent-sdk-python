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
from pathlib import Path

from ..types import SDKSessionInfo

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

        head, tail, mtime, size = lite.head, lite.tail, lite.mtime, lite.size

        # Check first line for sidechain sessions
        first_newline = head.find("\n")
        first_line = head[:first_newline] if first_newline >= 0 else head
        if '"isSidechain":true' in first_line or '"isSidechain": true' in first_line:
            continue

        custom_title = _extract_last_json_string_field(tail, "customTitle") or None
        first_prompt = _extract_first_prompt_from_head(head) or None
        summary = (
            custom_title
            or _extract_last_json_string_field(tail, "summary")
            or first_prompt
        )

        # Skip metadata-only sessions (no title, no summary, no prompt)
        if not summary:
            continue

        git_branch = (
            _extract_last_json_string_field(tail, "gitBranch")
            or _extract_json_string_field(head, "gitBranch")
            or None
        )
        session_cwd = _extract_json_string_field(head, "cwd") or project_path or None

        results.append(
            SDKSessionInfo(
                session_id=session_id,
                summary=summary,
                last_modified=mtime,
                file_size=size,
                custom_title=custom_title,
                first_prompt=first_prompt,
                git_branch=git_branch,
                cwd=session_cwd,
            )
        )

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
