"""S3-backed :class:`~claude_agent_sdk.SessionStore` adapter.

This is a reference implementation — copy it into your own project and adapt
as needed. It mirrors the S3 reference adapter in the TypeScript SDK
(examples/session-stores/s3/).

Transcripts are stored as JSONL part files::

    s3://{bucket}/{prefix}{project_key}/{session_id}/part-{epochMs13}-{rand6}.jsonl

Each :meth:`S3SessionStore.append` writes a new part; :meth:`load` lists,
sorts, and concatenates them. The 13-digit zero-padded epoch-ms prefix means
lexical key order == chronological order. A per-instance monotonic millisecond
counter orders same-instance same-ms appends; the random hex suffix
disambiguates concurrent instances.

Requires ``boto3`` (not a dependency of ``claude-agent-sdk`` — install it
yourself)::

    pip install boto3

Usage::

    import boto3
    from claude_agent_sdk import ClaudeAgentOptions, query

    store = S3SessionStore(
        bucket="my-claude-sessions",
        prefix="transcripts",
        client=boto3.client("s3", region_name="us-east-1"),
    )

    async for message in query(
        prompt="Hello!",
        options=ClaudeAgentOptions(session_store=store),
    ):
        ...  # messages are mirrored to S3 automatically

Retention: this adapter never deletes objects on its own. Configure an S3
lifecycle policy on the bucket/prefix to expire transcripts according to your
compliance requirements. :meth:`delete` is implemented but only invoked when
you call ``delete_session_via_store()`` from the SDK.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk.types import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
)

# Bounded-parallel GetObject so load() isn't N×RTT serial.
_LOAD_CONCURRENCY = 16

_PART_MTIME_RE = re.compile(r"/part-(\d{13})-[0-9a-f]{6}\.jsonl$")


@dataclass
class S3SessionStoreOptions:
    """Configuration for :class:`S3SessionStore`."""

    bucket: str
    """S3 bucket name."""

    client: Any
    """Pre-configured ``boto3`` S3 client. Caller controls region, credentials,
    endpoint, etc. Typed as ``Any`` so this example does not depend on
    ``boto3`` stubs."""

    prefix: str = ""
    """Optional key prefix (e.g. ``"transcripts"``). Trailing slash is
    normalized — non-empty values always end in exactly one ``/``."""


class S3SessionStore(SessionStore):
    """S3-backed :class:`SessionStore`.

    ``append()`` = ``PutObject`` of a new part file
    ``{prefix}{project_key}/{session_id}/part-{epochMs13}-{rand6}.jsonl``;
    ``load()`` = ``ListObjectsV2`` + sort + bounded-parallel ``GetObject`` +
    concat. Monotonic ms orders same-instance same-ms appends; rand suffix
    disambiguates instances.

    All ``boto3`` calls are wrapped in :func:`asyncio.to_thread` so the event
    loop is never blocked. ``boto3`` clients are thread-safe.
    """

    def __init__(
        self,
        bucket: str | None = None,
        prefix: str = "",
        client: Any = None,
        *,
        options: S3SessionStoreOptions | None = None,
    ) -> None:
        if options is not None:
            bucket = options.bucket
            prefix = options.prefix
            client = options.client
        if bucket is None or client is None:
            raise ValueError("S3SessionStore requires 'bucket' and 'client'")
        self._bucket = bucket
        # Normalize: non-empty prefix always ends in exactly one '/'; empty
        # stays empty.
        self._prefix = (prefix.rstrip("/") + "/") if prefix else ""
        self._client = client
        self._last_ms = 0

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _key_prefix(self, key: SessionKey) -> str:
        """Directory prefix for a session (or subpath). Always ends in ``/``."""
        parts = [key["project_key"], key["session_id"]]
        subpath = key.get("subpath")
        if subpath:
            parts.append(subpath)
        return self._prefix + "/".join(parts) + "/"

    def _project_prefix(self, project_key: str) -> str:
        """Directory prefix for a project. Always ends in ``/``."""
        return self._prefix + project_key + "/"

    def _next_part_name(self) -> str:
        """Fixed-width epoch ms → lexical sort = chronological.

        ``last_ms + 1`` makes same-instance same-ms appends deterministic;
        ``rand`` disambiguates instances.
        """
        now = int(time.time() * 1000)
        ms = max(now, self._last_ms + 1)
        self._last_ms = ms
        rand = secrets.token_hex(3)  # 6 lowercase hex chars
        return f"part-{ms:013d}-{rand}.jsonl"

    # ------------------------------------------------------------------
    # boto3 wrappers (sync → asyncio.to_thread)
    # ------------------------------------------------------------------

    async def _put_object(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._client.put_object, **kwargs)

    async def _list_objects_v2(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._client.list_objects_v2, **kwargs)

    async def _get_object(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._client.get_object, **kwargs)

    async def _delete_objects(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._client.delete_objects, **kwargs)

    # ------------------------------------------------------------------
    # SessionStore protocol
    # ------------------------------------------------------------------

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        object_key = self._key_prefix(key) + self._next_part_name()
        body = "\n".join(json.dumps(e, separators=(",", ":")) for e in entries) + "\n"
        await self._put_object(
            Bucket=self._bucket,
            Key=object_key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        prefix = self._key_prefix(key)

        # List part files directly under this prefix only. Without Delimiter,
        # S3 recurses into subpaths (e.g. subagents/*), so a main-transcript
        # load({project_key, session_id}) would mix in subagent entries —
        # diverging from InMemorySessionStore's exact-key semantics and
        # corrupting resume.
        keys: list[str] = []
        continuation_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self._bucket,
                "Prefix": prefix,
                "Delimiter": "/",
            }
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            result = await self._list_objects_v2(**kwargs)
            for obj in result.get("Contents") or []:
                k = obj.get("Key")
                # Guard against S3-compatibles that ignore Delimiter: keep only
                # direct children (part files have no '/' after the prefix).
                if k and "/" not in k[len(prefix) :]:
                    keys.append(k)
            continuation_token = result.get("NextContinuationToken")
            if not continuation_token:
                break

        if not keys:
            return None

        # 13-digit epochMs prefix is fixed-width, so lexical == chronological.
        keys.sort()

        # Bounded-parallel GetObject (serial is N×RTT); preserves sorted-key
        # order via slot-indexed result list.
        bodies: list[str | None] = [None] * len(keys)
        sem = asyncio.Semaphore(_LOAD_CONCURRENCY)

        async def fetch(i: int, object_key: str) -> None:
            async with sem:
                result = await self._get_object(Bucket=self._bucket, Key=object_key)
                raw = await asyncio.to_thread(result["Body"].read)
                bodies[i] = (
                    raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
                )

        await asyncio.gather(*(fetch(i, k) for i, k in enumerate(keys)))

        all_entries: list[SessionStoreEntry] = []
        for body in bodies:
            if not body:
                continue
            for line in body.split("\n"):
                trimmed = line.strip()
                if not trimmed:
                    continue
                # Skip malformed lines.
                with contextlib.suppress(json.JSONDecodeError, ValueError):
                    all_entries.append(json.loads(trimmed))

        return all_entries if all_entries else None

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        prefix = self._project_prefix(project_key)
        sessions: dict[str, int] = {}
        continuation_token: str | None = None

        # List Contents (no Delimiter) so we can derive mtime from each part
        # filename's 13-digit epochMs prefix. CommonPrefixes carry no
        # timestamp.
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            result = await self._list_objects_v2(**kwargs)
            for obj in result.get("Contents") or []:
                k = obj.get("Key")
                if not k:
                    continue
                # {prefix}{session_id}/part-{epochMs13}-{rand}.jsonl
                rest = k[len(prefix) :]
                slash = rest.find("/")
                if slash == -1:
                    continue
                # Main-transcript parts only (one level under session_id);
                # deeper keys are subagent parts and would surface phantom
                # session_ids / skew mtime.
                if rest.find("/", slash + 1) != -1:
                    continue
                session_id = rest[:slash]
                m = _PART_MTIME_RE.search(k)
                if m:
                    mtime = int(m.group(1))
                else:
                    last_modified = obj.get("LastModified")
                    mtime = (
                        int(last_modified.timestamp() * 1000) if last_modified else 0
                    )
                if mtime > sessions.get(session_id, 0):
                    sessions[session_id] = mtime
            continuation_token = result.get("NextContinuationToken")
            if not continuation_token:
                break

        return [{"session_id": sid, "mtime": mtime} for sid, mtime in sessions.items()]

    async def delete(self, key: SessionKey) -> None:
        prefix = self._key_prefix(key)
        # Match InMemorySessionStore: whole-session delete cascades into
        # subpaths; delete({subpath:'a'}) is exact-key only (must NOT touch
        # 'a/b').
        direct_only = key.get("subpath") is not None
        continuation_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if direct_only:
                kwargs["Delimiter"] = "/"
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            result = await self._list_objects_v2(**kwargs)
            to_delete: list[dict[str, str]] = []
            for obj in result.get("Contents") or []:
                k = obj.get("Key")
                if not k:
                    continue
                if direct_only and "/" in k[len(prefix) :]:
                    continue
                to_delete.append({"Key": k})
            if to_delete:
                del_result = await self._delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": to_delete, "Quiet": True},
                )
                errors = del_result.get("Errors") or []
                if errors:
                    detail = ", ".join(
                        f"{e.get('Key')}: {e.get('Code')}" for e in errors
                    )
                    raise RuntimeError(
                        f"S3 delete failed for {len(errors)} object(s): {detail}"
                    )
            continuation_token = result.get("NextContinuationToken")
            if not continuation_token:
                break

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        prefix = self._key_prefix(
            {"project_key": key["project_key"], "session_id": key["session_id"]}
        )
        subkeys: set[str] = set()
        continuation_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            result = await self._list_objects_v2(**kwargs)
            for obj in result.get("Contents") or []:
                k = obj.get("Key")
                if not k:
                    continue
                # Extract subpath from key:
                # {prefix}{project_key}/{session_id}/{subpath}/part-{epochMs}-{rand}.jsonl
                rel = k[len(prefix) :]
                parts = rel.split("/")
                if len(parts) >= 2:
                    # subpath is everything except the last segment (the part
                    # file)
                    subpath = "/".join(parts[:-1])
                    if subpath:
                        subkeys.add(subpath)
            continuation_token = result.get("NextContinuationToken")
            if not continuation_token:
                break

        # Defense-in-depth: drop '..'/'.'/'' segments (never produced by legit
        # writers). Primary traversal guard stays in materialize_resume_session.
        return [
            sp
            for sp in subkeys
            if not any(seg in ("..", ".", "") for seg in sp.split("/"))
        ]


@dataclass
class _RecordingClient:
    """Minimal in-memory S3 client double for unit tests.

    Implements only the four methods :class:`S3SessionStore` calls. Honors
    ``Prefix`` and ``Delimiter='/'`` (returns only direct children in
    ``Contents``). Records every call so tests can assert on operation
    sequences without a network round-trip.
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        body = kwargs["Body"]
        self.objects[kwargs["Key"]] = (
            body.encode("utf-8") if isinstance(body, str) else body
        )
        return {}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_objects_v2", kwargs))
        prefix = kwargs.get("Prefix", "")
        delimiter = kwargs.get("Delimiter")
        contents = []
        for k in self.objects:
            if not k.startswith(prefix):
                continue
            if delimiter == "/" and "/" in k[len(prefix) :]:
                continue
            contents.append({"Key": k})
        return {"Contents": contents}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        import io

        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_objects", kwargs))
        for obj in kwargs["Delete"]["Objects"]:
            self.objects.pop(obj["Key"], None)
        return {}
