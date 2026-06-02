"""Regression tests for SubprocessCLITransport.close() pipe-I/O deadlock.

Bug:
    When ``Query.close()`` cancels the stdout reader *before* calling
    ``transport.close()``, and ``transport.close()`` then closes only stdin
    but leaves the read-end of stdout open, the subprocess's final
    ``write()`` on a full pipe buffer blocks in the kernel. ``process.wait()``
    times out after 5 s, SIGTERM is sent — but a child that has
    SA_RESTART-style ``write()`` (e.g. Node.js) is not interruptible by
    SIGTERM mid-write — and only SIGKILL after another 5 s ends the deadlock.

Fix:
    ``transport.close()`` must ``aclose()`` ``_stdout_stream`` *before*
    waiting for the subprocess. Closing the read-end causes the kernel to
    deliver ``EPIPE`` (or ``SIGPIPE``) on the next ``write()`` from the
    child, after which the child exits cleanly and ``process.wait()``
    returns immediately.

Refs:
    - GitHub issue: #728

These tests stand up a real fake-CLI as a Python subprocess. No mocks of the
SDK transport itself; we only mock-out ``shutil.which`` via the ``cli_path``
parameter (which is the SDK's own escape hatch and is also used by the
existing ``test_transport.py``).
"""

from __future__ import annotations

import os
import signal
import stat
import sys
import textwrap
import time
from pathlib import Path

import anyio
import pytest

from claude_agent_sdk._internal.transport.subprocess_cli import (
    SubprocessCLITransport,
)
from claude_agent_sdk.types import ClaudeAgentOptions


@pytest.fixture(autouse=True)
def _skip_version_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass ``--version`` probe so the fake CLI is not asked to advertise.

    SDK's ``connect()`` calls ``_check_claude_version`` which spawns the CLI
    with ``--version`` and parses semver output. Our fake CLI cannot answer
    that probe; the env flag short-circuits the check, which is the SDK's
    own intended escape hatch (see subprocess_cli.py:346).
    """
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

# Wall-clock budget that close() must hit even under the pathological
# pipe-full + SA_RESTART scenario. Pre-fix, close() takes 5 s (SIGTERM) +
# up to 5 s (SIGKILL). Post-fix, close() should return in well under a
# second because EPIPE on the next write() lets the child exit cleanly.
CLOSE_DEADLINE_SECONDS = 2.0


# ----------------------------------------------------------------------
# Fake-CLI generation
# ----------------------------------------------------------------------


def _write_fake_cli(tmp_path: Path, body: str) -> Path:
    """Write an executable Python script that masquerades as ``claude``.

    The script body is appended after a shebang and a stdlib import block.
    The resulting file is chmod 0o755 (no world-writable bit; per policy).
    """
    cli = tmp_path / "fake_claude"
    cli.write_text(
        textwrap.dedent(
            """\
            #!{python}
            import os, signal, sys, time
            """
        ).format(python=sys.executable)
        + body
    )
    # rwx for owner, rx for group/others — explicit, no 0o777.
    cli.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return cli


FAKE_CLI_PIPE_FULL_IGNORES_SIGTERM = textwrap.dedent(
    """
    # Ignore SIGTERM so the SDK has to fall through to closing the pipe
    # read-end (the fix under test). If the SDK never closes the read-end,
    # the SDK has to wait 5 s + 5 s and SIGKILL us — that is the bug.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # Emit a tiny "ready" line so the SDK has something on stdout from the
    # very start (mirrors the real claude CLI printing its init JSON).
    sys.stdout.write('{"type":"system","subtype":"init"}\\n')
    sys.stdout.flush()

    # Block until stdin EOF. The SDK signals shutdown by aclose()'ing stdin
    # (transport.close() does this first), so we get unblocked exactly at
    # the moment the SDK enters its process.wait() phase.
    try:
        sys.stdin.read()
    except Exception:
        pass

    # On stdin EOF, dump a payload large enough to overfill the default
    # 64 KB kernel pipe buffer many times over. Nobody is reading stdout
    # at this point (Query.close() cancels its reader before transport.
    # close()), so without the fix this write() blocks in the kernel:
    #   * 5 s — process.wait() times out, SDK sends SIGTERM (we ignore it)
    #   * 5 s — process.wait() times out again, SDK sends SIGKILL
    # With the fix, the SDK aclose()'s the stdout read-end before
    # process.wait(); our write() takes EPIPE and we exit cleanly.
    payload = ('{"type":"text","text":"' + ('x' * 1023) + '"}\\n') * 256  # ~256 KB
    try:
        sys.stdout.write(payload)
        sys.stdout.flush()
    except BrokenPipeError:
        # Healthy shutdown via EPIPE — exactly what the fix enables.
        sys.exit(0)
    sys.exit(0)
    """
)


FAKE_CLI_QUICK_EXIT = textwrap.dedent(
    """
    # Quick happy path: print a tiny line and exit on stdin EOF.
    sys.stdout.write('{"type":"text","text":"hi"}\\n')
    sys.stdout.flush()
    try:
        sys.stdin.read()
    except Exception:
        pass
    sys.exit(0)
    """
)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


class TestSubprocessCLICloseDoesNotDeadlock:
    """Regression suite for the pipe-I/O deadlock in transport.close()."""

    def test_close_returns_promptly_when_subprocess_ignores_sigterm_and_pipe_is_full(
        self, tmp_path: Path
    ) -> None:
        """close() must NOT take 10 s when the child has buffered stdout.

        This is the exact pathological scenario from the incident:
        - stdout pipe buffer is full
        - nobody is reading the pipe (the SDK cancels its reader before close)
        - the child ignores SIGTERM (Node.js + SA_RESTART)

        Without the fix: close() takes ~10 s (5 s graceful + 5 s SIGTERM).
        With the fix: close() takes well under a second (EPIPE on next write).
        """
        cli = _write_fake_cli(tmp_path, FAKE_CLI_PIPE_FULL_IGNORES_SIGTERM)

        async def _run() -> float:
            transport = SubprocessCLITransport(
                prompt="hello",
                options=ClaudeAgentOptions(cli_path=str(cli)),
            )
            await transport.connect()

            # IMPORTANT: do NOT consume stdout. This reproduces Query.close()'s
            # behaviour of cancelling the stdout reader before transport.close().
            # The fake CLI is blocked on stdin.read(); the moment
            # transport.close() aclose()'s stdin, the child wakes up and
            # tries to dump 256 KB onto a stdout pipe that nobody is reading
            # — this is the pathological state from the incident.
            t0 = time.monotonic()
            await transport.close()
            return time.monotonic() - t0

        elapsed = anyio.run(_run)

        assert elapsed < CLOSE_DEADLINE_SECONDS, (
            f"transport.close() took {elapsed:.2f}s — pipe-I/O deadlock fix "
            f"is missing or regressed. Expected < {CLOSE_DEADLINE_SECONDS}s."
        )

    def test_close_happy_path_still_fast(self, tmp_path: Path) -> None:
        """Verify the fix does not regress the trivial-exit happy path."""
        cli = _write_fake_cli(tmp_path, FAKE_CLI_QUICK_EXIT)

        async def _run() -> float:
            transport = SubprocessCLITransport(
                prompt="hello",
                options=ClaudeAgentOptions(cli_path=str(cli)),
            )
            await transport.connect()
            # Drain a small amount so the child can exit on EOF naturally.
            await anyio.sleep(0.1)

            t0 = time.monotonic()
            await transport.close()
            return time.monotonic() - t0

        elapsed = anyio.run(_run)

        assert elapsed < CLOSE_DEADLINE_SECONDS, (
            f"transport.close() regressed on the happy path: {elapsed:.2f}s"
        )

    def test_close_is_idempotent_after_fix(self, tmp_path: Path) -> None:
        """Calling close() twice must not raise — same contract as before."""
        cli = _write_fake_cli(tmp_path, FAKE_CLI_QUICK_EXIT)

        async def _run() -> None:
            transport = SubprocessCLITransport(
                prompt="hello",
                options=ClaudeAgentOptions(cli_path=str(cli)),
            )
            await transport.connect()
            await anyio.sleep(0.1)
            await transport.close()
            # Second call must be a no-op.
            await transport.close()

        anyio.run(_run)
