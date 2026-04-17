# SessionStore reference adapters

> **Reference implementations for interface validation. Not packaged, not maintained as production code.**

These adapters live in `examples/` (not `src/`) so the SDK package stays
free of heavyweight optional dependencies. They are imported and exercised by
the test suite to prove the [`SessionStore`](../../src/claude_agent_sdk/types.py)
protocol generalizes beyond the in-memory default. Copy whichever adapter you
need into your project and adapt it.

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
    if message.type == "result" and message.subtype == "success":
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
only called when you invoke `delete_session()` from the SDK.

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

This mirrors the `RedisSessionStore` reference implementation from the
TypeScript SDK.

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

The fakeredis-backed unit tests skip automatically unless the optional
`[examples]` dependency group is installed:

```bash
pip install -e '.[dev,examples]'
pytest tests/test_example_redis_session_store.py
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

