"""Incremental session-summary derivation for :class:`SessionStore` adapters.

:func:`fold_session_summary` lets a store maintain a per-session
:class:`SessionSummaryEntry` sidecar incrementally inside ``append()`` so
``list_sessions_from_store()`` can fetch all metadata in a single
``list_session_summaries()`` call instead of N per-session ``load()`` calls.

Every derived field is append-incremental (set-once or last-wins) so adapters
never need to re-read previously appended entries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from ..types import (
    SDKSessionInfo,
    SessionKey,
    SessionStoreEntry,
    SessionSummaryEntry,
)
from .sessions import _COMMAND_NAME_RE, _SKIP_FIRST_PROMPT_PATTERN

__all__ = ["fold_session_summary", "summary_entry_to_sdk_info"]


# Map of JSONL entry keys → SessionSummaryEntry keys for last-wins string
# fields. Each appended entry overwrites the previous value when present.
_LAST_WINS_FIELDS: dict[str, str] = {
    "customTitle": "custom_title",
    "aiTitle": "ai_title",
    "lastPrompt": "last_prompt",
    "summary": "summary_hint",
    "gitBranch": "git_branch",
}


def _iso_to_epoch_ms(ts: Any) -> int | None:
    """Parse an ISO-8601 timestamp string to Unix epoch milliseconds."""
    if not isinstance(ts, str):
        return None
    try:
        # Python 3.10's fromisoformat doesn't support trailing 'Z'
        norm = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        return int(datetime.fromisoformat(norm).timestamp() * 1000)
    except ValueError:
        return None


def _entry_text_blocks(entry: dict[str, Any]) -> list[str]:
    """Extract text strings from a ``type=="user"`` entry's message content."""
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
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
    return texts


def _fold_first_prompt(data: dict[str, Any], entry: dict[str, Any]) -> None:
    """Replicate ``_extract_first_prompt_from_head`` for a single parsed entry.

    Mutates ``data`` in place: sets ``first_prompt`` + ``first_prompt_locked``
    on a real match, or stashes a ``command_fallback`` for slash-command
    messages. Skips tool_result, isMeta, isCompactSummary, and auto-generated
    patterns.
    """
    if data.get("first_prompt_locked"):
        return
    if entry.get("type") != "user":
        return
    if entry.get("isMeta") is True or entry.get("isCompactSummary") is True:
        return
    # Skip tool_result-carrying user messages.
    message = entry.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            return

    for raw in _entry_text_blocks(entry):
        result = raw.replace("\n", " ").strip()
        if not result:
            continue
        cmd_match = _COMMAND_NAME_RE.search(result)
        if cmd_match:
            if not data.get("command_fallback"):
                data["command_fallback"] = cmd_match.group(1)
            continue
        if _SKIP_FIRST_PROMPT_PATTERN.match(result):
            continue
        if len(result) > 200:
            result = result[:200].rstrip() + "\u2026"
        data["first_prompt"] = result
        data["first_prompt_locked"] = True
        return


def fold_session_summary(
    prev: SessionSummaryEntry | None,
    key: SessionKey,
    entries: list[SessionStoreEntry],
) -> SessionSummaryEntry:
    """Fold a batch of appended entries into the running summary for ``key``.

    Stores call this from inside ``append()`` to keep a
    :class:`SessionSummaryEntry` sidecar up to date without re-reading the
    transcript. ``prev`` is the previous summary for the same key (or ``None``
    for the first append).

    Do not call this for keys with a ``subpath`` — subagent transcripts must
    not contribute to the main session's summary. Guard with
    ``if key.get("subpath") is None:`` before calling.

    All derived state lives in the opaque ``data`` dict; stores persist it
    verbatim and do not interpret it.

    ``mtime`` is NOT touched by the fold — it is the sidecar's storage
    write time and must be stamped by the adapter after persisting. It has
    to share a clock with the ``mtime`` returned by
    :meth:`SessionStore.list_sessions` for the same session (typically file
    mtime, S3 ``LastModified``, Postgres ``updated_at``, or whatever native
    timestamp the adapter surfaces); deriving it from entry ISO timestamps
    would make every batched-write sidecar appear strictly older than the
    session's current mtime, defeating the fast-path staleness check. For a
    new session (``prev is None``) the fold returns ``mtime=0`` as a
    placeholder; the adapter is expected to overwrite it.

    ``created_at`` latches the first parseable entry timestamp; the disk
    lite-parse only inspects the first line, so for streams whose first
    entry lacks a timestamp (does not occur in CLI-produced transcripts)
    the fold path yields a non-``None`` ``created_at`` where lite-parse
    yields ``None``.
    """
    if prev is not None:
        summary: SessionSummaryEntry = {
            "session_id": prev["session_id"],
            "mtime": prev["mtime"],
            "data": dict(prev["data"]),
        }
    else:
        summary = {"session_id": key["session_id"], "mtime": 0, "data": {}}
    data = summary["data"]

    for raw in entries:
        # SessionStoreEntry is a permissive TypedDict; widen to a plain dict
        # so .get() of unknown keys type-checks.
        entry = cast("dict[str, Any]", raw)

        ms = _iso_to_epoch_ms(entry.get("timestamp"))

        if "is_sidechain" not in data:
            data["is_sidechain"] = entry.get("isSidechain") is True
        if "created_at" not in data and ms is not None:
            data["created_at"] = ms

        if "cwd" not in data:
            cwd = entry.get("cwd")
            if isinstance(cwd, str) and cwd:
                data["cwd"] = cwd

        _fold_first_prompt(data, entry)

        for src, dst in _LAST_WINS_FIELDS.items():
            val = entry.get(src)
            if isinstance(val, str):
                data[dst] = val

        if entry.get("type") == "tag":
            tag_val = entry.get("tag")
            if isinstance(tag_val, str) and tag_val:
                data["tag"] = tag_val
            else:
                # Empty string or absent tag clears the tag.
                data.pop("tag", None)

    return summary


def summary_entry_to_sdk_info(
    entry: SessionSummaryEntry, project_path: str | None
) -> SDKSessionInfo | None:
    """Convert a :class:`SessionSummaryEntry` to :class:`SDKSessionInfo`.

    Returns ``None`` for sidechain sessions or sessions with no extractable
    summary, matching ``_parse_session_info_from_lite``'s filtering.
    """
    data = entry["data"]
    if data.get("is_sidechain"):
        return None

    first_prompt = (
        data.get("first_prompt")
        if data.get("first_prompt_locked")
        else data.get("command_fallback")
    ) or None
    custom_title = data.get("custom_title") or data.get("ai_title") or None
    summary = (
        custom_title
        or data.get("last_prompt")
        or data.get("summary_hint")
        or first_prompt
    )
    if not summary:
        return None

    return SDKSessionInfo(
        session_id=entry["session_id"],
        summary=summary,
        last_modified=entry["mtime"],
        # file_size is a JSONL byte count — meaningful only for the local-disk
        # path (see SDKSessionInfo.file_size). Stores have no equivalent.
        file_size=None,
        custom_title=custom_title,
        first_prompt=first_prompt,
        git_branch=data.get("git_branch") or None,
        cwd=data.get("cwd") or project_path or None,
        tag=data.get("tag") or None,
        created_at=data.get("created_at"),
    )
