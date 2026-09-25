"""A ClaudeSDKClient is bound to an event loop, not to a task or a nursery.

The client's docstring used to claim the opposite -- that an instance could not
cross "different trio nurseries or asyncio task groups" because it held a
persistent task group open from connect() to disconnect(). That task group is
gone (the read loop is a detached task now, see _task_compat.py), so every
cross-task shape users asked about works. The tests here pin that, with a real
SubprocessCLITransport talking to a stand-in CLI rather than a mock, because a
mock transport cannot show whether the real pipes survive the move.

What does not work is crossing event loops: the read task and the subprocess
pipes die with the loop they were spawned on. Without a guard that shape is
silent -- query() writes, receive_response() ends with zero messages and no
exception -- so the last test pins the error instead.

Every test runs under both asyncio and trio (``anyio_backend`` in conftest.py).
"""

import sys
import textwrap
from contextlib import suppress
from pathlib import Path
from typing import Any

import anyio
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk._internal.transport.subprocess_cli import (
    _ACTIVE_CHILDREN,
    SubprocessCLITransport,
)

pytestmark = pytest.mark.anyio

# The stand-in CLI is a shebang script, and the transport execs it directly.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="spawns a shebang script"
)

# A stand-in `claude` CLI: answers `-v`, answers the initialize control request,
# and echoes every user message back as an assistant turn plus a result. It
# stays alive until stdin EOF, so one process serves several turns.
FAKE_CLI = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import json, sys

    if "-v" in sys.argv or "--version" in sys.argv:
        print("2.0.0 (Claude Code)")
        sys.exit(0)

    turns = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "control_request":
            print(json.dumps({"type": "control_response",
                              "response": {"subtype": "success",
                                           "request_id": msg["request_id"],
                                           "response": {"commands": [],
                                                        "output_style": "default"}}}),
                  flush=True)
            continue
        if msg.get("type") == "user":
            turns += 1
            text = msg["message"]["content"]
            print(json.dumps({"type": "assistant",
                              "message": {"role": "assistant",
                                          "content": [{"type": "text",
                                                       "text": f"echo:{text}"}],
                                          "model": "fake-model"}}), flush=True)
            print(json.dumps({"type": "result", "subtype": "success",
                              "duration_ms": 1, "duration_api_ms": 1,
                              "is_error": False, "num_turns": turns,
                              "session_id": "s", "total_cost_usd": 0.0}), flush=True)
    """
).lstrip()


@pytest.fixture
def fake_cli_options(tmp_path: Path) -> ClaudeAgentOptions:
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI)
    script.chmod(0o755)
    return ClaudeAgentOptions(cli_path=str(script))


async def _one_turn(client: ClaudeSDKClient, prompt: str) -> list[str]:
    """Send ``prompt`` and collect the reply's text, one turn."""
    await client.query(prompt)
    texts: list[str] = []
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
        elif isinstance(message, ResultMessage):
            break
    return texts


@posix_only
async def test_turn_in_a_later_task_group_than_connect(
    fake_cli_options: ClaudeAgentOptions,
) -> None:
    """connect() in one task group, the turn in a separate, later one.

    The shape reported from FastAPI-style code: a dependency connects, its
    scope exits, and the request handler runs in a group of its own.
    """
    client = ClaudeSDKClient(options=fake_cli_options)
    async with anyio.create_task_group() as tg:
        tg.start_soon(client.connect)

    texts: list[str] = []
    async with anyio.create_task_group() as tg:

        async def turn() -> None:
            texts.extend(await _one_turn(client, "hello"))

        tg.start_soon(turn)

    assert texts == ["echo:hello"]
    await client.disconnect()


@posix_only
async def test_two_turns_from_two_different_tasks(
    fake_cli_options: ClaudeAgentOptions,
) -> None:
    """The "second request hangs forever" report: two turns, two tasks, one
    client, neither of them the task that connected."""
    async with ClaudeSDKClient(options=fake_cli_options) as client:
        collected: list[list[str]] = []

        async def turn(prompt: str) -> None:
            collected.append(await _one_turn(client, prompt))

        async with anyio.create_task_group() as tg:
            tg.start_soon(turn, "first")
        async with anyio.create_task_group() as tg:
            tg.start_soon(turn, "second")

        assert collected == [["echo:first"], ["echo:second"]]


@posix_only
async def test_disconnect_from_a_different_task_than_connect(
    fake_cli_options: ClaudeAgentOptions,
) -> None:
    """Teardown from a foreign task used to hit anyio's cancel-scope task
    affinity; it must just close."""
    client = ClaudeSDKClient(options=fake_cli_options)
    await client.connect()
    assert await _one_turn(client, "hi") == ["echo:hi"]

    async with anyio.create_task_group() as tg:
        tg.start_soon(client.disconnect)

    assert client._query is None


@posix_only
async def test_query_from_the_parent_while_a_child_task_reads(
    fake_cli_options: ClaudeAgentOptions,
) -> None:
    """Reader and writer in different tasks at the same time, over real pipes.

    test_streaming_client.py::test_concurrent_send_receive covers this against
    a mock; a mock cannot tell whether the subprocess pipes tolerate it.
    """
    async with ClaudeSDKClient(options=fake_cli_options) as client:
        received: list[str] = []

        async def reader() -> None:
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    received.extend(
                        b.text for b in message.content if isinstance(b, TextBlock)
                    )

        async with anyio.create_task_group() as tg:
            tg.start_soon(reader)
            await client.query("concurrent")

        assert received == ["echo:concurrent"]


@posix_only
# Stranding a subprocess on a closed asyncio loop makes its pipe transport
# raise "Event loop is closed" from __del__ -- part of the mess this guard
# exists to warn about, and nothing the test can clean up from here.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_reuse_on_a_second_event_loop_raises(backend: str, tmp_path: Path) -> None:
    """Reusing a client on a second event loop must say so, not go quiet.

    get_server_info() is the documented exception and is checked here too, so
    the class docstring's carve-out fails loudly if that ever changes.

    Sync, unlike the rest of the module: it needs two loops of its own, so it
    runs the backend matrix itself instead of taking ``anyio_backend``.
    """
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI)
    script.chmod(0o755)
    client = ClaudeSDKClient(options=ClaudeAgentOptions(cli_path=str(script)))

    async def first() -> list[str]:
        await client.connect()
        return await _one_turn(client, "first")

    async def second() -> None:
        await _one_turn(client, "second")

    async def control() -> None:
        await client.set_model("other")

    async def server_info() -> dict[str, Any] | None:
        return await client.get_server_info()

    assert anyio.run(first, backend=backend) == ["echo:first"]
    transport = client._transport
    assert isinstance(transport, SubprocessCLITransport)
    process = transport._process
    assert process is not None

    try:
        # Before the guard: the write went through and this returned [].
        with pytest.raises(CLIConnectionError, match="different event loop"):
            anyio.run(second, backend=backend)
        # Before the guard: this sat waiting out the control-request timeout.
        with pytest.raises(CLIConnectionError, match="different event loop"):
            anyio.run(control, backend=backend)
        # Unguarded on purpose: it answers from the initialization result
        # cached at connect() and never touches the loop, so there is nothing
        # to fail silently.
        assert anyio.run(server_info, backend=backend) == {
            "commands": [],
            "output_style": "default",
        }
    finally:
        # disconnect() is deliberately unguarded, but it cannot help here:
        # the child belongs to a loop that is gone, so nothing can await it
        # and close() would pay its three 5s waits for a wait() that never
        # returns. Signal it directly instead.
        with suppress(ProcessLookupError):
            process.kill()
        _ACTIVE_CHILDREN.discard(process)
