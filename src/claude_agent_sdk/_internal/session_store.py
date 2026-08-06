"""In-memory reference implementation of :class:`SessionStore`."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..types import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
    SessionSummaryEntry,
)
from .session_summary import fold_session_summary
from .sessions import project_key_for_directory

__all__ = [
    "InMemorySessionStore",
    "file_path_to_session_key",
    "project_key_for_directory",
]


def _key_to_string(key: SessionKey) -> str:
    parts = [key["project_key"], key["session_id"]]
    subpath = key.get("subpath")
    if subpath:
        parts.append(subpath)
    return "/".join(parts)


class InMemorySessionStore(SessionStore):
    """In-memory :class:`SessionStore` implementation for testing and development.

    Stores entries in a ``dict`` keyed by a composite ``project_key/session_id``
    string (with an optional ``/subpath`` suffix). Not suitable for production —
    data is lost when the process exits.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[SessionStoreEntry]] = {}
        self._mtimes: dict[str, int] = {}
        self._summaries: dict[tuple[str, str], SessionSummaryEntry] = {}
        self._last_mtime = 0

    def _next_mtime(self) -> int:
        """Storage write time for this adapter, in Unix epoch ms.

        Guaranteed strictly monotonically increasing across calls within the
        process so back-to-back appends always produce distinct mtimes (real
        storage backends — file mtime on modern filesystems, S3
        LastModified, Postgres updated_at — get this property for free from
        their commit ordering).
        """
        now_ms = int(time.time() * 1000)
        if now_ms <= self._last_mtime:
            now_ms = self._last_mtime + 1
        self._last_mtime = now_ms
        return now_ms

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        k = _key_to_string(key)
        self._store.setdefault(k, []).extend(entries)
        now_ms = self._next_mtime()
        # Maintain the per-session summary sidecar incrementally so
        # list_session_summaries() never re-reads. Subagent subpaths don't
        # contribute to the main session's summary.
        if key.get("subpath") is None:
            sk = (key["project_key"], key["session_id"])
            folded = fold_session_summary(self._summaries.get(sk), key, entries)
            # Stamp the sidecar with this adapter's storage write time — the
            # SAME clock list_sessions() exposes below. SessionSummaryEntry.
            # mtime is contractually storage write time (not entry time), so
            # the fast-path staleness check (summary.mtime < list_sessions
            # mtime) works correctly.
            folded["mtime"] = now_ms
            self._summaries[sk] = folded
        self._mtimes[k] = now_ms

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        entries = self._store.get(_key_to_string(key))
        return None if entries is None else list(entries)

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        results: list[SessionStoreListEntry] = []
        prefix = project_key + "/"
        for k in self._store:
            if k.startswith(prefix):
                rest = k[len(prefix) :]
                # Only include main transcripts (no subpath, so no second '/')
                if "/" not in rest:
                    results.append(
                        {"session_id": rest, "mtime": self._mtimes.get(k, 0)}
                    )
        return results

    async def list_session_summaries(
        self, project_key: str
    ) -> list[SessionSummaryEntry]:
        return [s for (pk, _), s in self._summaries.items() if pk == project_key]

    async def delete(self, key: SessionKey) -> None:
        k = _key_to_string(key)
        self._store.pop(k, None)
        self._mtimes.pop(k, None)
        # Deleting the main transcript cascades to its subkeys (subagent
        # transcripts, metadata) so they aren't orphaned. A targeted delete
        # with an explicit subpath removes only that one entry.
        if key.get("subpath") is None:
            self._summaries.pop((key["project_key"], key["session_id"]), None)
            prefix = f"{key['project_key']}/{key['session_id']}/"
            for store_key in [sk for sk in self._store if sk.startswith(prefix)]:
                self._store.pop(store_key, None)
                self._mtimes.pop(store_key, None)

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        prefix = f"{key['project_key']}/{key['session_id']}/"
        return [k[len(prefix) :] for k in self._store if k.startswith(prefix)]

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def get_entries(self, key: SessionKey) -> list[SessionStoreEntry]:
        """Test helper — get all entries for a key (empty list if absent)."""
        return list(self._store.get(_key_to_string(key), []))

    @property
    def size(self) -> int:
        """Test helper — number of stored sessions (main transcripts only)."""
        count = 0
        for k in self._store:
            first_slash = k.find("/")
            if first_slash != -1 and "/" not in k[first_slash + 1 :]:
                count += 1
        return count

    def clear(self) -> None:
        """Test helper — clear all stored data."""
        self._store.clear()
        self._mtimes.clear()
        self._summaries.clear()
        self._last_mtime = 0


def file_path_to_session_key(file_path: str, projects_dir: str) -> SessionKey | None:
    """Derive a :class:`SessionKey` from an absolute transcript file path.

    Main transcripts: ``<projects_dir>/<project_key>/<session_id>.jsonl``
    Subagent transcripts: ``<projects_dir>/<project_key>/<session_id>/subagents/agent-<id>.jsonl``

    Returns ``None`` if ``file_path`` is not under ``projects_dir`` or has an
    unrecognized shape.
    """
    try:
        rel = os.path.relpath(file_path, projects_dir)
    except ValueError:
        # Windows: relpath raises when the paths are on different drives.
        # Treat as "not under projects_dir" so the batcher drops the frame
        # with a warning instead of letting the exception escape _drain().
        return None
    rel_path = Path(rel)
    parts = list(rel_path.parts)
    if not parts or parts[0] == ".." or rel_path.is_absolute():
        return None

    if len(parts) < 2:
        return None

    project_key = parts[0]
    second = parts[1]

    # Main transcript: <project_key>/<session_id>.jsonl
    if len(parts) == 2 and second.endswith(".jsonl"):
        return {"project_key": project_key, "session_id": second[: -len(".jsonl")]}

    # Subagent transcript: <project_key>/<session_id>/subagents/.../agent-<id>.jsonl
    if len(parts) >= 4:
        subpath_parts = parts[2:]
        last = subpath_parts[-1]
        if last.endswith(".jsonl"):
            subpath_parts[-1] = last[: -len(".jsonl")]
        # Subpaths are always /-joined regardless of os.sep so keys are
        # portable across platforms.
        return {
            "project_key": project_key,
            "session_id": second,
            "subpath": "/".join(subpath_parts),
        }

    return None
