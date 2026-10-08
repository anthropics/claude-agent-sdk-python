"""query() against a stand-in CLI that reports session state (#1190).

The stand-in speaks the CLI side of the contract: with
``CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS`` set it sends ``session_state_changed``
frames as they are, otherwise with ``CLAUDE_CODE_SDK_READS_SESSION_STATE`` set
it marks them ``sdk_host_only``, otherwise it sends none. After its first
result a background agent "finishes" and wakes a follow-up turn whose hook
needs stdin; the stand-in only finishes that turn once the hook's answer
arrives on stdin, so the second result proves stdin was still open.

Every test here runs under both asyncio and trio (``anyio_backend`` in
conftest.py).
"""

import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import anyio
import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    query,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform == "win32", reason="spawns a shebang script"),
]

FAKE_CLI = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import json, os, sys

    if "-v" in sys.argv or "--version" in sys.argv:
        print("2.1.999 (Claude Code)")
        sys.exit(0)

    def truthy(name):
        return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

    def emit(obj):
        print(json.dumps(obj), flush=True)

    def state(value):
        frame = {"type": "system", "subtype": "session_state_changed",
                 "state": value, "uuid": "u-" + value, "session_id": "s"}
        if truthy("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"):
            emit(frame)
        elif truthy("CLAUDE_CODE_SDK_READS_SESSION_STATE"):
            emit(dict(frame, sdk_host_only=True))

    def assistant(text):
        emit({"type": "assistant", "parent_tool_use_id": None, "session_id": "s",
              "message": {"role": "assistant", "model": "m",
                          "content": [{"type": "text", "text": text}]}})

    def result(text):
        emit({"type": "result", "subtype": "success", "duration_ms": 1,
              "duration_api_ms": 1, "is_error": False, "num_turns": 1,
              "session_id": "s", "result": text})

    scenario = os.environ.get("FAKE_CLI_SCENARIO", "wake")
    names = ("CLAUDE_CODE_SDK_READS_SESSION_STATE",
             "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS")
    emit({"type": "system", "subtype": "init", "session_id": "s", "model": "m",
          "cwd": ".", "tools": [], "mcp_servers": [],
          "permissionMode": "default", "apiKeySource": "none",
          "fake_env": {name: os.environ.get(name) for name in names}})

    hook_id = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "control_request":
            request = msg["request"]
            if request.get("subtype") == "initialize":
                for matchers in (request.get("hooks") or {}).values():
                    for matcher in matchers:
                        hook_id = hook_id or matcher["hookCallbackIds"][0]
            emit({"type": "control_response",
                  "response": {"subtype": "success",
                               "request_id": msg["request_id"],
                               "response": {}}})
        elif kind == "user":
            state("running")
            assistant("LAUNCHED")
            result("LAUNCHED")
            if scenario == "wake":
                # The background agent finished just before that result; its
                # completion wakes a turn whose hook needs an answer on stdin.
                assistant("writing")
                emit({"type": "control_request", "request_id": "hook-1",
                      "request": {"subtype": "hook_callback",
                                  "callback_id": hook_id,
                                  "input": {"hook_event_name": "PreToolUse",
                                            "tool_name": "Write",
                                            "tool_input": {}},
                                  "tool_use_id": "toolu_1"}})
            # "stuck": the background agent never finishes, so the state
            # stays "running" until stdin closes.
        elif (kind == "control_response"
              and msg["response"].get("request_id") == "hook-1"):
            assistant("FINISHED")
            result("FINISHED")
            state("idle")
    """
).lstrip()


def _write_fake_cli(tmp_path: Path) -> Path:
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI)
    script.chmod(0o755)
    return script


async def _run(tmp_path: Path, env: dict[str, str]) -> tuple[list[Any], list[str]]:
    asked: list[str] = []

    async def hook(input_data, tool_use_id, context):
        asked.append(input_data["tool_name"])
        return {}

    options = ClaudeAgentOptions(
        cli_path=str(_write_fake_cli(tmp_path)),
        hooks={"PreToolUse": [HookMatcher(hooks=[hook])]},
        env=env,
    )
    with anyio.fail_after(20):
        messages = [m async for m in query(prompt="go", options=options)]
    return messages, asked


def _results(messages: list[Any]) -> list[str | None]:
    return [m.result for m in messages if isinstance(m, ResultMessage)]


def _states(messages: list[Any]) -> list[str]:
    return [
        m.data["state"]
        for m in messages
        if isinstance(m, SystemMessage) and m.subtype == "session_state_changed"
    ]


def _fake_env(messages: list[Any]) -> dict[str, str | None]:
    init = next(
        m for m in messages if isinstance(m, SystemMessage) and m.subtype == "init"
    )
    return init.data["fake_env"]


async def test_follow_up_turn_is_served_and_sdk_frames_stay_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SDK_READS_SESSION_STATE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS", raising=False)

    messages, asked = await _run(tmp_path, env={})

    assert _fake_env(messages) == {
        "CLAUDE_CODE_SDK_READS_SESSION_STATE": "1",
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": None,
    }
    assert _results(messages) == ["LAUNCHED", "FINISHED"]
    assert asked == ["Write"]
    assert _states(messages) == []


async def test_caller_who_opted_in_sees_the_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SDK_READS_SESSION_STATE", raising=False)

    messages, asked = await _run(
        tmp_path, env={"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"}
    )

    assert _results(messages) == ["LAUNCHED", "FINISHED"]
    assert asked == ["Write"]
    assert _states(messages) == ["running", "idle"]


async def test_ceiling_closes_stdin_when_idle_never_comes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS", raising=False)
    started = time.monotonic()

    messages, _ = await _run(
        tmp_path,
        env={
            "FAKE_CLI_SCENARIO": "stuck",
            "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "300",
        },
    )

    assert _results(messages) == ["LAUNCHED"]
    assert time.monotonic() - started >= 0.3
