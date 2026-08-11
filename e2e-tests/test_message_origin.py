"""End-to-end test for ``origin`` on result messages.

A host that stamps ``origin: {"kind": "human"}`` on the user message it
streams in gets that origin echoed back on the turn's ResultMessage; an
unstamped prompt yields ``origin is None``. This is the round trip a
streaming-input app relies on to tell "the answer to my prompt" from results
of injected turns (background-task notifications etc.).
"""

from collections.abc import AsyncIterator
from typing import Any

import anyio
import pytest

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage


def _user_message(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        **extra,
    }


async def _one(msg: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield msg


@pytest.mark.e2e
@pytest.mark.anyio
async def test_result_origin_round_trips_host_stamp():
    results: list[ResultMessage] = []
    async with ClaudeSDKClient(ClaudeAgentOptions(max_turns=1)) as client:
        with anyio.fail_after(120):
            await client.query(
                _one(_user_message("Reply with exactly: one", origin={"kind": "human"}))
            )
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    results.append(message)

            await client.query(_one(_user_message("Reply with exactly: two")))
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    results.append(message)

    assert len(results) == 2, results
    stamped, unstamped = results
    # origin is echoed on error results too, so is_error is only diagnostic.
    # Honoring a host-stamped {"kind": "human"} requires CLI >= 2.1.210.
    assert stamped.origin == {"kind": "human"}, (
        f"got {stamped.origin!r} (is_error={stamped.is_error}); "
        "requires Claude Code >= 2.1.210"
    )
    assert unstamped.origin is None, unstamped
