"""MongoDB-backed :class:`~claude_agent_sdk.SessionStore` reference adapter.

This is a **reference implementation** demonstrating that the
:class:`~claude_agent_sdk.SessionStore` protocol generalizes to a document
store. It is not shipped as part of the SDK; copy it into your project and
adapt as needed (add migrations, sharding, retention sweeps, etc.). This
mirrors the ``MongoDBSessionStore`` reference implementation from the
TypeScript SDK.

Requires ``pymongo>=4.13`` (the stable async API). Install with::

    pip install pymongo

Usage::

    from pymongo import AsyncMongoClient
    from claude_agent_sdk import ClaudeAgentOptions, query

    from mongodb_session_store import MongoDBSessionStore

    client = AsyncMongoClient("mongodb://localhost:27017")
    store = MongoDBSessionStore(client=client, db_name="claude")
    await store.create_schema()  # one-time, idempotent

    async for message in query(
        prompt="Hello!",
        options=ClaudeAgentOptions(session_store=store),
    ):
        ...  # messages are mirrored to MongoDB as they stream

Schema
------
Two collections share a single database:

``claude_session_entries`` — one document per JSONL entry::

    {
      _id: ObjectId,                    # ordering key (server-assigned)
      project_key: str,
      session_id:  str,
      subpath:     str,                 # "" sentinel for main transcript
      entry:       <opaque JSON>,
      mtime:       int,                 # Unix epoch ms, write-time stamp
    }

``claude_session_summaries`` — one document per main session, maintained
incrementally inside :meth:`MongoDBSessionStore.append` via
:func:`~claude_agent_sdk.fold_session_summary`::

    {
      _id:   {project_key: str, session_id: str},
      mtime: int,                       # Unix epoch ms (same clock as entries)
      data:  <opaque SDK-owned dict>,
    }

The empty string is the ``subpath`` sentinel for the main transcript so the
``(project_key, session_id, subpath)`` triple is total (mirrors the Postgres
adapter's convention).

Concurrency
-----------
Per the :meth:`SessionStore.list_session_summaries` contract, stores
maintaining sidecars inside ``append()`` must serialize the read-fold-write
when ``append()`` calls can race for the same session. This adapter holds a
per-session ``asyncio.Lock`` keyed by ``(project_key, session_id)`` for the
duration of the summary update. The SDK's own ``TranscriptMirrorBatcher``
already sequences appends per session within one process, but a user could
share one store instance across multiple concurrent batchers — the lock keeps
the fold deterministic in that case.

Retention
---------
This adapter never deletes documents on its own. Add a TTL index on ``mtime``
or a scheduled ``delete_many({"mtime": {"$lt": cutoff}})`` to expire
transcripts according to your compliance requirements. Local-disk
transcripts under ``CLAUDE_CONFIG_DIR`` are swept independently by the CLI's
``cleanupPeriodDays`` setting.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
    SessionSummaryEntry,
)
from claude_agent_sdk._internal.session_summary import fold_session_summary

if TYPE_CHECKING:
    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.collection import AsyncCollection
    from pymongo.asynchronous.database import AsyncDatabase

#: Conservative collection-name guard. Mongo allows ``.`` for namespacing but
#: ``$`` and null bytes are invalid; reject anything that isn't a plain
#: ``[A-Za-z_][A-Za-z0-9_.]*`` to head off injection-like footguns.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

#: Sentinel used in entry documents to mark the main transcript. The SDK
#: never emits an empty subpath; treating ``key.get("subpath") or ""`` as the
#: sentinel keeps the Mongo query and Postgres adapter aligned.
_MAIN: str = ""


@dataclass
class MongoDBSessionStoreOptions:
    """Configuration for :class:`MongoDBSessionStore`."""

    client: AsyncMongoClient[dict[str, Any]]
    """Pre-configured ``pymongo.AsyncMongoClient``. Caller controls URI,
    auth, TLS, pool sizing, server selection, etc."""

    db_name: str | None = None
    """Database name. Falls back to the client's default database (i.e. the
    one named in the connection URI) when ``None``."""

    entries_collection: str = "claude_session_entries"
    """Collection name for transcript entries. Must match
    ``[A-Za-z_][A-Za-z0-9_.]*``."""

    summaries_collection: str = "claude_session_summaries"
    """Collection name for the per-session summary sidecar. Must match
    ``[A-Za-z_][A-Za-z0-9_.]*``."""


class MongoDBSessionStore(SessionStore):
    """MongoDB-backed :class:`~claude_agent_sdk.SessionStore`.

    One document per transcript entry; ordering via the server-assigned
    ``_id`` (``ObjectId``). ``append()`` is a single ``insert_many``;
    ``load()`` is ``find().sort("_id", 1)``.

    Args:
        client: Pre-configured ``pymongo.AsyncMongoClient``.
        db_name: Database name (default: the client's default DB).
        entries_collection: Collection for entry documents
            (default ``"claude_session_entries"``).
        summaries_collection: Collection for summary sidecars
            (default ``"claude_session_summaries"``).
        options: Alternative to positional args; takes precedence if given.
    """

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, Any]] | None = None,
        db_name: str | None = None,
        entries_collection: str = "claude_session_entries",
        summaries_collection: str = "claude_session_summaries",
        *,
        options: MongoDBSessionStoreOptions | None = None,
    ) -> None:
        if options is not None:
            client = options.client
            db_name = options.db_name
            entries_collection = options.entries_collection
            summaries_collection = options.summaries_collection
        if client is None:
            raise ValueError("MongoDBSessionStore requires 'client'")
        for label, name in (
            ("entries_collection", entries_collection),
            ("summaries_collection", summaries_collection),
        ):
            if not _IDENT_RE.match(name):
                raise ValueError(
                    f"{label} {name!r} must match [A-Za-z_][A-Za-z0-9_.]*"
                )

        self._db: AsyncDatabase[dict[str, Any]] = (
            client[db_name] if db_name is not None else client.get_default_database()
        )
        self._entries: AsyncCollection[dict[str, Any]] = self._db[entries_collection]
        self._summaries: AsyncCollection[dict[str, Any]] = self._db[
            summaries_collection
        ]
        # Per-session locks for the read-fold-write summary update. Keys are
        # (project_key, session_id); locks are created lazily and never
        # garbage-collected — this is reference code, not a long-running
        # service.
        self._summary_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _summary_lock(self, key: SessionKey) -> asyncio.Lock:
        slot = (key["project_key"], key["session_id"])
        lock = self._summary_locks.get(slot)
        if lock is None:
            lock = asyncio.Lock()
            self._summary_locks[slot] = lock
        return lock

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def create_schema(self) -> None:
        """Create the indexes if absent. Idempotent.

        Call once at startup (or run the equivalent migration out-of-band).
        Each index is independently named so re-running the call is a no-op
        in the steady state.
        """
        await self._entries.create_index(
            [("project_key", 1), ("session_id", 1), ("subpath", 1), ("_id", 1)],
            name="key_idx",
        )
        await self._entries.create_index(
            [("project_key", 1), ("subpath", 1), ("mtime", -1)],
            name="sessions_idx",
        )
        await self._summaries.create_index(
            [("_id.project_key", 1), ("mtime", -1)],
            name="summaries_idx",
        )

    # ------------------------------------------------------------------
    # SessionStore protocol
    # ------------------------------------------------------------------

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        subpath = key.get("subpath") or _MAIN
        now = int(time.time() * 1000)
        docs: list[dict[str, Any]] = [
            {
                "project_key": key["project_key"],
                "session_id": key["session_id"],
                "subpath": subpath,
                "entry": dict(entry),
                "mtime": now,
            }
            for entry in entries
        ]
        # ordered=True preserves intra-batch order; the server-assigned
        # ObjectId is monotonic per writer so inter-batch order is preserved
        # too without an explicit sequence column.
        await self._entries.insert_many(docs, ordered=True)

        # Subagent transcripts must NOT contribute to the main session's
        # summary — guard before the fold (per fold_session_summary docs).
        if subpath != _MAIN:
            return

        compound_id = {
            "project_key": key["project_key"],
            "session_id": key["session_id"],
        }
        async with self._summary_lock(key):
            prev_doc = await self._summaries.find_one({"_id": compound_id})
            prev: SessionSummaryEntry | None = (
                {
                    "session_id": prev_doc["_id"]["session_id"],
                    "mtime": int(prev_doc["mtime"]),
                    "data": prev_doc["data"],
                }
                if prev_doc is not None
                else None
            )
            folded = fold_session_summary(prev, key, entries)
            new_doc = {
                "_id": compound_id,
                "mtime": now,
                "data": folded["data"],
            }
            await self._summaries.replace_one(
                {"_id": compound_id}, new_doc, upsert=True
            )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        cursor = self._entries.find(
            {
                "project_key": key["project_key"],
                "session_id": key["session_id"],
                "subpath": key.get("subpath") or _MAIN,
            }
        ).sort("_id", 1)
        docs = await cursor.to_list(length=None)
        if not docs:
            return None
        return [d["entry"] for d in docs]

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        # ``aggregate()`` is itself awaitable in pymongo's async API (returns
        # a cursor); ``find()`` is not (returns the cursor synchronously).
        cursor = await self._entries.aggregate(
            [
                {"$match": {"project_key": project_key, "subpath": _MAIN}},
                {
                    "$group": {
                        "_id": "$session_id",
                        "mtime": {"$max": "$mtime"},
                    }
                },
            ]
        )
        rows = await cursor.to_list(length=None)
        return [
            {"session_id": str(r["_id"]), "mtime": int(r["mtime"])} for r in rows
        ]

    async def list_session_summaries(
        self, project_key: str
    ) -> list[SessionSummaryEntry]:
        cursor = self._summaries.find({"_id.project_key": project_key})
        docs = await cursor.to_list(length=None)
        return [
            {
                "session_id": d["_id"]["session_id"],
                "mtime": int(d["mtime"]),
                "data": d["data"],
            }
            for d in docs
        ]

    async def delete(self, key: SessionKey) -> None:
        subpath = key.get("subpath")
        if subpath:
            # Targeted: remove only this subpath's entries; do NOT touch the
            # summary sidecar (which represents the main transcript).
            await self._entries.delete_many(
                {
                    "project_key": key["project_key"],
                    "session_id": key["session_id"],
                    "subpath": subpath,
                }
            )
            return
        # Cascade: main + every subpath under (project_key, session_id),
        # plus the summary sidecar.
        await self._entries.delete_many(
            {
                "project_key": key["project_key"],
                "session_id": key["session_id"],
            }
        )
        await self._summaries.delete_one(
            {
                "_id": {
                    "project_key": key["project_key"],
                    "session_id": key["session_id"],
                }
            }
        )

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        result = await self._entries.distinct(
            "subpath",
            {
                "project_key": key["project_key"],
                "session_id": key["session_id"],
                "subpath": {"$ne": _MAIN},
            },
        )
        return list(result)
