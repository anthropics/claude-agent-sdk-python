"""End-to-end test: session reads return a parallel tool turn whole."""

from pathlib import Path
from typing import Any

import anyio
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    InMemorySessionStore,
    ResultMessage,
    SessionMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    get_session_messages,
    get_session_messages_from_store,
    project_key_for_directory,
    tool,
)

_KEYS = ("alpha", "beta", "gamma")
_TOOL = "mcp__slow__lookup"

_PROMPT = (
    f"Look up {', '.join(_KEYS)} with the {_TOOL} tool, one call per key. "
    f"Make all {len(_KEYS)} tool calls in a single response, so they run in "
    "parallel: do not wait for one result before making the next call. Then "
    "reply with the single word DONE."
)


def _block_ids(
    messages: list[SessionMessage], block_type: str, field: str
) -> list[tuple[int, str]]:
    """``(message index, id)`` of every ``block_type`` content block, in order."""
    return [
        (index, block[field])
        for index, message in enumerate(messages)
        if isinstance(message.message.get("content"), list)
        for block in message.message["content"]
        if isinstance(block, dict) and block.get("type") == block_type
    ]


@pytest.mark.e2e
@pytest.mark.anyio
async def test_parallel_tool_calls_are_read_back_whole(tmp_path: Path) -> None:
    """After a turn that makes several tool calls in one response, both
    get_session_messages() (local transcript) and
    get_session_messages_from_store() (mirrored entries) return every tool
    call the live stream showed and every one of their results.

    The CLI writes each result under its own call's entry. When the calls
    are all written before the first result, the transcript forks and only
    one branch leads to the next turn."""
    cwd = str(tmp_path)

    @tool("lookup", "Look up the value stored for a key", {"key": str})
    async def lookup(args: dict[str, Any]) -> dict[str, Any]:
        # Slow, so every call of the response is written before any result.
        await anyio.sleep(1)
        return {"content": [{"type": "text", "text": f"value of {args['key']}"}]}

    store = InMemorySessionStore()
    options = ClaudeAgentOptions(
        cwd=cwd,
        session_store=store,
        mcp_servers={"slow": create_sdk_mcp_server(name="slow", tools=[lookup])},
        allowed_tools=[_TOOL],
        max_turns=4,
    )

    session_id: str | None = None
    live_calls: set[str] = set()
    live_results: set[str] = set()
    async with ClaudeSDKClient(options) as client:
        with anyio.fail_after(180):
            await client.query(_PROMPT)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    assert not message.is_error, f"run failed: {message}"
                    session_id = message.session_id
                elif not isinstance(message, (AssistantMessage, UserMessage)):
                    continue
                elif message.parent_tool_use_id is None and isinstance(
                    message.content, list
                ):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            live_calls.add(block.id)
                        elif isinstance(block, ToolResultBlock):
                            live_results.add(block.tool_use_id)

    assert session_id, "run should report a session id"
    assert live_calls, "the model should have called the lookup tool"
    assert live_calls == live_results, "every live tool call should have a result"

    # The readers are only under test if the transcript forked: some entry is
    # the parent of two user/assistant entries. Whether it does is up to the
    # model, which may make the calls one response at a time.
    raw = await store.load(
        {"project_key": project_key_for_directory(cwd), "session_id": session_id}
    )
    parent_of = {
        entry["uuid"]: entry.get("parentUuid")
        for entry in raw or []
        if entry.get("type") in ("user", "assistant") and "uuid" in entry
    }
    parents = [parent for parent in parent_of.values() if parent]
    if len(parents) == len(set(parents)):
        pytest.skip("the model did not make parallel tool calls")

    local = get_session_messages(session_id, directory=cwd)
    mirrored = await get_session_messages_from_store(store, session_id, directory=cwd)
    for reader, messages in (("local", local), ("store", mirrored)):
        shape = [(m.type, m.uuid) for m in messages]
        calls = _block_ids(messages, "tool_use", "id")
        results = _block_ids(messages, "tool_result", "tool_use_id")

        # Every call and every result, each exactly once.
        assert sorted(i for _, i in calls) == sorted(live_calls), (reader, shape)
        assert sorted(i for _, i in results) == sorted(live_calls), (reader, shape)
        assert len({m.uuid for m in messages}) == len(messages), (reader, shape)

        # Every result follows its call, with nothing but that call's own
        # response in between, and the conversation still ends on the reply.
        call_index = {call_id: index for index, call_id in calls}
        response_at = {
            index: m.message.get("id")
            for index, m in enumerate(messages)
            if m.type == "assistant"
        }
        for index, call_id in results:
            start = call_index[call_id]
            assert start < index, (reader, shape)
            assert {
                response_at[i] for i in range(start, index) if i in response_at
            } == {response_at[start]}, (reader, shape)
        assert messages[0].type == "user", (reader, shape)
        assert messages[-1].type == "assistant", (reader, shape)
        assert not _block_ids(messages[-1:], "tool_use", "id"), (reader, shape)

    assert [m.uuid for m in local] == [m.uuid for m in mirrored]
