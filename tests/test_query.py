"""Tests for query() stdin lifecycle with SDK MCP servers, hooks and can_use_tool.

The SDK communicates with the CLI subprocess over stdin/stdout. When SDK MCP
servers, hooks, or a can_use_tool callback are configured, the CLI sends
control_request messages back to the SDK *after* the prompt is written. The
SDK must keep stdin open long enough to respond to these requests. These tests
verify that both the string prompt and AsyncIterable prompt paths defer
closing stdin until the CLI's run is over.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    ResultMessage,
    SystemMessage,
    create_sdk_mcp_server,
    query,
    tool,
)
from claude_agent_sdk._errors import CLIConnectionError, ProcessError, ResultError
from claude_agent_sdk._internal.query import Query, run_end_ceiling_ms
from claude_agent_sdk.types import HookMatcher


def _capture_initialize_request(**query_kwargs):
    """Run Query.initialize() with a stubbed control channel and return the request dict."""
    captured: dict = {}

    async def _run():
        transport = AsyncMock()
        transport.is_ready = Mock(return_value=True)
        q = Query(transport=transport, is_streaming_mode=True, **query_kwargs)

        async def fake_send(request, timeout):
            captured.update(request)
            return {"commands": []}

        with patch.object(q, "_send_control_request", side_effect=fake_send):
            await q.initialize()

    anyio.run(_run)
    return captured


def test_initialize_sends_exclude_dynamic_sections():
    """Query.initialize() includes excludeDynamicSections in the control request."""
    sent = _capture_initialize_request(exclude_dynamic_sections=True)
    assert sent["subtype"] == "initialize"
    assert sent["excludeDynamicSections"] is True


def test_initialize_omits_exclude_dynamic_sections_when_unset():
    """excludeDynamicSections is absent from initialize when not configured."""
    sent = _capture_initialize_request()
    assert sent["subtype"] == "initialize"
    assert "excludeDynamicSections" not in sent


def test_initialize_sends_system_prompt_snapshot():
    """Query.initialize() includes systemPromptSnapshot, even when it is False."""
    sent = _capture_initialize_request(system_prompt_snapshot=False)
    assert sent["subtype"] == "initialize"
    assert sent["systemPromptSnapshot"] is False


def test_initialize_omits_system_prompt_snapshot_when_unset():
    """systemPromptSnapshot is absent from initialize when not configured."""
    sent = _capture_initialize_request()
    assert sent["subtype"] == "initialize"
    assert "systemPromptSnapshot" not in sent


def test_initialize_sends_skills_list():
    """Query.initialize() includes skills only when it is a list."""
    sent = _capture_initialize_request(skills=["pdf", "docx"])
    assert sent["skills"] == ["pdf", "docx"]

    sent_empty = _capture_initialize_request(skills=[])
    assert sent_empty["skills"] == []


def test_initialize_omits_skills_for_none_and_all():
    """'all' and None both omit skills from initialize (no filter at wire level)."""
    assert "skills" not in _capture_initialize_request()
    assert "skills" not in _capture_initialize_request(skills=None)
    assert "skills" not in _capture_initialize_request(skills="all")


def test_initialize_sends_forward_subagent_text_when_enabled():
    """forwardSubagentText is sent as an initialize capability, not a CLI flag."""
    sent = _capture_initialize_request(forward_subagent_text=True)
    assert sent["forwardSubagentText"] is True


def test_initialize_omits_forward_subagent_text_by_default():
    assert "forwardSubagentText" not in _capture_initialize_request()
    assert "forwardSubagentText" not in _capture_initialize_request(
        forward_subagent_text=False
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_forward_subagent_text_option_reaches_initialize(enabled):
    """ClaudeAgentOptions.forward_subagent_text is plumbed through query()."""

    async def _test():
        mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)
        captured: dict = {}

        async def fake_send(self, request, timeout=60.0):
            if request.get("subtype") == "initialize":
                captured.update(request)
            return {}

        with (
            patch(
                "claude_agent_sdk._internal.client.SubprocessCLITransport"
            ) as mock_cls,
            patch.object(Query, "_send_control_request", fake_send),
        ):
            mock_cls.return_value = mock_transport
            async for _ in query(
                prompt="Hello",
                options=ClaudeAgentOptions(forward_subagent_text=enabled),
            ):
                pass

        assert captured["subtype"] == "initialize"
        assert captured.get("forwardSubagentText") == (True if enabled else None)

    anyio.run(_test)


def _make_mock_transport(messages, control_requests=None):
    """Create a mock transport that yields messages and optionally sends control requests.

    Args:
        messages: List of message dicts to yield from read_messages.
        control_requests: Optional list of control request dicts. If provided,
            they are injected before the regular messages to simulate MCP init.
    """
    mock_transport = AsyncMock()

    all_messages = list(control_requests or []) + list(messages)

    async def mock_receive():
        for msg in all_messages:
            yield msg

    mock_transport.read_messages = mock_receive
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)
    return mock_transport


_ASSISTANT_AND_RESULT = [
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-sonnet-4-20250514",
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "duration_ms": 100,
        "duration_api_ms": 80,
        "is_error": False,
        "num_turns": 1,
        "session_id": "test",
        "total_cost_usd": 0.001,
    },
]


_MCP_CONTROL_REQUESTS = [
    {
        "type": "control_request",
        "request_id": "mcp_init_1",
        "request": {
            "subtype": "mcp_message",
            "server_name": "greeter",
            "message": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-cli", "version": "0"},
                },
            },
        },
    },
    {
        "type": "control_request",
        "request_id": "mcp_init_2",
        "request": {
            "subtype": "mcp_message",
            "server_name": "greeter",
            "message": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        },
    },
]


def _mcp_handshake_transport(writes: list[str]) -> AsyncMock:
    """Mock transport that plays the CLI side of an SDK MCP handshake.

    Like the real CLI, it sends each MCP control request and waits for the
    SDK's control response before moving on, then emits the assistant and
    result messages.
    """
    mock_transport = AsyncMock()
    responded = {req["request_id"]: anyio.Event() for req in _MCP_CONTROL_REQUESTS}

    async def tracking_write(data):
        writes.append(data)
        frame = json.loads(data)
        if frame.get("type") == "control_response":
            responded[frame["response"]["request_id"]].set()

    async def mock_receive():
        for req in _MCP_CONTROL_REQUESTS:
            yield req
            await responded[req["request_id"]].wait()
        for msg in _ASSISTANT_AND_RESULT:
            yield msg

    mock_transport.write = tracking_write
    mock_transport.read_messages = mock_receive
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)
    return mock_transport


def _assert_mcp_handshake_succeeded(writes: list[str]) -> None:
    control_responses = [
        json.loads(w) for w in writes if json.loads(w).get("type") == "control_response"
    ]
    assert len(control_responses) == 2
    init, listing = (r["response"] for r in control_responses)
    assert init["subtype"] == "success"
    assert init["response"]["mcp_response"]["result"]["serverInfo"]["name"] == "greeter"
    assert listing["subtype"] == "success"
    tools = listing["response"]["mcp_response"]["result"]["tools"]
    assert [t["name"] for t in tools] == ["greet"]


def _make_greet_server():
    @tool("greet", "Greet a user", {"name": str})
    async def greet_tool(args):
        return {"content": [{"type": "text", "text": f"Hi {args['name']}"}]}

    return create_sdk_mcp_server("greeter", tools=[greet_tool])


class TestStringPromptWithSdkMcpServers:
    """Test that string prompts keep stdin open for SDK MCP servers."""

    def test_string_prompt_waits_for_result_with_sdk_mcp_servers(self):
        """end_input() should not be called until after the first result
        when SDK MCP servers are present."""

        async def _test():
            server = _make_greet_server()
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            call_order = []
            original_write = mock_transport.write

            async def tracking_write(data):
                call_order.append(("write", data))
                return await original_write(data)

            async def tracking_end_input():
                call_order.append(("end_input",))

            mock_transport.write = tracking_write
            mock_transport.end_input = tracking_end_input

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt="Hello",
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)
            assert any(c[0] == "end_input" for c in call_order)

            write_calls = [c for c in call_order if c[0] == "write"]
            assert len(write_calls) >= 1
            written_data = json.loads(write_calls[0][1])
            assert written_data["type"] == "user"
            assert written_data["message"]["content"] == "Hello"

        anyio.run(_test)

    def test_string_prompt_without_mcp_servers_closes_immediately(self):
        """end_input() should be called immediately when no SDK MCP servers
        are present (preserving existing behavior)."""

        async def _test():
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(prompt="Hello"):
                    messages.append(msg)

            assert len(messages) == 2
            mock_transport.end_input.assert_called_once()

        anyio.run(_test)

    def test_string_prompt_mcp_server_control_requests_succeed(self):
        """MCP control requests arriving after the user message should be
        handled successfully because stdin is still open."""

        async def _test():
            server = _make_greet_server()
            writes: list[str] = []
            mock_transport = _mcp_handshake_transport(writes)

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt="Greet Alice",
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)

            # user message + 2 MCP control responses = at least 3 writes
            assert len(writes) >= 3
            _assert_mcp_handshake_succeeded(writes)

        anyio.run(_test)

    def test_string_prompt_with_hooks_waits_for_result(self):
        """end_input() should wait for first result when hooks are configured,
        even without SDK MCP servers."""

        async def _test():
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            call_order = []

            async def tracking_write(data):
                call_order.append(("write", data))

            async def tracking_end_input():
                call_order.append(("end_input",))

            mock_transport.write = tracking_write
            mock_transport.end_input = tracking_end_input

            async def dummy_hook(input_data, tool_use_id, context):
                return {"continue_": True}

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt="Do something",
                    options=ClaudeAgentOptions(
                        hooks={
                            "PreToolUse": [
                                HookMatcher(hooks=[dummy_hook]),
                            ],
                        },
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert any(c[0] == "end_input" for c in call_order)

        anyio.run(_test)


_TASK_STARTED = {
    "type": "system",
    "subtype": "task_started",
    "task_id": "task-1",
    "task_type": "local_agent",
    "description": "background subagent",
    "uuid": "uuid-ts1",
    "session_id": "test",
}

# A backgrounded shell (dev server, `tail -f`) is reported through the same
# frames but can run indefinitely, so it must never defer the stdin close.
_SHELL_TASK_STARTED = {
    "type": "system",
    "subtype": "task_started",
    "task_id": "shell-1",
    "task_type": "local_bash",
    "description": "npm run dev",
    "uuid": "uuid-ts2",
    "session_id": "test",
}

_TASK_NOTIFICATION = {
    "type": "system",
    "subtype": "task_notification",
    "task_id": "task-1",
    "status": "completed",
    "output_file": "/tmp/task-1.output",
    "summary": "done",
    "uuid": "uuid-tn1",
    "session_id": "test",
}

_TASK_UPDATED_TERMINAL = {
    "type": "system",
    "subtype": "task_updated",
    "task_id": "task-1",
    "patch": {"status": "completed"},
}

_BACKGROUND_TASKS_CHANGED = {
    "type": "system",
    "subtype": "background_tasks_changed",
    "tasks": [
        {"task_id": "task-1", "task_type": "local_agent"},
        {"task_id": "task-2", "task_type": "local_agent"},
    ],
}


def _make_result(uid):
    return dict(_ASSISTANT_AND_RESULT[1], uuid=uid)


class TestStdinStaysOpenWithInflightTasks:
    """A result frame with tasks in flight must not close stdin (#1088).

    Background tasks keep running past the turn's result frame and still
    need stdin for hook/SDK-MCP control responses. Closing it there fails
    those responses with "Stream closed" and silently disables hooks.
    """

    @pytest.mark.parametrize(
        "drain_frame",
        [_TASK_NOTIFICATION, _TASK_UPDATED_TERMINAL],
        ids=["task_notification", "task_updated_terminal_patch"],
    )
    def test_result_with_inflight_task_keeps_stdin_open(self, drain_frame):
        """stdin stays open across an intermediate result, then closes on the
        first result that arrives with no tasks in flight."""

        async def _test():
            server = _make_greet_server()
            end_input_calls = []

            mock_transport = AsyncMock()
            mock_transport.connect = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.write = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            async def tracking_end_input():
                end_input_calls.append(True)

            mock_transport.end_input = tracking_end_input

            open_after_intermediate_result = None
            closed_after_final_result = None

            async def mock_receive():
                nonlocal open_after_intermediate_result, closed_after_final_result
                yield dict(_ASSISTANT_AND_RESULT[0])
                yield dict(_TASK_STARTED)
                yield _make_result("uuid-r1")
                # Let the stdin-closing waiter run if the first-result event
                # (incorrectly) fired for the intermediate result.
                for _ in range(20):
                    await anyio.sleep(0)
                open_after_intermediate_result = not end_input_calls
                yield dict(drain_frame)
                yield _make_result("uuid-r2")
                # Now the waiter should wake and close stdin before the
                # stream ends (i.e. not merely via the reader's finally).
                for _ in range(20):
                    await anyio.sleep(0)
                closed_after_final_result = bool(end_input_calls)

            mock_transport.read_messages = mock_receive

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt="Hello",
                    options=ClaudeAgentOptions(mcp_servers={"greeter": server}),
                ):
                    messages.append(msg)

            assert open_after_intermediate_result is True
            assert closed_after_final_result is True
            results = [m for m in messages if isinstance(m, ResultMessage)]
            assert len(results) == 2

        anyio.run(_test)

    def test_track_task_lifecycle_unit(self):
        """_track_task_lifecycle adds on start and clears only on terminal."""
        transport = AsyncMock()
        transport.is_ready = Mock(return_value=True)
        q = Query(transport=transport, is_streaming_mode=False)

        q._track_task_lifecycle(dict(_TASK_STARTED))
        assert q._inflight_tasks == {"task-1"}

        # Non-terminal patch does not clear the task.
        q._track_task_lifecycle(
            {
                "subtype": "task_updated",
                "task_id": "task-1",
                "patch": {"status": "running"},
            }
        )
        assert q._inflight_tasks == {"task-1"}

        # Patch without a dict payload is ignored.
        q._track_task_lifecycle(
            {"subtype": "task_updated", "task_id": "task-1", "patch": None}
        )
        assert q._inflight_tasks == {"task-1"}

        # Terminal patch clears it.
        q._track_task_lifecycle(dict(_TASK_UPDATED_TERMINAL))
        assert q._inflight_tasks == set()

        # Draining an unknown/already-cleared task is a no-op, not an error.
        q._track_task_lifecycle(dict(_TASK_NOTIFICATION))
        assert q._inflight_tasks == set()

        # Frames without a task_id are ignored.
        q._track_task_lifecycle({"subtype": "task_started"})
        assert q._inflight_tasks == set()

    def test_shell_and_monitor_tasks_never_defer_the_close(self):
        """Only delegated agent work defers the stdin close.

        A backgrounded shell can run forever, and the CLI only exits on stdin
        EOF, so tracking one would withhold the close permanently rather than
        briefly — the reader's ``finally`` never runs either (#1088).
        """
        transport = AsyncMock()
        transport.is_ready = Mock(return_value=True)
        q = Query(transport=transport, is_streaming_mode=False)

        q._track_task_lifecycle(dict(_SHELL_TASK_STARTED))
        assert q._inflight_tasks == set()

        for task_type in ("monitor_mcp", "monitor_ws", "in_process_teammate"):
            q._track_task_lifecycle(
                {
                    "subtype": "task_started",
                    "task_id": f"{task_type}-1",
                    "task_type": task_type,
                }
            )
        assert q._inflight_tasks == set()

        # A start frame with no task_type at all is not assumed to be an agent.
        q._track_task_lifecycle({"subtype": "task_started", "task_id": "unknown-1"})
        assert q._inflight_tasks == set()

        # Agent work still defers, alongside the ignored shell.
        q._track_task_lifecycle(dict(_TASK_STARTED))
        assert q._inflight_tasks == {"task-1"}

    def test_background_snapshot_does_not_touch_the_ledger(self):
        """background_tasks_changed is ignored in both directions (#1088).

        It reports the live *background* set, but a subagent is registered in
        the foreground and only flips to backgrounded later without a second
        ``task_started`` — so a tracked agent that is still running can be
        absent from the snapshot entirely.
        """
        transport = AsyncMock()
        transport.is_ready = Mock(return_value=True)
        q = Query(transport=transport, is_streaming_mode=False)

        # It cannot add: the snapshot spans every background task type and
        # cannot distinguish an observer agent, whose bookends are suppressed.
        q._track_task_lifecycle(dict(_BACKGROUND_TASKS_CHANGED))
        assert q._inflight_tasks == set()

        # Nor can it remove. A foreground subagent is tracked from its
        # task_started but appears in no snapshot; narrowing against one would
        # drop it, and when it is later auto-backgrounded no second
        # task_started re-adds it — so the result frame would close stdin while
        # it is still running, which is exactly the bug being fixed.
        q._track_task_lifecycle(dict(_TASK_STARTED))
        q._track_task_lifecycle(
            {
                "type": "system",
                "subtype": "background_tasks_changed",
                "tasks": [{"task_id": "shell-1", "task_type": "local_bash"}],
            }
        )
        assert q._inflight_tasks == {"task-1"}

        # Not even an empty snapshot clears it; only a terminal frame does.
        q._track_task_lifecycle(
            {"type": "system", "subtype": "background_tasks_changed", "tasks": []}
        )
        assert q._inflight_tasks == {"task-1"}

        q._track_task_lifecycle(dict(_TASK_NOTIFICATION))
        assert q._inflight_tasks == set()

    def test_never_ending_shell_does_not_wedge_stdin_open(self):
        """A backgrounded shell that never finishes must not hang the query.

        Unlike the other streams in this file, this transport does not end on
        its own: it models the real contract the stdin close depends on — the
        CLI's stdout does not reach EOF until its stdin does. A tracked task
        that never reaches a terminal status therefore cannot be rescued by
        the reader's ``finally``, because that only runs once the process
        exits (#1088).
        """

        async def _test():
            server = _make_greet_server()
            stdin_closed = anyio.Event()

            mock_transport = AsyncMock()
            mock_transport.connect = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.write = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            async def end_input():
                stdin_closed.set()

            mock_transport.end_input = end_input

            async def mock_receive():
                yield dict(_ASSISTANT_AND_RESULT[0])
                yield dict(_SHELL_TASK_STARTED)
                yield {
                    "type": "system",
                    "subtype": "background_tasks_changed",
                    "tasks": [{"task_id": "shell-1", "task_type": "local_bash"}],
                }
                yield _make_result("uuid-r1")
                # The shell never exits, so no terminal frame ever arrives and
                # the snapshot keeps listing it. Park until stdin closes.
                await stdin_closed.wait()

            mock_transport.read_messages = mock_receive

            completed = False
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                with anyio.move_on_after(10):
                    async for _ in query(
                        prompt="start the dev server",
                        options=ClaudeAgentOptions(mcp_servers={"greeter": server}),
                    ):
                        pass
                    completed = True

            assert stdin_closed.is_set(), "stdin was never closed"
            assert completed, "query() did not terminate"

        anyio.run(_test)


def _session_state(state, *, sdk_host_only=True):
    """A session_state_changed frame. By default it is marked the way a CLI
    marks the frames it sends only because the SDK asked for them."""
    frame = {
        "type": "system",
        "subtype": "session_state_changed",
        "state": state,
        "uuid": f"uuid-state-{state}",
        "session_id": "test",
    }
    if sdk_host_only:
        frame["sdk_host_only"] = True
    return frame


def _assistant(parent_tool_use_id=None):
    return dict(_ASSISTANT_AND_RESULT[0], parent_tool_use_id=parent_tool_use_id)


def _user_prompt(text="Hello"):
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        "session_id": "",
    }


async def _let_waiter_run():
    for _ in range(20):
        await anyio.sleep(0)


async def _until(condition, timeout=5.0):
    with anyio.fail_after(timeout):
        while not condition():
            await anyio.sleep(0.005)


def _run_query_over(
    frames_fn,
    *,
    prompt: Any = "Hello",
    env: dict[str, str] | None = None,
    backend: str = "asyncio",
):
    """Run query() with a hook over a scripted CLI.

    ``frames_fn(end_input_calls, writes)`` is an async generator producing the
    CLI's stdout; between frames it can inspect ``end_input_calls`` and the
    user messages written to stdin so far. Returns the messages query()
    yielded.
    """

    async def _test():
        end_input_calls: list[bool] = []
        writes: list[dict[str, Any]] = []

        mock_transport = AsyncMock()
        mock_transport.connect = AsyncMock()
        mock_transport.close = AsyncMock()
        mock_transport.is_ready = Mock(return_value=True)

        async def tracking_write(data):
            writes.append(json.loads(data))

        async def tracking_end_input():
            end_input_calls.append(True)

        mock_transport.write = tracking_write
        mock_transport.end_input = tracking_end_input
        mock_transport.read_messages = lambda: frames_fn(end_input_calls, writes)

        async def hook(input_data, tool_use_id, context):
            return {}

        with (
            patch("claude_agent_sdk._internal.client.SubprocessCLITransport") as cls,
            patch(
                "claude_agent_sdk._internal.query.Query.initialize",
                new_callable=AsyncMock,
            ),
        ):
            cls.return_value = mock_transport
            return [
                msg
                async for msg in query(
                    prompt=prompt,
                    options=ClaudeAgentOptions(
                        hooks={"PreToolUse": [HookMatcher(hooks=[hook])]},
                        env=env or {},
                    ),
                )
            ]

    return anyio.run(_test, backend=backend)


def _state_frames(messages):
    return [
        m.data["state"]
        for m in messages
        if isinstance(m, SystemMessage) and m.subtype == "session_state_changed"
    ]


class TestStdinStaysOpenUntilIdle:
    """With session state reported, the run ends at "idle", not a result (#1190).

    A background agent that finishes just before the turn's result leaves
    nothing in flight at that result, yet its completion still wakes the
    parent for a follow-up turn whose hook, permission and SDK MCP requests
    need stdin. The CLI reports "running" until no such turn is owed.
    """

    def test_task_settled_before_result_keeps_stdin_open_until_idle(self):
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield dict(_TASK_STARTED)
            yield dict(_TASK_NOTIFICATION)
            yield _make_result("uuid-r1")
            await _let_waiter_run()
            checks["open_after_first_result"] = not end_input_calls
            yield _make_result("uuid-r2")
            await _let_waiter_run()
            checks["open_after_second_result"] = not end_input_calls
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_at_idle"] = bool(end_input_calls)

        messages = _run_query_over(frames)

        assert checks == {
            "open_after_first_result": True,
            "open_after_second_result": True,
            "closed_at_idle": True,
        }
        assert len([m for m in messages if isinstance(m, ResultMessage)]) == 2
        # The CLI marked these frames sdk_host_only, so the caller never sees
        # them.
        assert _state_frames(messages) == []

    @pytest.mark.parametrize("sdk_host_only", [True, False])
    def test_only_marked_frames_are_dropped(self, sdk_host_only):
        """Marked frames were sent only because the SDK asked; unmarked ones
        mean the caller opted in (CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS), and
        both drive the run end the same way."""
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running", sdk_host_only=sdk_host_only)
            yield _make_result("uuid-r1")
            await _let_waiter_run()
            checks["open_after_result"] = not end_input_calls
            yield _session_state("idle", sdk_host_only=sdk_host_only)
            await _let_waiter_run()
            checks["closed_at_idle"] = bool(end_input_calls)

        messages = _run_query_over(frames)

        assert checks == {"open_after_result": True, "closed_at_idle": True}
        assert _state_frames(messages) == ([] if sdk_host_only else ["running", "idle"])

    def test_no_state_frames_closes_stdin_at_the_first_result(self):
        """A CLI too old to honor CLAUDE_CODE_SDK_READS_SESSION_STATE sends no
        frames; the result is then all there is to go on."""
        checks = {}

        async def frames(end_input_calls, writes):
            yield _assistant()
            yield _make_result("uuid-r1")
            await _let_waiter_run()
            checks["closed_at_result"] = bool(end_input_calls)

        _run_query_over(frames)

        assert checks == {"closed_at_result": True}

    def test_idle_just_before_result_ends_the_run_at_the_result(self):
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _session_state("idle")
            await _let_waiter_run()
            checks["open_before_result"] = not end_input_calls
            yield _make_result("uuid-r1")
            await _let_waiter_run()
            checks["closed_at_result"] = bool(end_input_calls)

        _run_query_over(frames)

        assert checks == {"open_before_result": True, "closed_at_result": True}

    def test_idle_before_any_result_does_not_end_the_run(self):
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("idle")
            yield _session_state("running")
            yield _make_result("uuid-r1")
            await _let_waiter_run()
            checks["open_after_result"] = not end_input_calls
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_at_idle"] = bool(end_input_calls)

        _run_query_over(frames)

        assert checks == {"open_after_result": True, "closed_at_idle": True}

    def test_idle_with_a_tracked_task_in_flight_keeps_stdin_open(self):
        """A CLI that reports "idle" at every turn end still defers to the
        task ledger (#1088)."""
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield dict(_TASK_STARTED)
            yield _make_result("uuid-r1")
            yield _session_state("idle")
            await _let_waiter_run()
            checks["open_with_task_in_flight"] = not end_input_calls
            yield dict(_TASK_NOTIFICATION)
            yield _session_state("running")
            yield _make_result("uuid-r2")
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_after_task_settled"] = bool(end_input_calls)

        _run_query_over(frames)

        assert checks == {
            "open_with_task_in_flight": True,
            "closed_after_task_settled": True,
        }

    def test_async_iterable_prompt_waits_for_idle(self):
        checks = {}

        async def prompt():
            yield _user_prompt()

        async def frames(end_input_calls, writes):
            await _until(lambda: len(writes) == 1)
            yield _session_state("running")
            yield _make_result("uuid-r1")
            await _let_waiter_run()
            checks["open_after_result"] = not end_input_calls
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_at_idle"] = bool(end_input_calls)

        _run_query_over(frames, prompt=prompt())

        assert checks == {"open_after_result": True, "closed_at_idle": True}

    def test_stream_last_prompt_waits_for_its_own_run(self):
        """A prompt written after an earlier prompt's run ended owes a run of
        its own: its turn's requests need stdin too."""
        checks = {}
        second_ready = anyio.Event()

        async def prompt():
            yield _user_prompt("first")
            await second_ready.wait()
            yield _user_prompt("second")

        async def frames(end_input_calls, writes):
            await _until(lambda: len(writes) == 1)
            yield _session_state("running")
            yield _make_result("uuid-r1")
            yield _session_state("idle")
            await _let_waiter_run()
            second_ready.set()
            await _until(lambda: len(writes) == 2)
            await _let_waiter_run()
            checks["open_after_second_prompt"] = not end_input_calls
            yield _session_state("running")
            yield _make_result("uuid-r2")
            await _let_waiter_run()
            checks["open_after_second_result"] = not end_input_calls
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_at_second_idle"] = bool(end_input_calls)

        _run_query_over(frames, prompt=prompt())

        assert checks == {
            "open_after_second_prompt": True,
            "open_after_second_result": True,
            "closed_at_second_idle": True,
        }

    @pytest.mark.parametrize(
        "wake_frame",
        [_session_state("running"), _session_state("requires_action")],
        ids=["running", "requires_action"],
    )
    def test_stream_reopens_for_work_the_cli_takes_up_after_idle(self, wake_frame):
        """A background task that wakes the CLI after the run ended reopens
        it, as long as the caller's input had not ended yet."""
        checks = {}
        end_stream = anyio.Event()

        async def prompt():
            yield _user_prompt()
            await end_stream.wait()

        async def frames(end_input_calls, writes):
            await _until(lambda: len(writes) == 1)
            yield _session_state("running")
            yield _make_result("uuid-r1")
            yield _session_state("idle")
            await _let_waiter_run()
            yield dict(wake_frame)
            await _let_waiter_run()
            end_stream.set()
            await _let_waiter_run()
            checks["open_after_input_ended"] = not end_input_calls
            yield _make_result("uuid-r2")
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_at_next_idle"] = bool(end_input_calls)

        _run_query_over(frames, prompt=prompt())

        assert checks == {
            "open_after_input_ended": True,
            "closed_at_next_idle": True,
        }


class TestRunEndCeiling:
    """The wait for "idle" is bounded between turns (#1190).

    A background agent that never finishes holds "running" for good, and the
    CLI's own background-wait ceiling only starts once stdin is closed, so
    the SDK ends the run itself once CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS pass
    after a result with no new turn.
    """

    _FAST = {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "100"}

    @pytest.mark.parametrize("backend", ["asyncio", "trio"])
    def test_ceiling_ends_the_run_with_no_idle(self, backend):
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _make_result("uuid-r1")
            await _let_waiter_run()
            checks["open_right_after_result"] = not end_input_calls
            await _until(lambda: bool(end_input_calls))
            # A late idle does not close stdin a second time.
            yield _session_state("idle")
            await _let_waiter_run()
            checks["end_input_calls"] = len(end_input_calls)

        _run_query_over(frames, env=self._FAST, backend=backend)

        assert checks == {"open_right_after_result": True, "end_input_calls": 1}

    def test_main_thread_turn_stops_the_ceiling(self):
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _make_result("uuid-r1")
            # The turn the finished background agent woke starts before the
            # ceiling, then runs well past it.
            yield _assistant()
            await anyio.sleep(0.3)
            checks["open_during_turn"] = not end_input_calls
            # Its result starts the wait between turns over.
            yield _make_result("uuid-r2")
            await _let_waiter_run()
            checks["open_right_after_second_result"] = not end_input_calls
            await _until(lambda: bool(end_input_calls))

        _run_query_over(frames, env=self._FAST)

        assert checks == {
            "open_during_turn": True,
            "open_right_after_second_result": True,
        }

    def test_subagent_messages_do_not_stop_the_ceiling(self):
        """Only main-thread activity is a new turn; a background agent's own
        messages are the very work the ceiling bounds."""

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _make_result("uuid-r1")
            yield _assistant(parent_tool_use_id="toolu_agent")
            await _until(lambda: bool(end_input_calls))

        _run_query_over(frames, env=self._FAST)

    def test_requires_action_stops_the_ceiling(self):
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _make_result("uuid-r1")
            # A background agent's permission prompt waits on the host well
            # past the ceiling.
            yield _session_state("requires_action")
            await anyio.sleep(0.3)
            checks["open_while_answering"] = not end_input_calls
            # Answered: the wait between turns starts over.
            yield _session_state("running")
            await _let_waiter_run()
            checks["open_right_after_answer"] = not end_input_calls
            await _until(lambda: bool(end_input_calls))

        _run_query_over(frames, env=self._FAST)

        assert checks == {
            "open_while_answering": True,
            "open_right_after_answer": True,
        }

    def test_ceiling_is_not_armed_mid_turn(self):
        """A "running" after an answered request inside a turn does not start
        the clock; only the wait between turns counts."""
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _make_result("uuid-r1")
            # The follow-up turn starts, asks for a permission, gets it, then
            # runs a long tool with no main-thread output.
            yield _assistant()
            yield _session_state("requires_action")
            yield _session_state("running")
            await anyio.sleep(0.3)
            checks["open_during_turn"] = not end_input_calls
            yield _make_result("uuid-r2")
            await _until(lambda: bool(end_input_calls))

        _run_query_over(frames, env=self._FAST)

        assert checks == {"open_during_turn": True}

    def test_result_while_answering_a_request_does_not_arm(self):
        """A background agent's request the SDK is still answering when the
        turn's result arrives keeps the clock stopped until it is answered."""
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _session_state("requires_action")
            yield _make_result("uuid-r1")
            await anyio.sleep(0.3)
            checks["open_while_answering"] = not end_input_calls
            yield _session_state("running")
            await _let_waiter_run()
            checks["open_right_after_answer"] = not end_input_calls
            await _until(lambda: bool(end_input_calls))

        _run_query_over(frames, env=self._FAST)

        assert checks == {
            "open_while_answering": True,
            "open_right_after_answer": True,
        }

    def test_ceiling_leaves_a_tracked_agent_alone(self):
        """A tracked background agent still in flight may still need stdin
        (#1088), so the ceiling does not cut it off; the wait between turns
        starts over once it settles."""
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield dict(_TASK_STARTED)
            yield _make_result("uuid-r1")
            await anyio.sleep(0.35)
            checks["open_with_task_in_flight"] = not end_input_calls
            yield dict(_TASK_NOTIFICATION)
            await _let_waiter_run()
            checks["open_right_after_task_settled"] = not end_input_calls
            await _until(lambda: bool(end_input_calls))

        _run_query_over(frames, env=self._FAST)

        assert checks == {
            "open_with_task_in_flight": True,
            "open_right_after_task_settled": True,
        }

    @pytest.mark.parametrize(
        "ceiling", ["0", str(2**31), "9" * 400], ids=["zero", "2**31", "huge"]
    )
    def test_waits_for_idle_with_no_or_a_huge_ceiling(self, ceiling):
        """0 means no ceiling; a huge one is honored as a very long wait, not
        one that fires at once or fails."""
        checks = {}

        async def frames(end_input_calls, writes):
            yield _session_state("running")
            yield _make_result("uuid-r1")
            await anyio.sleep(0.3)
            checks["open_after_wait"] = not end_input_calls
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_at_idle"] = bool(end_input_calls)

        _run_query_over(frames, env={"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": ceiling})

        assert checks == {"open_after_wait": True, "closed_at_idle": True}

    def test_ceiling_ended_stream_reopens_for_a_main_thread_turn(self):
        checks = {}
        end_stream = anyio.Event()

        async def prompt():
            yield _user_prompt()
            await end_stream.wait()

        async def frames(end_input_calls, writes):
            await _until(lambda: len(writes) == 1)
            yield _session_state("running")
            yield _make_result("uuid-r1")
            # The ceiling passes while the caller's input is still open.
            await anyio.sleep(0.3)
            # The background agent finishes after all; its turn starts
            # streaming with no state change, and the caller's input ends.
            yield _assistant()
            await _let_waiter_run()
            end_stream.set()
            await _let_waiter_run()
            checks["open_after_input_ended"] = not end_input_calls
            yield _make_result("uuid-r2")
            yield _session_state("idle")
            await _let_waiter_run()
            checks["closed_at_idle"] = bool(end_input_calls)

        _run_query_over(frames, prompt=prompt(), env=self._FAST)

        assert checks == {"open_after_input_ended": True, "closed_at_idle": True}

    def test_no_ceiling_is_armed_once_stdin_is_closed(self):
        """Frames still arrive while the CLI winds down after stdin closed; a
        ceiling armed then would outlive the run it bounds."""

        async def _test():
            transport = AsyncMock()
            transport.is_ready = Mock(return_value=True)

            async def hook(input_data, tool_use_id, context):
                return {}

            q = Query(
                transport=transport,
                is_streaming_mode=True,
                hooks={"PreToolUse": [{"matcher": None, "hooks": [hook]}]},
                run_end_ceiling_ms=50,
            )
            q._on_session_state("running")
            q._result_received = True
            q._arm_run_end_ceiling()
            assert q._run_end_ceiling_task is not None

            q._end_run()
            await q.wait_for_result_and_end_input()
            transport.end_input.assert_awaited_once()
            assert q._run_end_ceiling_task is None

            # Work after stdin closed neither reopens the run nor arms a
            # ceiling for it.
            q._on_session_state("running")
            q._arm_run_end_ceiling()
            assert q._run_end_ceiling_task is None
            assert q._run_ended_event.is_set()
            await q.close()

        anyio.run(_test)


class TestRunEndCeilingFromEnv:
    """CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS is read as the CLI will see it."""

    @pytest.mark.parametrize(
        ("options_env", "ambient", "expected"),
        [
            ({}, None, 600_000),
            ({"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}, None, 0),
            ({"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": " 250 "}, None, 250),
            ({}, "1234", 1234),
            ({"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "250"}, "1234", 250),
            ({"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "soon"}, None, 600_000),
            ({"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "-1"}, None, 600_000),
            ({"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": ""}, "1234", 600_000),
        ],
        ids=[
            "default",
            "zero",
            "whitespace",
            "ambient",
            "options_over_ambient",
            "not_a_number",
            "negative",
            "empty_option_wins",
        ],
    )
    def test_parse(self, monkeypatch, options_env, ambient, expected):
        if ambient is None:
            monkeypatch.delenv("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", ambient)

        assert run_end_ceiling_ms(options_env) == expected


class TestAsyncIterablePromptWithSdkMcpServers:
    """Test that AsyncIterable prompts keep stdin open for SDK MCP servers."""

    def test_async_iterable_with_sdk_mcp_servers(self):
        """AsyncIterable prompt path should wait for first result before
        closing stdin when SDK MCP servers are present."""

        async def _test():
            server = _make_greet_server()
            mock_transport = _make_mock_transport(messages=_ASSISTANT_AND_RESULT)

            call_order = []
            original_write = mock_transport.write

            async def tracking_write(data):
                call_order.append(("write", data))
                return await original_write(data)

            async def tracking_end_input():
                call_order.append(("end_input",))

            mock_transport.write = tracking_write
            mock_transport.end_input = tracking_end_input

            async def prompt_stream():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": "Hello"},
                }

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt=prompt_stream(),
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)
            assert any(c[0] == "end_input" for c in call_order)

            write_calls = [c for c in call_order if c[0] == "write"]
            assert len(write_calls) >= 1
            written_data = json.loads(write_calls[0][1])
            assert written_data["type"] == "user"
            assert written_data["message"]["content"] == "Hello"

        anyio.run(_test)

    def test_async_iterable_mcp_control_requests_succeed(self):
        """MCP control requests should be handled correctly when using
        AsyncIterable prompts with SDK MCP servers."""

        async def _test():
            server = _make_greet_server()
            writes: list[str] = []
            mock_transport = _mcp_handshake_transport(writes)

            async def prompt_stream():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": "Greet Alice"},
                }

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = mock_transport

                messages = []
                async for msg in query(
                    prompt=prompt_stream(),
                    options=ClaudeAgentOptions(
                        mcp_servers={"greeter": server},
                    ),
                ):
                    messages.append(msg)

            assert len(messages) == 2
            assert isinstance(messages[0], AssistantMessage)
            assert isinstance(messages[1], ResultMessage)

            # user message + 2 MCP control responses = at least 3 writes
            assert len(writes) >= 3
            _assert_mcp_handshake_succeeded(writes)

        anyio.run(_test)


class TestNoTimeoutForHooksAndMcpServers:
    """Regression test for #730: stdin must not be closed by a timeout when
    hooks or SDK MCP servers are active."""

    def test_hooks_wait_without_timeout(self):
        """wait_for_result_and_end_input() should wait indefinitely for the
        result event when hooks are configured, not cut off after 60s."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            end_input_called = anyio.Event()

            async def tracking_end_input():
                end_input_called.set()

            mock_transport.end_input = tracking_end_input

            q = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                hooks={"PreToolUse": [{"matcher": "Bash", "hooks": ["hook_0"]}]},
            )

            async with anyio.create_task_group() as tg:

                async def wait_then_check():
                    await anyio.sleep(0.05)
                    assert not end_input_called.is_set()
                    q._run_ended_event.set()
                    await anyio.sleep(0.05)
                    assert end_input_called.is_set()

                tg.start_soon(q.wait_for_result_and_end_input)
                tg.start_soon(wait_then_check)

        anyio.run(_test)

    def test_no_hooks_closes_immediately(self):
        """Without hooks or SDK MCP servers, end_input should be called
        immediately without waiting for any event."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])

            q = Query(
                transport=mock_transport,
                is_streaming_mode=True,
            )

            await q.wait_for_result_and_end_input()
            mock_transport.end_input.assert_called_once()

        anyio.run(_test)

    def test_can_use_tool_waits_for_result(self):
        """A can_use_tool callback alone must hold stdin open until the
        run-ending result, exactly like hooks and SDK MCP servers do."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            end_input_called = anyio.Event()

            async def tracking_end_input():
                end_input_called.set()

            mock_transport.end_input = tracking_end_input

            async def allow_all(tool_name, tool_input, context):
                return PermissionResultAllow()

            q = Query(
                transport=mock_transport,
                is_streaming_mode=True,
                can_use_tool=allow_all,
            )

            async with anyio.create_task_group() as tg:

                async def wait_then_check():
                    await anyio.sleep(0.05)
                    assert not end_input_called.is_set()
                    q._run_ended_event.set()
                    await anyio.sleep(0.05)
                    assert end_input_called.is_set()

                tg.start_soon(q.wait_for_result_and_end_input)
                tg.start_soon(wait_then_check)

        anyio.run(_test)


def _make_permission_gated_transport():
    """Mock transport that enforces the real CLI contract for can_use_tool.

    - The ``can_use_tool`` control_request is only emitted after the SDK has
      written the user message.
    - The assistant/result frames are only emitted after the SDK has written
      the permission control_response.
    - Any write after ``end_input()`` raises, like a closed pipe would.

    Returns ``(transport, state)`` where ``state`` records what happened.
    """
    state: dict = {"writes": [], "ended": False, "callback_calls": []}
    user_message_written = anyio.Event()
    permission_response_written = anyio.Event()

    transport = AsyncMock()
    transport.connect = AsyncMock()
    transport.close = AsyncMock()
    transport.is_ready = Mock(return_value=True)

    async def write(data):
        if state["ended"]:
            raise RuntimeError("stdin closed")
        state["writes"].append(data)
        payload = json.loads(data)
        if payload.get("type") == "user":
            user_message_written.set()
        elif payload.get("type") == "control_response":
            permission_response_written.set()

    async def end_input():
        state["ended"] = True

    async def read_messages():
        with anyio.move_on_after(5):
            await user_message_written.wait()
        if not user_message_written.is_set():
            return
        yield {
            "type": "control_request",
            "request_id": "perm_1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Write",
                "input": {"file_path": "/tmp/x", "content": "hi"},
                "tool_use_id": "toolu_1",
            },
        }
        # The CLI cannot make progress until the permission verdict arrives.
        with anyio.move_on_after(5):
            await permission_response_written.wait()
        if not permission_response_written.is_set():
            return
        for msg in _ASSISTANT_AND_RESULT:
            yield msg

    transport.write = write
    transport.end_input = end_input
    transport.read_messages = read_messages
    return transport, state


class TestCanUseToolKeepsStdinOpen:
    """A ``can_use_tool`` callback is served over the control protocol, so
    stdin must stay open until the run-ending result for the permission
    verdict to reach the CLI. Previously only hooks and SDK MCP servers held
    stdin open, so ``query()`` with a finite prompt and only ``can_use_tool``
    closed stdin as soon as the prompt was written and every permission
    request failed CLI-side with "Stream closed"."""

    @staticmethod
    async def _allow_all(state):
        async def callback(tool_name, tool_input, context):
            state["callback_calls"].append(tool_name)
            return PermissionResultAllow()

        return callback

    def _run_query(self, prompt_factory):
        async def _test():
            transport, state = _make_permission_gated_transport()
            callback = await self._allow_all(state)

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = transport
                messages = [
                    msg
                    async for msg in query(
                        prompt=prompt_factory(),
                        options=ClaudeAgentOptions(can_use_tool=callback),
                    )
                ]
            return messages, state

        return anyio.run(_test)

    def test_async_iterable_prompt_with_can_use_tool_waits_for_result(self):
        async def prompt_stream():
            yield {"type": "user", "message": {"role": "user", "content": "write it"}}

        messages, state = self._run_query(prompt_stream)

        assert state["callback_calls"] == ["Write"]
        responses = [
            json.loads(w) for w in state["writes"] if '"control_response"' in w
        ]
        assert len(responses) == 1
        assert responses[0]["response"]["subtype"] == "success"
        assert responses[0]["response"]["response"]["behavior"] == "allow"
        assert [type(m) for m in messages] == [AssistantMessage, ResultMessage]
        assert state["ended"] is True

    def test_string_prompt_with_can_use_tool_is_supported(self):
        """String prompts are streamed over stdin internally, so can_use_tool
        no longer needs an AsyncIterable prompt."""
        messages, state = self._run_query(lambda: "write it")

        assert state["callback_calls"] == ["Write"]
        assert [type(m) for m in messages] == [AssistantMessage, ResultMessage]
        assert state["ended"] is True

    def test_prompt_iterable_that_raises_after_a_message_still_closes_stdin(self):
        """If the caller's prompt iterable fails after sending a message, the
        turn already sent still completes (permission round-trip included)
        and stdin is closed afterwards instead of being left open forever."""

        async def prompt_stream():
            yield {"type": "user", "message": {"role": "user", "content": "write it"}}
            raise RuntimeError("caller's generator blew up")

        messages, state = self._run_query(prompt_stream)

        assert state["callback_calls"] == ["Write"]
        assert [type(m) for m in messages] == [AssistantMessage, ResultMessage]
        assert state["ended"] is True

    def test_prompt_iterable_that_raises_immediately_closes_stdin(self):
        """Nothing was sent, so no result can release the hold: stdin must be
        closed right away or the CLI (and the consumer) would wait forever."""

        async def _test():
            ended = anyio.Event()
            transport = _make_mock_transport(messages=[])

            async def end_input():
                ended.set()

            async def read_messages():
                # Like the real CLI: produce nothing and stay alive until
                # stdin is closed.
                await ended.wait()
                return
                yield  # pragma: no cover

            transport.end_input = end_input
            transport.read_messages = read_messages

            async def prompt_stream():
                raise RuntimeError("caller's generator blew up")
                yield  # pragma: no cover

            async def allow_all(tool_name, tool_input, context):
                return PermissionResultAllow()

            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_cls,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_cls.return_value = transport
                with anyio.fail_after(5):
                    messages = [
                        msg
                        async for msg in query(
                            prompt=prompt_stream(),
                            options=ClaudeAgentOptions(can_use_tool=allow_all),
                        )
                    ]
            assert messages == []
            assert ended.is_set()

        anyio.run(_test)


class TestQueryCrossTaskCleanup:
    """Tests for cross-task cleanup of Query task groups (issue #454).

    When a user breaks out of an async for loop over process_query(), Python
    finalizes the async generator in a different task than the one that called
    start(). This triggers close() from a different task context, which causes
    anyio to raise RuntimeError because cancel scopes must be exited by the
    same task that entered them. These tests verify that close() handles this
    gracefully.
    """

    @pytest.mark.anyio
    async def test_close_from_different_task_does_not_raise(self):
        """close() called from a different task than start() must not raise."""
        mock_transport = _make_mock_transport(messages=[])
        q = Query(transport=mock_transport, is_streaming_mode=True)

        await q.start()

        close_error = None

        async def close_in_other_task():
            nonlocal close_error
            try:
                await q.close()
            except Exception as e:
                close_error = e

        async with anyio.create_task_group() as tg:
            tg.start_soon(close_in_other_task)

        assert close_error is None, f"close() raised: {close_error}"

    def test_close_from_same_task_still_works(self):
        """close() from the same task as start() should still work normally."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            q = Query(transport=mock_transport, is_streaming_mode=True)

            await q.start()
            await q.close()

            assert q._read_task is None
            mock_transport.close.assert_called_once()

        anyio.run(_test)


@pytest.mark.filterwarnings(
    "ignore:Unclosed <MemoryObjectReceiveStream:ResourceWarning"
)
class TestQueryTrioBackend:
    """Regression tests for trio compatibility.

    ``Query`` uses detached background tasks rather than an anyio
    ``TaskGroup`` (whose cancel scope has task affinity). The asyncio
    implementation of that (``loop.create_task()``) raises ``RuntimeError``
    under trio; these tests run start/spawn_task/close on the trio backend
    to guard the sniffio-dispatch path.

    The ResourceWarning filter is for ``_message_receive``: ``Query`` owns
    the send side (and closes it), but the receive side is the consumer's
    to close. Tests that don't iterate ``receive_messages()`` leave it
    unclosed; trio's GC timing surfaces anyio's ``__del__`` warning.
    """

    def test_start_and_close_under_trio(self):
        """start() + close() under trio must not raise."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            q = Query(transport=mock_transport, is_streaming_mode=True)

            await q.start()
            await q.close()

            assert q._read_task is None
            mock_transport.close.assert_called_once()

        anyio.run(_test, backend="trio")

    def test_spawn_task_and_cancel_under_trio(self):
        """spawn_task() under trio tracks and cancels child tasks on close()."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            q = Query(transport=mock_transport, is_streaming_mode=True)

            await q.start()

            async def _slow():
                await anyio.sleep(10)

            handle = q.spawn_task(_slow())
            assert handle in q._child_tasks

            await q.close()
            # close() cancels child tasks; give the system task a tick to
            # fire its done callback that removes it from the set.
            await anyio.sleep(0)
            assert len(q._child_tasks) == 0

        anyio.run(_test, backend="trio")

    def test_close_from_different_task_under_trio(self):
        """close() from a different task than start() must not raise (trio)."""

        async def _test():
            mock_transport = _make_mock_transport(messages=[])
            q = Query(transport=mock_transport, is_streaming_mode=True)

            await q.start()

            close_error = []

            async def close_in_other_task():
                try:
                    await q.close()
                except Exception as e:
                    close_error.append(e)

            async with anyio.create_task_group() as tg:
                tg.start_soon(close_in_other_task)

            assert close_error == [], f"close() raised: {close_error}"

        anyio.run(_test, backend="trio")

    @staticmethod
    def _make_blocking_transport():
        """Mock transport whose read_messages() blocks forever.

        Needed to reproduce the level-triggered-cancellation hang: the
        read task must still be running when close() cancels it, so the
        finally block executes inside a cancelled scope.
        """
        mock_transport = AsyncMock()

        async def blocking_read():
            await anyio.Event().wait()  # never set
            yield {}  # pragma: no cover - unreachable, makes this a generator

        mock_transport.read_messages = blocking_read
        mock_transport.connect = AsyncMock()
        mock_transport.close = AsyncMock()
        mock_transport.end_input = AsyncMock()
        mock_transport.write = AsyncMock()
        mock_transport.is_ready = Mock(return_value=True)
        return mock_transport

    def test_receive_messages_unblocks_on_close_under_trio(self):
        """Consumer blocked in receive_messages() must unblock on close().

        trio's level-triggered cancellation re-raises Cancelled at every
        checkpoint inside a cancelled scope; if the end sentinel is sent
        via ``await send()`` in the read task's ``finally``, it is dropped
        and the consumer hangs. ``send_nowait`` is checkpoint-free.
        """

        async def _test():
            with anyio.fail_after(5.0):
                mock_transport = self._make_blocking_transport()
                q = Query(transport=mock_transport, is_streaming_mode=True)
                await q.start()

                consumer_done = anyio.Event()

                async def consumer():
                    async for _msg in q.receive_messages():
                        pass
                    consumer_done.set()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(consumer)
                    await anyio.sleep(0.01)  # let consumer block on receive
                    await q.close()
                    await consumer_done.wait()

                assert consumer_done.is_set()

        anyio.run(_test, backend="trio")

    def test_receive_messages_unblocks_on_close_under_asyncio(self):
        """asyncio parity for the unblock-on-close test above."""

        async def _test():
            with anyio.fail_after(5.0):
                mock_transport = self._make_blocking_transport()
                q = Query(transport=mock_transport, is_streaming_mode=True)
                await q.start()

                consumer_done = anyio.Event()

                async def consumer():
                    async for _msg in q.receive_messages():
                        pass
                    consumer_done.set()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(consumer)
                    await anyio.sleep(0.01)
                    await q.close()
                    await consumer_done.wait()

                assert consumer_done.is_set()

        anyio.run(_test, backend="asyncio")

    def _run_buffered_drain_after_close(self, backend: str) -> None:
        async def _test():
            with anyio.fail_after(5.0):
                mock_transport = self._make_blocking_transport()
                q = Query(transport=mock_transport, is_streaming_mode=True)
                await q.start()

                # Buffer 3 messages directly (bypassing the read task,
                # which is blocked on the transport).
                for i in range(3):
                    q._message_send.send_nowait({"type": "user", "i": i})

                consumed: list[dict] = []
                consumer_error: list[BaseException] = []
                got_first = anyio.Event()
                in_user_code = anyio.Event()

                async def consumer():
                    try:
                        async for msg in q.receive_messages():
                            consumed.append(msg)
                            if len(consumed) == 1:
                                got_first.set()
                                # Stay in user code (NOT parked in
                                # receive()) while close() runs.
                                await in_user_code.wait()
                    except BaseException as e:  # noqa: BLE001
                        consumer_error.append(e)

                async with anyio.create_task_group() as tg:
                    tg.start_soon(consumer)
                    await got_first.wait()
                    # Consumer is now awaiting in_user_code (user code),
                    # with 2 messages still buffered.
                    await q.close()
                    in_user_code.set()

                assert consumer_error == [], (
                    f"[{backend}] consumer raised: {consumer_error}"
                )
                assert len(consumed) == 3, (
                    f"[{backend}] expected 3 messages, got {len(consumed)}: {consumed}"
                )

        anyio.run(_test, backend=backend)

    def test_buffered_messages_drain_after_close_asyncio(self):
        """Consumer in user code when close() runs must drain the buffer.

        anyio's ``receive_nowait()`` checks ``_closed`` before the buffer,
        so closing ``_message_receive`` from ``close()`` would make a
        non-parked consumer hit ``ClosedResourceError`` and drop buffered
        messages. ``_message_send.close()`` alone yields ``EndOfStream``
        only after the buffer drains.
        """
        self._run_buffered_drain_after_close("asyncio")

    def test_buffered_messages_drain_after_close_trio(self):
        """trio parity for the buffered-drain-after-close test above."""
        self._run_buffered_drain_after_close("trio")


class TestControlCancelRequest:
    """Tests for control_cancel_request handling (issue #739).

    When the CLI sends a control_cancel_request, the SDK should cancel the
    matching in-flight _handle_control_request task so it stops executing and
    does not write a response for a request the CLI has already abandoned.
    """

    @pytest.mark.anyio
    async def test_cancel_request_cancels_inflight_hook(self):
        """A control_cancel_request should cancel the matching hook task."""
        hook_started = anyio.Event()
        hook_cancelled = anyio.Event()

        async def slow_hook(input_data, tool_use_id, context):
            hook_started.set()
            try:
                await anyio.sleep(10)
            except anyio.get_cancelled_exc_class():
                hook_cancelled.set()
                raise
            return {}

        mock_transport = AsyncMock()
        emitted: list[dict] = []

        async def mock_receive():
            yield {
                "type": "control_request",
                "request_id": "hook_1",
                "request": {
                    "subtype": "hook_callback",
                    "callback_id": "hook_0",
                },
            }
            await hook_started.wait()
            yield {
                "type": "control_cancel_request",
                "request_id": "hook_1",
            }
            await hook_cancelled.wait()

        async def mock_write(data):
            emitted.append(json.loads(data))

        mock_transport.read_messages = mock_receive
        mock_transport.write = mock_write
        mock_transport.close = AsyncMock()
        mock_transport.is_ready = Mock(return_value=True)

        q = Query(transport=mock_transport, is_streaming_mode=True)
        q.hook_callbacks["hook_0"] = slow_hook

        await q.start()
        with anyio.fail_after(5):
            await hook_cancelled.wait()
        await q.close()

        assert hook_cancelled.is_set()
        assert "hook_1" not in q._inflight_requests
        responses = [m for m in emitted if m.get("type") == "control_response"]
        assert responses == [], (
            f"Cancelled request should not write a response, got: {responses}"
        )

    @pytest.mark.anyio
    async def test_cancel_request_for_unknown_id_is_noop(self):
        """A control_cancel_request for an unknown request_id should not raise."""
        mock_transport = _make_mock_transport(
            messages=[
                {
                    "type": "control_cancel_request",
                    "request_id": "nonexistent",
                },
            ]
            + _ASSISTANT_AND_RESULT
        )
        q = Query(transport=mock_transport, is_streaming_mode=True)

        await q.start()
        messages = []
        async for msg in q.receive_messages():
            messages.append(msg)
        await q.close()

        assert any(m.get("type") == "result" for m in messages)

    @pytest.mark.anyio
    async def test_completed_request_is_removed_from_inflight(self):
        """Once a control_request handler completes, it should be removed from
        _inflight_requests so a late cancel is a no-op."""

        async def fast_hook(input_data, tool_use_id, context):
            return {}

        mock_transport = _make_mock_transport(
            messages=_ASSISTANT_AND_RESULT,
            control_requests=[
                {
                    "type": "control_request",
                    "request_id": "fast_1",
                    "request": {
                        "subtype": "hook_callback",
                        "callback_id": "hook_0",
                    },
                }
            ],
        )
        q = Query(transport=mock_transport, is_streaming_mode=True)
        q.hook_callbacks["hook_0"] = fast_hook

        await q.start()
        async for msg in q.receive_messages():
            if msg.get("type") == "result":
                break
        await anyio.sleep(0)
        await q.close()

        assert "fast_1" not in q._inflight_requests


class TestProcessExitAfterErrorResult:
    """Regression tests for #913: when the CLI emits a result message with
    is_error=True (e.g. subtype=error_max_turns) and then exits non-zero,
    the trailing ProcessError carries no information beyond "exit code 1".
    Replace it with the structured error text the CLI already reported so
    the exception is actionable. Mirrors the TypeScript SDK (Query.ts)."""

    def _make_transport_then_raise(self, messages, exc):
        mock_transport = AsyncMock()

        async def mock_receive():
            for msg in messages:
                yield msg
            raise exc

        mock_transport.read_messages = mock_receive
        mock_transport.connect = AsyncMock()
        mock_transport.close = AsyncMock()
        mock_transport.end_input = AsyncMock()
        mock_transport.write = AsyncMock()
        mock_transport.is_ready = Mock(return_value=True)
        return mock_transport

    def _error_result(self, subtype="error_max_turns", errors=None, **overrides):
        msg = {
            "type": "result",
            "subtype": subtype,
            "is_error": True,
            "num_turns": 1,
            "session_id": "s",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "total_cost_usd": 0.0,
        }
        if errors is not None:
            msg["errors"] = errors
        msg.update(overrides)
        return msg

    def test_process_error_after_error_result_uses_result_error_text(self):
        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(
                        subtype="error_max_turns",
                        errors=["Reached maximum number of turns (60)"],
                        num_turns=60,
                    )
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            received = []
            with pytest.raises(
                ProcessError,
                match=r"Claude Code returned an error result: "
                r"Reached maximum number of turns \(60\)",
            ) as exc_info:
                async for msg in q.receive_messages():
                    received.append(msg)
            await q.close()

            assert len(received) == 1
            assert received[0]["subtype"] == "error_max_turns"
            # The stream raises a typed ResultError (a ProcessError), not a
            # bare Exception(str): payload and exit code preserved, original
            # exit error chained.
            err = exc_info.value
            assert type(err) is ResultError
            assert isinstance(err, ProcessError)
            assert err.exit_code == 1
            assert err.subtype == "error_max_turns"
            assert err.errors == ["Reached maximum number of turns (60)"]
            assert err.data is received[0]
            assert isinstance(err.__cause__, ProcessError)
            assert "Command failed" in str(err.__cause__)

        anyio.run(_test)

    def test_api_error_result_uses_result_text_not_success_subtype(self):
        """A run that ends on an API failure is reported as subtype=success,
        is_error=True, errors=[] with the prose in `result`. The raised error
        must carry that prose — never "returned an error result: success"."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(
                        subtype="success",
                        errors=[],
                        result="API Error: Stream idle timeout - no chunks received",
                        api_error_status=None,
                        terminal_reason="api_error",
                    )
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(ResultError) as exc_info:
                async for _ in q.receive_messages():
                    pass
            await q.close()

            err = exc_info.value
            text = str(err)
            assert (
                "Claude Code returned an error result: "
                "API Error: Stream idle timeout - no chunks received" in text
            )
            assert "error result: success" not in text
            # The "mid-turn API failure" shape is recoverable from the payload.
            assert err.subtype == "success"
            assert err.terminal_reason == "api_error"
            assert err.result == "API Error: Stream idle timeout - no chunks received"
            assert err.errors == []
            assert err.session_id == "s"

        anyio.run(_test)

    def test_api_error_result_without_text_uses_http_status(self):
        """If neither errors[] nor result carry text, fall back to the HTTP
        status rather than the meaningless "success" subtype."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(
                        subtype="success", errors=[], result="", api_error_status=529
                    )
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(
                ProcessError,
                match=r"Claude Code returned an error result: API error \(HTTP 529\)",
            ):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    def test_blank_errors_fall_back_to_subtype(self):
        """errors=[""] must not produce an empty-suffixed message."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(subtype="error_during_execution", errors=[" "])
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(
                ProcessError,
                match=r"Claude Code returned an error result: error_during_execution",
            ):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    @pytest.mark.parametrize(
        ("errors", "expected"),
        [("boom", "boom"), (42, "error_during_execution")],
    )
    def test_malformed_errors_field_does_not_break_reader(self, errors, expected):
        """A non-list `errors` must neither be split per character nor crash
        the read loop with an unrelated TypeError."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(subtype="error_during_execution", errors=errors)
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(
                ProcessError,
                match=rf"Claude Code returned an error result: {expected} \(",
            ):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    def test_non_process_error_type_is_preserved(self):
        """Transport failures other than ProcessError keep their type when
        re-raised from the message stream."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[],
                exc=CLIConnectionError("lost the CLI"),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(CLIConnectionError, match="lost the CLI"):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    def test_process_error_after_error_result_falls_back_to_subtype(self):
        """When the result has no errors[] (older CLI / minimal payload), the
        improved message falls back to the subtype so it's still actionable."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[self._error_result(subtype="error_during_execution")],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(
                Exception,
                match=r"Claude Code returned an error result: error_during_execution",
            ):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    def test_process_error_after_error_result_joins_multiple_errors(self):
        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(
                        subtype="error_during_execution",
                        errors=["tool timed out", "ENOENT: missing file"],
                    )
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(
                Exception,
                match=r"tool timed out; ENOENT: missing file",
            ):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    def test_pending_initialize_gets_result_error_text(self):
        """An error result emitted during CLI startup (e.g. a refused resume)
        arrives before the initialize response. The in-flight initialize must
        fail with the same actionable text, not the bare exit code."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(
                        subtype="error_during_execution",
                        errors=["Resume rejected by --resume-drops-turn: nope"],
                        num_turns=0,
                    )
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(ProcessError) as exc_info:
                await q.initialize()
            assert (
                "Claude Code returned an error result: "
                "Resume rejected by --resume-drops-turn: nope" in str(exc_info.value)
            )
            assert exc_info.value.exit_code == 1
            assert isinstance(exc_info.value, ResultError)
            assert exc_info.value.subtype == "error_during_execution"
            await q.close()

        anyio.run(_test)

    def test_process_error_without_result_keeps_original_message(self):
        async def _test():
            transport = self._make_transport_then_raise(
                messages=[],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(Exception, match="Command failed"):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    def test_process_error_after_success_result_keeps_original_message(self):
        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "s",
                        "duration_ms": 1,
                        "duration_api_ms": 1,
                        "total_cost_usd": 0.0,
                    }
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            received = []
            with pytest.raises(Exception, match="Command failed"):
                async for msg in q.receive_messages():
                    received.append(msg)
            await q.close()

            assert len(received) == 1
            assert received[0]["subtype"] == "success"

        anyio.run(_test)

    def test_process_error_after_error_then_success_result_keeps_original(self):
        """Tracks the *most recent* result, not a sticky latch."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(subtype="error_during_execution"),
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "num_turns": 2,
                        "session_id": "s",
                        "duration_ms": 1,
                        "duration_api_ms": 1,
                        "total_cost_usd": 0.0,
                    },
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            received = []
            with pytest.raises(Exception, match="Command failed"):
                async for msg in q.receive_messages():
                    received.append(msg)
            await q.close()

            assert len(received) == 2

        anyio.run(_test)

    def test_session_state_changed_after_error_result_preserves_replacement(self):
        """The CLI emits a post-turn `system: session_state_changed(idle)`
        marker after the result and before exit. It must not reset the
        tracking flag — the conversation hasn't moved on."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(
                        subtype="error_max_turns",
                        errors=["Reached maximum number of turns (10)"],
                    ),
                    {
                        "type": "system",
                        "subtype": "session_state_changed",
                        "state": "idle",
                        "session_id": "s",
                    },
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            with pytest.raises(
                Exception, match=r"Claude Code returned an error result"
            ):
                async for _ in q.receive_messages():
                    pass
            await q.close()

        anyio.run(_test)

    def test_new_turn_after_error_result_keeps_original_message(self):
        """A new user turn invalidates the 'expecting imminent exit' state from
        a prior turn's error result; a crash mid-new-turn must surface as-is."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[
                    self._error_result(subtype="error_during_execution"),
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "next turn"},
                        "session_id": "s",
                    },
                ],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)
            await q.start()

            received = []
            with pytest.raises(Exception, match="Command failed"):
                async for msg in q.receive_messages():
                    received.append(msg)
            await q.close()

            assert len(received) == 2

        anyio.run(_test)

    def test_pending_control_requests_fail_fast_on_replaced_error(self):
        """In-flight control requests must still fail fast (process is dead;
        no control_response will ever arrive) regardless of message replacement."""

        async def _test():
            transport = self._make_transport_then_raise(
                messages=[self._error_result(subtype="error_max_turns")],
                exc=ProcessError(
                    "Command failed with exit code 1", exit_code=1, stderr=""
                ),
            )
            q = Query(transport=transport, is_streaming_mode=True)

            # Register a pending control request before the read loop runs.
            event = anyio.Event()
            q.pending_control_responses["req_1"] = event

            await q.start()
            with pytest.raises(
                Exception, match=r"Claude Code returned an error result"
            ):
                async for _ in q.receive_messages():
                    pass
            await q.close()

            assert event.is_set()
            assert isinstance(q.pending_control_results["req_1"], ProcessError)

        anyio.run(_test)
