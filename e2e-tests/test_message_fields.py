"""End-to-end tests for preserved message fields with real Claude API calls.

These tests verify that AssistantMessage and ResultMessage correctly preserve
fields from the raw CLI JSON output that were previously dropped during parsing.
See issue #562.
"""

from typing import Any

import pytest

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_assistant_message_preserves_fields():
    """Test that AssistantMessage preserves id, usage, stop_reason, session_id, uuid."""

    options = ClaudeAgentOptions(
        model="claude-sonnet-4-5",
        max_turns=1,
    )

    collected_messages: list[Any] = []

    async with ClaudeSDKClient(options) as client:
        await client.query("Say hi")

        async for message in client.receive_response():
            collected_messages.append(message)

    # Find AssistantMessage
    assistant_messages = [
        msg for msg in collected_messages if isinstance(msg, AssistantMessage)
    ]
    assert len(assistant_messages) >= 1, "No AssistantMessage received"

    msg = assistant_messages[0]

    # model should be a real model string (not empty or synthetic)
    assert msg.model is not None
    assert len(msg.model) > 0
    assert "claude" in msg.model, f"Unexpected model: {msg.model}"

    # id should be an Anthropic message ID
    assert msg.id is not None, "AssistantMessage.id should not be None"
    assert msg.id.startswith("msg_"), f"Unexpected message id format: {msg.id}"

    # usage should contain token counts
    assert msg.usage is not None, "AssistantMessage.usage should not be None"
    assert "input_tokens" in msg.usage, "usage missing input_tokens"
    assert "output_tokens" in msg.usage, "usage missing output_tokens"
    assert msg.usage["input_tokens"] >= 0
    assert msg.usage["output_tokens"] >= 0

    # session_id should be present
    assert msg.session_id is not None, "AssistantMessage.session_id should not be None"
    assert len(msg.session_id) > 0

    # uuid should be present
    assert msg.uuid is not None, "AssistantMessage.uuid should not be None"
    assert len(msg.uuid) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_result_message_preserves_model_usage():
    """Test that ResultMessage preserves modelUsage, permission_denials, and uuid."""

    options = ClaudeAgentOptions(
        model="claude-sonnet-4-5",
        max_turns=1,
    )

    collected_messages: list[Any] = []

    async with ClaudeSDKClient(options) as client:
        await client.query("Say hi")

        async for message in client.receive_response():
            collected_messages.append(message)

    # Find ResultMessage
    result_messages = [
        msg for msg in collected_messages if isinstance(msg, ResultMessage)
    ]
    assert len(result_messages) == 1, "Expected exactly one ResultMessage"

    result = result_messages[0]

    # model_usage should contain per-model breakdown
    assert result.model_usage is not None, (
        "ResultMessage.model_usage should not be None"
    )
    assert len(result.model_usage) >= 1, "model_usage should have at least one model"

    # The model key should be a real model identifier
    model_names = list(result.model_usage.keys())
    assert any(
        "claude" in name for name in model_names
    ), f"No claude model in model_usage keys: {model_names}"

    # Each model entry should have token and cost fields
    for model_name, model_data in result.model_usage.items():
        assert "inputTokens" in model_data, f"{model_name} missing inputTokens"
        assert "outputTokens" in model_data, f"{model_name} missing outputTokens"
        assert "costUSD" in model_data, f"{model_name} missing costUSD"

    # total_cost_usd should match modelUsage costUSD sum
    if result.total_cost_usd is not None:
        model_cost_sum = sum(
            data.get("costUSD", 0) for data in result.model_usage.values()
        )
        assert abs(result.total_cost_usd - model_cost_sum) < 0.0001, (
            f"total_cost_usd ({result.total_cost_usd}) doesn't match "
            f"modelUsage costUSD sum ({model_cost_sum})"
        )

    # permission_denials should be present (empty list for simple queries)
    assert result.permission_denials is not None, (
        "ResultMessage.permission_denials should not be None"
    )
    assert isinstance(result.permission_denials, list)

    # uuid should be present
    assert result.uuid is not None, "ResultMessage.uuid should not be None"
    assert len(result.uuid) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_streaming_mode_preserves_fields():
    """Test that fields are preserved in streaming mode with include_partial_messages."""

    options = ClaudeAgentOptions(
        include_partial_messages=True,
        model="claude-sonnet-4-5",
        max_turns=1,
    )

    collected_messages: list[Any] = []

    async with ClaudeSDKClient(options) as client:
        await client.query("Say hi")

        async for message in client.receive_response():
            collected_messages.append(message)

    # AssistantMessage fields should still be preserved in streaming mode
    assistant_messages = [
        msg for msg in collected_messages if isinstance(msg, AssistantMessage)
    ]
    assert len(assistant_messages) >= 1, "No AssistantMessage received"

    msg = assistant_messages[0]
    assert msg.id is not None, "AssistantMessage.id missing in streaming mode"
    assert msg.usage is not None, "AssistantMessage.usage missing in streaming mode"
    assert msg.session_id is not None, (
        "AssistantMessage.session_id missing in streaming mode"
    )

    # ResultMessage model_usage should also be preserved
    result_messages = [
        msg for msg in collected_messages if isinstance(msg, ResultMessage)
    ]
    assert len(result_messages) == 1
    assert result_messages[0].model_usage is not None, (
        "ResultMessage.model_usage missing in streaming mode"
    )
    assert result_messages[0].uuid is not None, (
        "ResultMessage.uuid missing in streaming mode"
    )
