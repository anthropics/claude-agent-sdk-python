"""Cancellation must never leave the CLI subprocess behind.

`close()` runs on the cancellation path (a cancelled `async with
ClaudeSDKClient()`, an expiring `move_on_after`, a failing task group). If the
cleanup awaits are cancellable, the terminate/kill escalation is skipped and the
child is left running -- an orphan that surfaces as `[claude] <defunct>` once
nothing is left to wait() on it.

Every test here runs under both asyncio and trio (``anyio_backend`` in
conftest.py): the leak reproduces on both.
"""

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._internal.transport.subprocess_cli import (
    _ACTIVE_CHILDREN,
    SubprocessCLITransport,
)

pytestmark = pytest.mark.anyio

# Only the tests that spawn the shebang-based fake CLI or read process state via
# `ps` are POSIX-only. The pure-mock bookkeeping test runs everywhere.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="spawns a shebang script / reads `ps` state"
)

# A stand-in `claude` CLI: answers `-v`, replies to control requests so
# connect() completes, and exits on stdin EOF.
FAKE_CLI = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import json, sys

    if "-v" in sys.argv or "--version" in sys.argv:
        print("2.0.0 (Claude Code)")
        sys.exit(0)

    print(json.dumps({"type": "system", "subtype": "init", "session_id": "s",
                      "model": "m", "cwd": ".", "tools": [], "mcp_servers": [],
                      "permissionMode": "default", "apiKeySource": "none"}), flush=True)

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
                                           "response": {}}}), flush=True)
    """
).lstrip()


@pytest.fixture
def restore_active_children() -> Iterator[None]:
    """Keep a test's leftovers out of the module-global _ACTIVE_CHILDREN, even
    when the test fails partway through."""
    before = set(_ACTIVE_CHILDREN)
    try:
        yield
    finally:
        for extra in _ACTIVE_CHILDREN - before:
            _ACTIVE_CHILDREN.discard(extra)


def _write_fake_cli(tmp_path: Path) -> Path:
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI)
    script.chmod(0o755)
    return script


def _process_state(pid: int) -> str:
    """'gone', 'zombie', or 'alive'."""
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    if not out:
        return "gone"
    return "zombie" if out.startswith("Z") else "alive"


@posix_only
async def test_close_under_cancellation_still_reaps_child(tmp_path: Path) -> None:
    """close() called with the surrounding scope already cancelled must still
    shut the child down; without shielding, the first await bails out."""
    transport = SubprocessCLITransport(
        prompt="hi",
        options=ClaudeAgentOptions(cli_path=str(_write_fake_cli(tmp_path))),
    )
    await transport.connect()
    process = transport._process
    assert process is not None
    pid = process.pid

    with anyio.CancelScope() as scope:
        scope.cancel()  # every await inside close() would raise
        await transport.close()

    assert process.returncode is not None
    assert _process_state(pid) == "gone"
    assert process not in _ACTIVE_CHILDREN


@posix_only
async def test_cancelled_client_context_leaves_no_child(tmp_path: Path) -> None:
    """A cancelled `async with ClaudeSDKClient()` must not leak the CLI child."""
    pid = -1
    # No wall-clock deadline: connect() has provably completed by the time we
    # cancel, so a slow/loaded runner cannot make this flake. The cancellation
    # is still delivered while __aexit__ runs, which is the path under test.
    with anyio.CancelScope() as scope:
        options = ClaudeAgentOptions(cli_path=str(_write_fake_cli(tmp_path)))
        async with ClaudeSDKClient(options=options) as client:
            transport = client._transport
            assert isinstance(transport, SubprocessCLITransport)
            assert transport._process is not None
            pid = transport._process.pid
            scope.cancel()
            await anyio.sleep(30)  # raises immediately; __aexit__ runs cancelled

    assert scope.cancelled_caught
    assert pid > 0
    assert _process_state(pid) == "gone"


async def test_still_running_child_stays_tracked_for_atexit_reaper(
    restore_active_children: None,
) -> None:
    """If close() cannot reap the child, it must stay in _ACTIVE_CHILDREN so the
    atexit reaper still gets a chance to signal it, rather than being silently
    forgotten."""
    with patch("anyio.open_process") as mock_exec:
        version_process = MagicMock()
        version_process.stdout = MagicMock()
        version_process.stdout.receive = AsyncMock(return_value=b"2.0.0 (Claude Code)")
        version_process.terminate = MagicMock()
        version_process.wait = AsyncMock()

        process = MagicMock()
        process.returncode = None  # never exits, even after SIGKILL
        process.terminate = MagicMock()
        process.kill = MagicMock()
        process.stdout = MagicMock()
        process.stderr = MagicMock()
        process.stdin = MagicMock(aclose=AsyncMock())
        process.wait = AsyncMock()

        mock_exec.side_effect = [version_process, process]

        transport = SubprocessCLITransport(
            prompt="hi", options=ClaudeAgentOptions(cli_path="/usr/bin/claude")
        )
        await transport.connect()
        assert process in _ACTIVE_CHILDREN

        with patch("anyio.fail_after", side_effect=TimeoutError):
            await transport.close()

        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        assert process in _ACTIVE_CHILDREN


@posix_only
async def test_reaped_child_is_untracked(tmp_path: Path) -> None:
    """The normal path still drops the child from _ACTIVE_CHILDREN."""
    transport = SubprocessCLITransport(
        prompt="hi",
        options=ClaudeAgentOptions(cli_path=str(_write_fake_cli(tmp_path))),
    )
    await transport.connect()
    process = transport._process
    assert process is not None
    await transport.close()
    assert process.returncode is not None
    assert process not in _ACTIVE_CHILDREN


@posix_only
def test_fake_cli_speaks_the_protocol(tmp_path: Path) -> None:
    """Guard the fixture itself: a broken fake CLI would make the tests above
    pass for the wrong reason."""
    script = _write_fake_cli(tmp_path)
    out = subprocess.run(
        [str(script), "-v"], capture_output=True, text=True, check=True
    ).stdout
    assert out.startswith("2.0.0")

    proc = subprocess.run(
        [str(script)],
        input=json.dumps({"type": "control_request", "request_id": "1"}) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ},
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert lines[0]["subtype"] == "init"
    assert lines[1]["response"]["request_id"] == "1"
