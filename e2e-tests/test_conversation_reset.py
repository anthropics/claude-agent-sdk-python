"""End-to-end test for ConversationResetMessage with a real CLI session.

In streaming input mode, sending ``/clear`` resets the conversation without
ending the connection. The CLI emits a ``conversation_reset`` frame so the SDK
consumer can react (snapshot totals, clear a rendered transcript, etc.).
"""

import anyio
import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ConversationResetMessage,
    ResultMessage,
)


@pytest.mark.e2e
@pytest.mark.anyio
async def test_clear_emits_conversation_reset():
    """`/clear` mid-session yields a ConversationResetMessage for the old session."""
    options = ClaudeAgentOptions(max_turns=1)

    first_result: ResultMessage | None = None
    reset: ConversationResetMessage | None = None
    clear_result: ResultMessage | None = None

    async with ClaudeSDKClient(options=options) as client:
        with anyio.fail_after(120):
            await client.query("Reply with exactly: one")
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    first_result = message
        assert first_result is not None

        with anyio.fail_after(120):
            await client.query("/clear")
            async for message in client.receive_response():
                if isinstance(message, ConversationResetMessage):
                    reset = message
                elif isinstance(message, ResultMessage):
                    clear_result = message

    assert reset is not None, "No ConversationResetMessage received after /clear"
    # The reset frame is stamped with the outgoing session's id...
    assert reset.session_id == first_result.session_id
    assert reset.new_conversation_id
    assert reset.uuid
    # ...and everything after it belongs to a fresh session.
    assert clear_result is not None
    assert clear_result.session_id != first_result.session_id
