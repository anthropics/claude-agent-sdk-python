"""Postgres-backed :class:`~claude_agent_sdk.SessionStore` reference adapter.

This is a **reference implementation** demonstrating that the
:class:`~claude_agent_sdk.SessionStore` protocol generalizes to a relational
backend. It is not shipped as part of the SDK; copy it into your project and
adapt as needed (add migrations, partitioning, retention sweeps, etc.).

Requires ``asyncpg`` (the native asyncio Postgres driver). Install with::

    pip install asyncpg

Usage::

    import asyncpg
    from claude_agent_sdk import ClaudeAgentOptions, query

    from postgres_session_store import PostgresSessionStore

    pool = await asyncpg.create_pool("postgresql://...")
    store = PostgresSessionStore(pool=pool)
    await store.create_schema()  # one-time, idempotent

    async for message in query(
        prompt="Hello!",
        options=ClaudeAgentOptions(session_store=store),
    ):
        ...  # messages are mirrored to Postgres as they stream

Schema (one row per transcript entry; ``seq`` orders entries within a key)::

    CREATE TABLE IF NOT EXISTS claude_session_store (
      project_key text   NOT NULL,
      session_id  text   NOT NULL,
      subpath     text   NOT NULL DEFAULT '',
      seq         bigserial,
      entry       jsonb  NOT NULL,
      mtime       bigint NOT NULL,
      PRIMARY KEY (project_key, session_id, subpath, seq)
    );
    CREATE INDEX IF NOT EXISTS claude_session_store_list_idx
      ON claude_session_store (project_key, session_id) WHERE subpath = '';

The empty string is the ``subpath`` sentinel for the main transcript so the
composite primary key is total (Postgres treats ``NULL`` as distinct in PKs).

JSONB key ordering
------------------
Entries are stored as ``jsonb``, which **reorders object keys** on read-back
(shorter keys first, then by byte order — see the Postgres docs). This is
explicitly allowed by the :class:`~claude_agent_sdk.SessionStore` contract:
:meth:`~claude_agent_sdk.SessionStore.load` requires *deep-equal*, not
*byte-equal*, returns. The SDK never hashes or byte-compares stored entries,
and the ``*_from_store`` read helpers hoist ``"type"`` to the first key when
re-serializing so the SDK's lite-parse tag scan still works. If you need
byte-stable storage, switch the column to ``json`` (preserves text as-is) or
``text`` and ``json.dumps`` yourself.

Retention: this adapter never deletes rows on its own. Add a scheduled
``DELETE ... WHERE mtime < $cutoff`` (or table partitioning by ``mtime``) to
expire transcripts according to your compliance requirements. Local-disk
transcripts under ``CLAUDE_CONFIG_DIR`` are swept independently by the CLI's
``cleanupPeriodDays`` setting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
)

if TYPE_CHECKING:
    import asyncpg

#: Conservative identifier guard for the table name. The name is interpolated
#: into DDL/DML (asyncpg cannot parameterize identifiers), so reject anything
#: that isn't a plain ``[A-Za-z_][A-Za-z0-9_]*`` to rule out injection.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class PostgresSessionStoreOptions:
    """Configuration for :class:`PostgresSessionStore`."""

    pool: asyncpg.Pool
    """Pre-configured ``asyncpg.Pool``. Caller controls DSN, auth, TLS,
    pool sizing, etc. A pool (not a single connection) is required so the
    adapter can be shared across concurrent batcher flushes."""

    table: str = "claude_session_store"
    """Table name. Must match ``[A-Za-z_][A-Za-z0-9_]*`` — it is interpolated
    directly into SQL (identifiers cannot be parameterized)."""


class PostgresSessionStore(SessionStore):
    """Postgres-backed :class:`~claude_agent_sdk.SessionStore`.

    One row per transcript entry. ``append()`` is a single multi-row
    ``INSERT``; ``load()`` is ``SELECT entry ... ORDER BY seq``.

    Args:
        pool: Pre-configured ``asyncpg.Pool``.
        table: Table name (default ``"claude_session_store"``). Must be a
            plain identifier — validated against ``[A-Za-z_][A-Za-z0-9_]*``.
        options: Alternative to positional args; takes precedence if given.
    """

    def __init__(
        self,
        pool: asyncpg.Pool | None = None,
        table: str = "claude_session_store",
        *,
        options: PostgresSessionStoreOptions | None = None,
    ) -> None:
        if options is not None:
            pool = options.pool
            table = options.table
        if pool is None:
            raise ValueError("PostgresSessionStore requires 'pool'")
        if not _IDENT_RE.match(table):
            raise ValueError(
                f"table {table!r} must match [A-Za-z_][A-Za-z0-9_]* "
                "(it is interpolated into SQL)"
            )
        self._pool = pool
        self._table = table

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def create_schema(self) -> None:
        """Create the table and listing index if absent. Idempotent.

        Call once at startup (or run the equivalent migration out-of-band).
        The partial index on ``subpath = ''`` keeps :meth:`list_sessions`
        cheap without indexing every subagent row.
        """
        # f-string interpolation of self._table is safe: validated against
        # _IDENT_RE in __init__.
        await self._pool.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
              project_key text   NOT NULL,
              session_id  text   NOT NULL,
              subpath     text   NOT NULL DEFAULT '',
              seq         bigserial,
              entry       jsonb  NOT NULL,
              mtime       bigint NOT NULL,
              PRIMARY KEY (project_key, session_id, subpath, seq)
            );
            CREATE INDEX IF NOT EXISTS {self._table}_list_idx
              ON {self._table} (project_key, session_id) WHERE subpath = '';
            """
        )

    # ------------------------------------------------------------------
    # SessionStore protocol
    # ------------------------------------------------------------------

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        subpath = key.get("subpath") or ""
        # Single round-trip multi-row INSERT: unnest() the jsonb[] payload so
        # the whole batch lands in one statement (atomic, ordered by array
        # position via WITH ORDINALITY, one bigserial draw per row).
        await self._pool.execute(
            f"""
            INSERT INTO {self._table} (project_key, session_id, subpath, entry, mtime)
            SELECT $1, $2, $3, e,
                   (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint
            FROM unnest($4::jsonb[]) WITH ORDINALITY AS t(e, ord)
            ORDER BY ord
            """,
            key["project_key"],
            key["session_id"],
            subpath,
            [json.dumps(e) for e in entries],
        )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        rows = await self._pool.fetch(
            f"""
            SELECT entry FROM {self._table}
            WHERE project_key = $1 AND session_id = $2 AND subpath = $3
            ORDER BY seq
            """,
            key["project_key"],
            key["session_id"],
            key.get("subpath") or "",
        )
        if not rows:
            return None
        # asyncpg returns jsonb as the raw JSON text by default (no codec
        # registered on the pool); decode each row. If a jsonb codec IS
        # registered, the value is already a dict — pass it through.
        out: list[SessionStoreEntry] = []
        for row in rows:
            v = row["entry"]
            out.append(json.loads(v) if isinstance(v, (str, bytes)) else v)
        return out

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        rows = await self._pool.fetch(
            f"""
            SELECT session_id, MAX(mtime) AS mtime FROM {self._table}
            WHERE project_key = $1 AND subpath = ''
            GROUP BY session_id
            """,
            project_key,
        )
        return [{"session_id": r["session_id"], "mtime": int(r["mtime"])} for r in rows]

    async def delete(self, key: SessionKey) -> None:
        subpath = key.get("subpath")
        if subpath:
            # Targeted: remove just this subpath's rows.
            await self._pool.execute(
                f"""
                DELETE FROM {self._table}
                WHERE project_key = $1 AND session_id = $2 AND subpath = $3
                """,
                key["project_key"],
                key["session_id"],
                subpath,
            )
            return
        # Cascade: main + every subpath under this (project_key, session_id).
        await self._pool.execute(
            f"""
            DELETE FROM {self._table}
            WHERE project_key = $1 AND session_id = $2
            """,
            key["project_key"],
            key["session_id"],
        )

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        rows = await self._pool.fetch(
            f"""
            SELECT DISTINCT subpath FROM {self._table}
            WHERE project_key = $1 AND session_id = $2 AND subpath <> ''
            """,
            key["project_key"],
            key["session_id"],
        )
        return [r["subpath"] for r in rows]
