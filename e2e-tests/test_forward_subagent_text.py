"""End-to-end tests for the forward_subagent_text option with real Claude API calls."""

import pytest

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

_AGENT_TOOL_NAMES = {"Agent", "Task"}


def _options(forward_subagent_text: bool) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        forward_subagent_text=forward_subagent_text,
        agents={
            "greeter": AgentDefinition(
                description="Replies with a short greeting. Use for greeting tasks.",
                prompt="Reply with one short friendly sentence. Do not use any tools.",
                model="haiku",
                # Foreground so the run exercises the synchronous subagent
                # path, where text forwarding is opt-in.
                background=False,
            )
        },
        allowed_tools=list(_AGENT_TOOL_NAMES),
        max_turns=4,
    )


_PROMPT = (
    "Use the Agent tool exactly once with subagent_type 'greeter', prompt "
    "'say hi', and run_in_background set to false. Then reply with the single "
    "word DONE."
)


async def _collect(forward_subagent_text: bool):
    """Run the prompt and return (agent_tool_use_ids, attributed assistant msgs)."""
    agent_tool_use_ids: set[str] = set()
    attributed: list[AssistantMessage] = []
    result: ResultMessage | None = None
    async for message in query(prompt=_PROMPT, options=_options(forward_subagent_text)):
        if isinstance(message, AssistantMessage):
            if message.parent_tool_use_id is None:
                for block in message.content:
                    if (
                        isinstance(block, ToolUseBlock)
                        and block.name in _AGENT_TOOL_NAMES
                    ):
                        agent_tool_use_ids.add(block.id)
            else:
                attributed.append(message)
        elif isinstance(message, ResultMessage):
            result = message
    assert result is not None and not result.is_error, f"run failed: {result}"
    assert agent_tool_use_ids, "the model should have called the Agent tool"
    return agent_tool_use_ids, attributed


@pytest.mark.e2e
@pytest.mark.anyio
async def test_forward_subagent_text_delivers_attributed_text():
    """With forward_subagent_text=True the subagent's text blocks arrive as
    AssistantMessages whose parent_tool_use_id is the Agent tool_use id."""
    agent_tool_use_ids, attributed = await _collect(forward_subagent_text=True)

    text_messages = [
        m for m in attributed if any(isinstance(b, TextBlock) for b in m.content)
    ]
    assert text_messages, (
        "expected at least one subagent text message with parent_tool_use_id set, "
        f"got attributed={[[type(b).__name__ for b in m.content] for m in attributed]}"
    )
    for m in text_messages:
        assert m.parent_tool_use_id in agent_tool_use_ids, (
            m.parent_tool_use_id,
            agent_tool_use_ids,
        )


@pytest.mark.e2e
@pytest.mark.anyio
async def test_subagent_text_not_forwarded_by_default():
    """Without the option, a foreground subagent only surfaces tool_use /
    tool_result blocks — its text stays out of the parent stream."""
    _, attributed = await _collect(forward_subagent_text=False)

    leaked = [m for m in attributed if any(isinstance(b, TextBlock) for b in m.content)]
    assert not leaked, (
        "subagent text should not be forwarded by default, got: "
        f"{[[type(b).__name__ for b in m.content] for m in leaked]}"
    )
