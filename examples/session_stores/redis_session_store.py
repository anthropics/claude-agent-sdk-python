"""Redis-backed :class:`~claude_agent_sdk.SessionStore` reference adapter.

This is a **reference implementation** demonstrating that the
:class:`~claude_agent_sdk.SessionStore` protocol generalizes to a non-blob
backend. It is not shipped as part of the SDK; copy it into your project and
adapt as needed. This mirrors the ``RedisSessionStore`` reference
implementation from the TypeScript SDK.

Requires ``redis>=4.2`` (the ``redis.asyncio`` client). Install with::

    pip install redis

Usage::

    import redis.asyncio as redis
    from claude_agent_sdk import ClaudeAgentOptions, query

    from redis_session_store import RedisSessionStore

    store = RedisSessionStore(
        client=redis.Redis(host="localhost", port=6379, decode_responses=True),
        prefix="transcripts",
    )

    async for message in query(
        prompt="Hello!",
        options=ClaudeAgentOptions(session_store=store),
    ):
        ...  # messages are mirrored to Redis as they stream

Key scheme (``:`` separator; ``project_key``/``session_id`` are opaque so
collisions with the SDK's ``/``-based ``project_key`` are avoided)::

    {prefix}:{project_key}:{session_id}             list   main transcript (JSON each)
    {prefix}:{project_key}:{session_id}:{subpath}   list   subagent transcript
    {prefix}:{project_key}:{session_id}:__subkeys   set    subpaths under this session
    {prefix}:{project_key}:__sessions               zset   session_id -> mtime(ms)

Index keys (``__subkeys``, ``__sessions``) live in reserved positions; the SDK
never emits a ``session_id`` of ``__sessions`` or a ``subpath`` of
``__subkeys``.

Retention: this adapter never expires keys on its own. Configure Redis key
expiration on your prefix or call :meth:`RedisSessionStore.delete` according to
your compliance requirements. Local-disk transcripts under
``CLAUDE_CONFIG_DIR`` are swept independently by the CLI's
``cleanupPeriodDays`` setting.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
)

if TYPE_CHECKING:
    import redis.asyncio as redis

#: Reserved subpath sentinel for the per-session subkey set.
_SUBKEYS = "__subkeys"
#: Reserved session_id sentinel for the per-project session index.
_SESSIONS = "__sessions"


class RedisSessionStore(SessionStore):
    """Redis-backed :class:`~claude_agent_sdk.SessionStore`.

    Each ``append()`` is an ``RPUSH`` (plus index update in a single
    ``MULTI``); ``load()`` is ``LRANGE 0 -1``.

    Args:
        client: Pre-configured ``redis.asyncio.Redis`` instance. Caller
            controls host, port, auth, TLS, etc. The client **must** be
            constructed with ``decode_responses=True`` so commands return
            ``str`` (the adapter ``json.loads`` each list element).
        prefix: Optional key prefix (e.g. ``"transcripts"``). Trailing ``:``
            is normalized; an empty prefix produces no leading separator.
    """

    def __init__(self, client: redis.Redis, prefix: str = "") -> None:
        self._client = client
        # Normalize: non-empty prefix always ends in exactly one ':'; empty
        # stays empty so keys never start with a stray separator.
        self._prefix = re.sub(r":+$", "", prefix) + ":" if prefix else ""

    # ------------------------------------------------------------------
    # Key derivation
    # ------------------------------------------------------------------

    def _entry_key(self, key: SessionKey) -> str:
        """Redis key for a transcript list (main or subpath)."""
        parts = [key["project_key"], key["session_id"]]
        subpath = key.get("subpath")
        if subpath:
            parts.append(subpath)
        return self._prefix + ":".join(parts)

    def _subkeys_key(self, key: SessionListSubkeysKey) -> str:
        """Redis key for the per-session subpath set."""
        return f"{self._prefix}{key['project_key']}:{key['session_id']}:{_SUBKEYS}"

    def _sessions_key(self, project_key: str) -> str:
        """Redis key for the per-project session index (sorted set, score=mtime)."""
        return f"{self._prefix}{project_key}:{_SESSIONS}"

    # ------------------------------------------------------------------
    # SessionStore protocol
    # ------------------------------------------------------------------

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        pipe = self._client.pipeline(transaction=True)
        pipe.rpush(self._entry_key(key), *(json.dumps(e) for e in entries))
        subpath = key.get("subpath")
        if subpath:
            pipe.sadd(self._subkeys_key(key), subpath)
        else:
            # Only main-transcript appends bump the session index — matches
            # InMemorySessionStore.list_sessions()'s "no subpath" filter and
            # the S3 adapter's main-parts-only mtime derivation.
            pipe.zadd(
                self._sessions_key(key["project_key"]),
                {key["session_id"]: int(time.time() * 1000)},
            )
        await pipe.execute()

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        raw = await self._client.lrange(self._entry_key(key), 0, -1)
        if not raw:
            return None
        out: list[SessionStoreEntry] = []
        for line in raw:
            # Skip malformed entries (parity with the S3 adapter).
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                out.append(json.loads(line))
        return out if out else None

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        pairs = await self._client.zrange(
            self._sessions_key(project_key), 0, -1, withscores=True
        )
        return [
            {"session_id": session_id, "mtime": int(score)}
            for session_id, score in pairs
        ]

    async def delete(self, key: SessionKey) -> None:
        subpath = key.get("subpath")
        if subpath is not None:
            # Targeted: remove just this subpath list and its index entry.
            pipe = self._client.pipeline(transaction=True)
            pipe.delete(self._entry_key(key))
            pipe.srem(self._subkeys_key(key), subpath)
            await pipe.execute()
            return
        # Cascade: main list + every subpath list + subkey set + session-index entry.
        subkeys_key = self._subkeys_key(key)
        subpaths = await self._client.smembers(subkeys_key)
        to_delete = [self._entry_key(key), subkeys_key]
        to_delete.extend(self._entry_key({**key, "subpath": sp}) for sp in subpaths)
        pipe = self._client.pipeline(transaction=True)
        pipe.delete(*to_delete)
        pipe.zrem(self._sessions_key(key["project_key"]), key["session_id"])
        await pipe.execute()

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        return list(await self._client.smembers(self._subkeys_key(key)))
