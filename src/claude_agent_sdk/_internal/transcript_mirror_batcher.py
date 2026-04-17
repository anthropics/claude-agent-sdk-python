"""Batching layer between ``transcript_mirror`` stdout frames and a SessionStore.

The CLI subprocess emits ``{"type": "transcript_mirror", "filePath": ..., "entries": [...]}``
frames interleaved with normal SDK messages. The receive loop peels these off
and hands them to :class:`TranscriptMirrorBatcher.enqueue`, which accumulates
them and flushes to :meth:`SessionStore.append` either when a ``result``
message arrives (explicit flush) or when the pending buffer exceeds size
thresholds (eager background flush). This keeps adapter latency off the
hot path during model streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..types import SessionKey, SessionStore, SessionStoreEntry
from .session_store import file_path_to_session_key

logger = logging.getLogger(__name__)

# Eager-flush thresholds. Exported for tests.
MAX_PENDING_ENTRIES = 500
MAX_PENDING_BYTES = 1 << 20  # 1 MiB
SEND_TIMEOUT_SECONDS = 60.0


@dataclass
class _MirrorEntry:
    file_path: str
    entries: list[SessionStoreEntry]
    bytes: int


@dataclass
class TranscriptMirrorBatcher:
    """Accumulates ``transcript_mirror`` frames and flushes them to a store.

    ``enqueue`` is fire-and-forget; ``flush`` is async. The pending queue is
    bounded — when it exceeds ``max_pending_entries`` or ``max_pending_bytes``
    an eager flush fires in the background so memory stays flat during long
    turns where no ``result`` (and thus no explicit ``flush()``) arrives.

    Adapter failures are reported via ``on_error`` and the failed batch is
    dropped (at-most-once delivery). Failures never raise — the local-disk
    transcript is already durable so the session must continue unaffected.
    """

    store: SessionStore
    projects_dir: str
    on_error: Callable[[SessionKey | None, str], Awaitable[None]]
    send_timeout: float = SEND_TIMEOUT_SECONDS
    max_pending_entries: int = MAX_PENDING_ENTRIES
    max_pending_bytes: int = MAX_PENDING_BYTES

    _pending: list[_MirrorEntry] = field(default_factory=list)
    _pending_entries: int = 0
    _pending_bytes: int = 0
    _flush_task: asyncio.Task[None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def enqueue(self, file_path: str, entries: list[SessionStoreEntry]) -> None:
        """Buffer a frame; schedule an eager flush if thresholds are exceeded."""
        # Approximate wire size — one stringify per frame (not per entry) keeps
        # this cheap relative to the json.loads the transport already did.
        size = len(json.dumps(entries))
        self._pending.append(_MirrorEntry(file_path, entries, size))
        self._pending_entries += len(entries)
        self._pending_bytes += size
        if (
            self._pending_entries > self.max_pending_entries
            or self._pending_bytes > self.max_pending_bytes
        ):
            # Fire-and-forget; the lock serializes against any in-flight flush
            # so append ordering holds. drain() never raises, but guard anyway
            # so a future regression can't surface as an unhandled exception.
            self._flush_task = asyncio.ensure_future(self._drain())
            self._flush_task.add_done_callback(lambda t: t.exception())

    async def flush(self) -> None:
        """Flush all pending entries. Awaits any in-flight eager flush first."""
        task = asyncio.ensure_future(self._drain())
        self._flush_task = task
        await task
        # Compare-and-null: enqueue() may have started a newer drain while we
        # were awaiting; don't clobber it or the next drain() would miss the
        # serialization wait and could overlap.
        if self._flush_task is task:
            self._flush_task = None

    async def close(self) -> None:
        """Final flush before teardown. Never raises."""
        try:
            await self.flush()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"[TranscriptMirrorBatcher] close flush failed: {e}")

    async def _drain(self) -> None:
        """Detach the pending buffer, await any prior flush, then send.

        Detaching happens before acquiring the lock so ``enqueue`` can keep
        accumulating into a fresh buffer while a prior flush is in flight.
        Never raises — adapter and ``on_error`` callback errors are caught
        and logged.
        """
        items = self._pending
        self._pending = []
        self._pending_entries = 0
        self._pending_bytes = 0
        errors: list[tuple[SessionKey, str]] = []
        async with self._lock:
            if not items:
                return
            try:
                await self._do_flush(items, errors)
            except Exception as e:  # pragma: no cover - defensive
                # _do_flush already wraps store.append; this guards any
                # remaining unguarded path so the "Never raises" contract
                # holds against future regressions.
                logger.error("[TranscriptMirrorBatcher] _do_flush raised: %s", e)
                return
        # Report errors after releasing the lock so a slow on_error callback
        # cannot block subsequent drains (which only need the lock for
        # append-ordering).
        for key, msg in errors:
            try:
                await self.on_error(key, msg)
            except Exception as cb_err:  # pragma: no cover - defensive
                logger.error(
                    "[TranscriptMirrorBatcher] on_error callback raised: %s",
                    cb_err,
                )

    async def _do_flush(
        self, items: list[_MirrorEntry], errors: list[tuple[SessionKey, str]]
    ) -> None:
        # Coalesce by file_path so each unique file gets one append per flush
        # instead of one per enqueued frame. dict preserves first-seen order;
        # entries within a path keep enqueue order.
        by_path: dict[str, list[SessionStoreEntry]] = {}
        for item in items:
            bucket = by_path.get(item.file_path)
            if bucket is not None:
                bucket.extend(item.entries)
            else:
                by_path[item.file_path] = list(item.entries)

        for file_path, entries in by_path.items():
            if not entries:
                # Avoid creating phantom keys in adapters that touch storage
                # on append([]) — nothing to write.
                continue
            key = file_path_to_session_key(file_path, self.projects_dir)
            if key is None:
                logger.warning(
                    "[SessionStore] dropping mirror frame: filePath %s is not "
                    "under %s -- subprocess CLAUDE_CONFIG_DIR likely differs "
                    "from parent (custom env / container?)",
                    file_path,
                    self.projects_dir,
                )
                continue
            try:
                await asyncio.wait_for(
                    self.store.append(key, entries), timeout=self.send_timeout
                )
            except Exception as e:
                logger.error(
                    "[TranscriptMirrorBatcher] flush failed for %s: %s", file_path, e
                )
                errors.append((key, str(e)))
