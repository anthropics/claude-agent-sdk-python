# SessionStore reference adapters

> **Reference implementations for interface validation. Not packaged, not maintained as production code.**

Reference [`SessionStore`](../../src/claude_agent_sdk/types.py) implementations
— copy into your project, install the backend client, validate with
`run_session_store_conformance`.

These adapters live in `examples/` (not `src/`) so the SDK package stays free
of heavyweight optional dependencies. They are imported and exercised by the
test suite to prove the `SessionStore` protocol generalizes beyond the
in-memory default. Each adapter here passes the full 13-contract conformance
suite.

## Validating your own adapter

When you copy an adapter into your project (or write a new one), assert it
satisfies the protocol's behavioral contracts with the shipped conformance
harness:

```python
import pytest
from claude_agent_sdk.testing import run_session_store_conformance

@pytest.mark.asyncio
async def test_my_store_conformance():
    await run_session_store_conformance(lambda: MyStore(...))
```

## Running the example tests

Install the optional `[examples]` dependency group, then run the unit tests
(they `importorskip` so default CI is unaffected if the group isn't installed):

```bash
pip install -e '.[dev,examples]'
pytest tests/test_example_s3_session_store.py \
       tests/test_example_redis_session_store.py \
       tests/test_example_postgres_session_store.py -v
```

S3 and Redis have in-process mocks (`moto`, `fakeredis`); Postgres is
live-only. The live e2e suites for all three skip unless the corresponding
`SESSION_STORE_*` env vars are set — see each section below.

## Production checklist

These adapters are reference code. Before running one in production, work
through the relevant items below.

### All adapters

- `run_session_store_conformance` proves *correctness*, not *resilience* —
  load-test your adapter under your expected throughput.
- `append()` failures are logged and emit a `MirrorErrorMessage`; they never
  block the conversation. Monitor for these so silent mirror gaps don't go
  unnoticed.

### S3

- Required IAM actions on the bucket/prefix: `s3:PutObject`, `s3:GetObject`,
  `s3:ListBucket`, `s3:DeleteObject`.
- Part-file ordering uses the **client-side wall clock**. Multiple writer
  instances with clock skew >1s may produce out-of-order `load()` results. Use
  NTP or a single writer per session.
- Consider S3 lifecycle policies for retention — the SDK never auto-deletes.
- For sessions with >1000 part files, `load()` paginates correctly but latency
  grows linearly; consider periodic compaction.

### Redis

- Set `maxmemory-policy noeviction` (or use a dedicated DB) — eviction will
  silently drop session data.
- Lists are unbounded; implement TTL via `EXPIRE` in a subclass if needed.
- Redis Cluster: keys with the same `{project_key}:{session_id}` prefix should
  hash to the same slot — wrap in `{...}` hash tags if using Cluster.
- If you derive `project_key` or `session_id` outside the SDK, ensure they
  cannot contain `:` (the key separator) — collisions would mix data across
  keys. The SDK's own `project_key_for_directory()` and UUID session IDs are
  already safe.

### Postgres

- Size the `asyncpg` pool ≥ expected concurrent sessions; don't share a pool
  with request-handler code that holds connections.
- `jsonb` reorders keys — contract-safe, but don't byte-compare entries.
- Add a retention job (`DELETE WHERE mtime < ...`) — the table grows
  unbounded.

---

## S3 — `s3_session_store.py`

Stores transcripts as JSONL part files:

```
s3://{bucket}/{prefix}{project_key}/{session_id}/part-{epochMs13}-{rand6}.jsonl
```

Each `append()` writes a new part; `load()` lists, sorts, and concatenates
them.

### Installation

`boto3` is **not** a dependency of `claude-agent-sdk` — install it yourself:

```bash
pip install claude-agent-sdk boto3
```

### Usage

```python
import anyio
import boto3

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from my_project.stores import S3SessionStore  # your copy of this file

store = S3SessionStore(
    bucket="my-claude-sessions",
    prefix="transcripts",
    client=boto3.client("s3", region_name="us-east-1"),
)


async def main() -> None:
    async for message in query(
        prompt="Hello!",
        options=ClaudeAgentOptions(session_store=store),
    ):
        # Messages are mirrored to S3 automatically.
        if isinstance(message, ResultMessage) and message.subtype == "success":
            print(message.result)


anyio.run(main)
```

### Resume from S3

```python
async for message in query(
    prompt="Continue where we left off",
    options=ClaudeAgentOptions(
        session_store=store,
        resume="previous-session-id",
    ),
):
    ...
```

### Retention

This adapter never deletes objects on its own. Configure an
[S3 lifecycle policy](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html)
on your bucket/prefix to expire transcripts according to your compliance
requirements.

`delete()` is implemented (removes all parts for a session) but is only
called when you invoke `delete_session_via_store()` from the SDK.

Local-disk transcripts under `CLAUDE_CONFIG_DIR` are swept independently by
the CLI's `cleanupPeriodDays` setting.

### Tests

Unit tests use [`moto`](https://github.com/getmoto/moto) to mock S3
in-process:

```bash
pytest tests/test_example_s3_session_store.py -v
```

### Live S3 end-to-end

To run the live e2e suite against a real S3-compatible backend, set the
`SESSION_STORE_S3_*` env vars and the tests will un-skip. For a quick local
MinIO:

```bash
docker run -d -p 9000:9000 minio/minio server /data
# create the bucket once:
docker run --rm --network host minio/mc \
    sh -c 'mc alias set local http://localhost:9000 minioadmin minioadmin && mc mb local/test'

SESSION_STORE_S3_ENDPOINT=http://localhost:9000 \
SESSION_STORE_S3_BUCKET=test \
SESSION_STORE_S3_ACCESS_KEY=minioadmin \
SESSION_STORE_S3_SECRET_KEY=minioadmin \
    pytest tests/test_example_s3_session_store_live.py -v
```

Each run uses a random key prefix and deletes everything under it on
teardown.

This mirrors the S3 reference adapter in the TypeScript SDK's
[`examples/session-stores/s3/`](https://github.com/anthropics/claude-agent-sdk-typescript/tree/main/examples/session-stores/s3).

---

## Redis — `redis_session_store.py`

Backed by [`redis-py`](https://github.com/redis/redis-py)'s `redis.asyncio`
client.

### Installation

```bash
pip install claude-agent-sdk redis
```

### Usage

```python
import redis.asyncio as redis
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from redis_session_store import RedisSessionStore

store = RedisSessionStore(
    client=redis.Redis(host="localhost", port=6379, decode_responses=True),
    prefix="transcripts",
)

async for message in query(
    prompt="Hello!",
    options=ClaudeAgentOptions(session_store=store),
):
    if isinstance(message, ResultMessage) and message.subtype == "success":
        print(message.result)
```

The client **must** be created with `decode_responses=True` — the adapter
`json.loads` each `LRANGE` element and expects `str`, not `bytes`.

### Key scheme

```
{prefix}:{project_key}:{session_id}             list   — main transcript entries (JSON each)
{prefix}:{project_key}:{session_id}:{subpath}   list   — subagent transcript entries
{prefix}:{project_key}:{session_id}:__subkeys   set    — subpaths under this session
{prefix}:{project_key}:__sessions               zset   — session_id → mtime(ms)
```

Each `append()` is an `RPUSH` plus an index update in a single `MULTI`;
`load()` is `LRANGE 0 -1`.

### Retention

This adapter never expires keys on its own. Configure
[Redis key expiration](https://redis.io/docs/latest/commands/expire/) or a
scheduled sweep on your prefix to expire transcripts according to your
compliance requirements.

`delete()` is implemented (cascades to subpath lists and index entries) but is
only called when you invoke `delete_session_via_store()` from the SDK.

Local-disk transcripts under `CLAUDE_CONFIG_DIR` are swept independently by the
CLI's `cleanupPeriodDays` setting.

### Resume from Redis

```python
async for message in query(
    prompt="Continue where we left off",
    options=ClaudeAgentOptions(
        session_store=store,
        resume="previous-session-id",
    ),
):
    ...
```

### Tests

Unit tests use [`fakeredis`](https://github.com/cunla/fakeredis-py) to mock
Redis in-process:

```bash
pytest tests/test_example_redis_session_store.py -v
```

### Live Redis end-to-end

A second test module exercises the adapter against a **real** Redis server.
It is skipped unless `SESSION_STORE_REDIS_URL` is set:

```bash
docker run -d -p 6379:6379 redis:7-alpine
SESSION_STORE_REDIS_URL=redis://localhost:6379/0 \
    pytest tests/test_example_redis_session_store_live.py -v
```

Each run writes under a random `test-{hex}` prefix and `SCAN`/`DEL`s it on
teardown.

This mirrors the `RedisSessionStore` reference implementation from the
TypeScript SDK.

---

## Postgres — `postgres_session_store.py`

Backed by [`asyncpg`](https://github.com/MagicStack/asyncpg), the native
asyncio Postgres driver.

### Installation

```bash
pip install claude-agent-sdk asyncpg
```

### Usage

```python
import asyncpg
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from postgres_session_store import PostgresSessionStore

pool = await asyncpg.create_pool("postgresql://...")
store = PostgresSessionStore(pool=pool)
await store.create_schema()  # idempotent CREATE TABLE IF NOT EXISTS

async for message in query(
    prompt="Hello!",
    options=ClaudeAgentOptions(session_store=store),
):
    if isinstance(message, ResultMessage) and message.subtype == "success":
        print(message.result)
```

### Schema

One row per transcript entry; `seq` (a `bigserial`) orders entries within a
`(project_key, session_id, subpath)` key:

```sql
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
```

`append()` is a single multi-row `INSERT ... SELECT unnest($entries::jsonb[])`;
`load()` is `SELECT entry ... ORDER BY seq`.

Note: this schema differs from the TypeScript SDK's Postgres reference adapter
(which defaults to table `claude_session_entries`, uses `NULL` rather than
`''` as the main-transcript subpath sentinel, and stores `created_at
TIMESTAMPTZ` rather than epoch-ms `mtime`). Sharing one Postgres table across
the two SDKs requires aligning on a single schema first.

### JSONB key ordering

Entries are stored as `jsonb`, which **reorders object keys** on read-back
(shorter keys first, then by byte order). This is explicitly allowed by the
`SessionStore` contract — `load()` requires *deep-equal*, not *byte-equal*,
returns. The SDK never byte-compares stored entries, and the `*_from_store`
read helpers hoist `"type"` to the first key when re-serializing so the SDK's
lite-parse tag scan still works. If you need byte-stable storage, switch the column
to `json` (preserves text as-is) or `text`.

### Retention

This adapter never deletes rows on its own. Add a scheduled
`DELETE FROM claude_session_store WHERE mtime < $cutoff` (or partition the
table by `mtime`) to expire transcripts according to your compliance
requirements.

`delete()` is implemented (cascades to subpath rows) but is only called when
you invoke `delete_session_via_store()` from the SDK.

Local-disk transcripts under `CLAUDE_CONFIG_DIR` are swept independently by the
CLI's `cleanupPeriodDays` setting.

### Resume from Postgres

```python
async for message in query(
    prompt="Continue where we left off",
    options=ClaudeAgentOptions(
        session_store=store,
        resume="previous-session-id",
    ),
):
    ...
```

### Live Postgres end-to-end

There is no in-process Postgres mock comparable to `moto`/`fakeredis`, so the
Postgres tests run **live-only** against a real server. They skip
automatically unless `SESSION_STORE_POSTGRES_URL` is set:

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16-alpine

SESSION_STORE_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres \
    pytest tests/test_example_postgres_session_store.py -v
```

Each run creates a random-suffixed table and `DROP`s it on teardown, so the
target database is left clean.
