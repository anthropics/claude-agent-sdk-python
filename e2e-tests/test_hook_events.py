"""End-to-end tests for hook event types with real Claude API calls."""

from typing import Any

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pre_tool_use_hook_with_additional_context():
    """Test PreToolUse hook returning additionalContext field end-to-end."""
    hook_invocations: list[dict[str, Any]] = []

    async def pre_tool_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """PreToolUse hook that provides additionalContext."""
        tool_name = input_data.get("tool_name", "")
        hook_invocations.append(
            {"tool_name": tool_name, "tool_use_id": input_data.get("tool_use_id")}
        )

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Approved with context",
                "additionalContext": "This command is running in a test environment",
            },
        }

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[pre_tool_hook]),
            ],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Run: echo 'test additional context'")

        async for message in client.receive_response():
            print(f"Got message: {message}")

    print(f"Hook invocations: {hook_invocations}")
    assert len(hook_invocations) > 0, "PreToolUse hook should have been invoked"
    # Verify tool_use_id is present in the input (new field)
    assert hook_invocations[0]["tool_use_id"] is not None, (
        "tool_use_id should be present in PreToolUse input"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_post_tool_use_hook_with_tool_use_id():
    """Test PostToolUse hook receives tool_use_id field end-to-end."""
    hook_invocations: list[dict[str, Any]] = []

    async def post_tool_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """PostToolUse hook that verifies tool_use_id is present."""
        tool_name = input_data.get("tool_name", "")
        hook_invocations.append(
            {
                "tool_name": tool_name,
                "tool_use_id": input_data.get("tool_use_id"),
            }
        )

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "Post-tool monitoring active",
            },
        }

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        hooks={
            "PostToolUse": [
                HookMatcher(matcher="Bash", hooks=[post_tool_hook]),
            ],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Run: echo 'test tool_use_id'")

        async for message in client.receive_response():
            print(f"Got message: {message}")

    print(f"Hook invocations: {hook_invocations}")
    assert len(hook_invocations) > 0, "PostToolUse hook should have been invoked"
    # Verify tool_use_id is present in the input (new field)
    assert hook_invocations[0]["tool_use_id"] is not None, (
        "tool_use_id should be present in PostToolUse input"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_notification_hook():
    """Test Notification hook fires end-to-end."""
    hook_invocations: list[dict[str, Any]] = []

    async def notification_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """Notification hook that tracks invocations."""
        hook_invocations.append(
            {
                "hook_event_name": input_data.get("hook_event_name"),
                "message": input_data.get("message"),
                "notification_type": input_data.get("notification_type"),
            }
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "Notification",
                "additionalContext": "Notification received",
            },
        }

    options = ClaudeAgentOptions(
        hooks={
            "Notification": [
                HookMatcher(hooks=[notification_hook]),
            ],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Say hello in one word.")

        async for message in client.receive_response():
            print(f"Got message: {message}")

    print(f"Notification hook invocations: {hook_invocations}")
    # Notification hooks may or may not fire depending on CLI behavior.
    # This test verifies the hook registration doesn't cause errors.
    # If it fires, verify the shape is correct.
    for invocation in hook_invocations:
        assert invocation["hook_event_name"] == "Notification"
        assert invocation["notification_type"] is not None


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_multiple_hooks_together():
    """Test registering multiple hook event types together end-to-end."""
    all_invocations: list[dict[str, Any]] = []

    async def track_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """Generic hook that tracks all invocations."""
        all_invocations.append(
            {
                "hook_event_name": input_data.get("hook_event_name"),
            }
        )
        return {}

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        hooks={
            "Notification": [HookMatcher(hooks=[track_hook])],
            "PreToolUse": [HookMatcher(matcher="Bash", hooks=[track_hook])],
            "PostToolUse": [HookMatcher(matcher="Bash", hooks=[track_hook])],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Run: echo 'multi-hook test'")

        async for message in client.receive_response():
            print(f"Got message: {message}")

    print(f"All hook invocations: {all_invocations}")
    event_names = [inv["hook_event_name"] for inv in all_invocations]

    # At minimum, PreToolUse and PostToolUse should fire for the Bash command
    assert "PreToolUse" in event_names, "PreToolUse hook should have fired"
    assert "PostToolUse" in event_names, "PostToolUse hook should have fired"
