"""End-to-end test: subagent transcript reads recover parent_tool_use_id."""

import pytest

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    InMemorySessionStore,
    ResultMessage,
    ToolUseBlock,
    get_subagent_messages,
    get_subagent_messages_from_store,
    list_subagents,
    list_subagents_from_store,
    query,
)

_AGENT_TOOL_NAMES = {"Agent", "Task"}

_PROMPT = (
    "Use the Agent tool exactly once with subagent_type 'greeter', prompt "
    "'say hi', and run_in_background set to false. Then reply with the single "
    "word DONE."
)


@pytest.mark.e2e
@pytest.mark.anyio
async def test_subagent_messages_carry_parent_tool_use_id():
    """After a run that spawns a subagent, both get_subagent_messages() (local
    transcript + .meta.json sidecar) and get_subagent_messages_from_store()
    (mirrored agent_metadata entry) attribute the subagent's messages to the
    Agent tool_use that spawned it."""
    store = InMemorySessionStore()
    options = ClaudeAgentOptions(
        session_store=store,
        agents={
            "greeter": AgentDefinition(
                description="Replies with a short greeting. Use for greeting tasks.",
                prompt="Reply with one short friendly sentence. Do not use any tools.",
                model="haiku",
                background=False,
            )
        },
        allowed_tools=list(_AGENT_TOOL_NAMES),
        max_turns=4,
    )

    session_id: str | None = None
    agent_tool_use_ids: set[str] = set()
    async for message in query(prompt=_PROMPT, options=options):
        if isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name in _AGENT_TOOL_NAMES:
                    agent_tool_use_ids.add(block.id)
        elif isinstance(message, ResultMessage):
            assert not message.is_error, f"run failed: {message}"
            session_id = message.session_id

    assert session_id, "run should report a session id"
    assert agent_tool_use_ids, "the model should have called the Agent tool"

    def attributed(messages_by_agent: dict[str, list]) -> dict[str, set]:
        """agent_id -> parent_tool_use_ids, for transcripts that map to an
        Agent tool_use we saw in the live stream. Other agent-*.jsonl files
        the CLI may keep under the session are ignored."""
        return {
            agent_id: {m.parent_tool_use_id for m in messages}
            for agent_id, messages in messages_by_agent.items()
            if messages
            and {m.parent_tool_use_id for m in messages} <= agent_tool_use_ids
        }

    # Local transcript path (reads the .meta.json sidecar).
    local = {
        agent_id: get_subagent_messages(session_id, agent_id)
        for agent_id in list_subagents(session_id)
    }
    local_attributed = attributed(local)
    assert local_attributed, (
        "expected a subagent transcript attributed to one of "
        f"{agent_tool_use_ids}, got "
        f"{ {a: {m.parent_tool_use_id for m in ms} for a, ms in local.items()} }"
    )
    for agent_id in local_attributed:
        # Spawned by the main session, not by another subagent.
        assert all(m.parent_agent_id is None for m in local[agent_id])

    # SessionStore path (reads the mirrored agent_metadata entry).
    mirrored = {
        agent_id: await get_subagent_messages_from_store(store, session_id, agent_id)
        for agent_id in await list_subagents_from_store(store, session_id)
    }
    mirrored_attributed = attributed(mirrored)
    assert mirrored_attributed, (
        "expected a mirrored subagent transcript attributed to one of "
        f"{agent_tool_use_ids}, got "
        f"{ {a: {m.parent_tool_use_id for m in ms} for a, ms in mirrored.items()} }"
    )
    # The same subagents are attributed the same way on both paths.
    assert {
        a: ids for a, ids in local_attributed.items() if a in mirrored_attributed
    } == {a: ids for a, ids in mirrored_attributed.items() if a in local_attributed}
    assert set(local_attributed) & set(mirrored_attributed)
    for agent_id in mirrored_attributed:
        assert all(m.parent_agent_id is None for m in mirrored[agent_id])
