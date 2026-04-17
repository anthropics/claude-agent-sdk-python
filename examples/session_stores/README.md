# SessionStore example adapters

Reference implementations of the
[`SessionStore`](../../src/claude_agent_sdk/types.py) protocol for common
external backends. These live under `examples/` rather than the main package
so the SDK does not take on `boto3`, `redis`, etc. as hard dependencies.

**These are not installable.** Copy the adapter you need into your own
project, install the backend client yourself (`boto3`, `redis`, …), and run
the shared conformance suite against your copy:

```python
import pytest
from claude_agent_sdk.testing import run_session_store_conformance
from my_project.stores import S3SessionStore  # your copy

@pytest.mark.asyncio
async def test_s3_store_conformance(s3_client):
    await run_session_store_conformance(
        lambda: S3SessionStore(bucket="test", client=s3_client)
    )
```

Each adapter here passes the full 13-contract conformance suite — see
`tests/test_example_s3_session_store.py` for a worked example using `moto`.

## S3 — `S3SessionStore`

Stores transcripts as JSONL part files:

```
s3://{bucket}/{prefix}{project_key}/{session_id}/part-{epochMs13}-{rand6}.jsonl
```

Each `append()` writes a new part; `load()` lists, sorts, and concatenates
them.

### Install

`boto3` is **not** a dependency of `claude-agent-sdk` — install it yourself:

```bash
pip install claude-agent-sdk boto3
```

### Usage

```python
import anyio
import boto3

from claude_agent_sdk import ClaudeAgentOptions, query
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
        if message.type == "result" and message.subtype == "success":
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
called when you invoke `delete_session()` from the SDK.

Local-disk transcripts under `CLAUDE_CONFIG_DIR` are swept independently by
the CLI's `cleanupPeriodDays` setting.

### Running the tests

Unit tests use [`moto`](https://github.com/getmoto/moto) to mock S3 in-process:

```bash
pip install 'claude-agent-sdk[examples]'   # boto3 + moto[s3]
pytest tests/test_example_s3_session_store.py -v
```

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

## Redis — `RedisSessionStore`

_Coming soon._
