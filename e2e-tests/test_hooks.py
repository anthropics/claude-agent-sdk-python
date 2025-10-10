"""End-to-end tests for hook callbacks with real Claude API calls."""

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
async def test_hook_with_permission_decision_and_reason():
    """Test that hooks with permissionDecision and reason fields work end-to-end."""
    hook_invocations = []

    async def test_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """Hook that uses permissionDecision and reason fields."""
        tool_name = input_data.get("tool_name", "")
        print(f"Hook called for tool: {tool_name}")
        hook_invocations.append(tool_name)

        # Block Bash commands for this test
        if tool_name == "Bash":
            return {
                "reason": "Bash commands are blocked in this test for safety",
                "systemMessage": "⚠️ Command blocked by hook",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Security policy: Bash blocked",
                },
            }

        return {
            "reason": "Tool approved by security review",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Tool passed security checks",
            },
        }

    options = ClaudeAgentOptions(
        allowed_tools=["Bash", "Write"],
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[test_hook]),
            ],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Run this bash command: echo 'hello'")

        async for message in client.receive_response():
            print(f"Got message: {message}")

    print(f"Hook invocations: {hook_invocations}")
    # Verify hook was called
    assert "Bash" in hook_invocations, f"Hook should have been invoked for Bash tool, got: {hook_invocations}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_hook_with_continue_and_stop_reason():
    """Test that hooks with continue_=False and stopReason fields work end-to-end."""
    hook_invocations = []

    async def post_tool_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """PostToolUse hook that stops execution with stopReason."""
        tool_name = input_data.get("tool_name", "")
        hook_invocations.append(tool_name)

        # Actually test continue_=False and stopReason fields
        return {
            "continue_": False,
            "stopReason": "Execution halted by test hook for validation",
            "reason": "Testing continue and stopReason fields",
            "systemMessage": "🛑 Test hook stopped execution",
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
        await client.query("Run: echo 'test message'")

        async for message in client.receive_response():
            print(f"Got message: {message}")

    print(f"Hook invocations: {hook_invocations}")
    # Verify hook was called
    assert "Bash" in hook_invocations, f"PostToolUse hook should have been invoked, got: {hook_invocations}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_hook_with_additional_context():
    """Test that hooks with hookSpecificOutput work end-to-end."""
    hook_invocations = []

    async def context_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        """Hook that provides additional context."""
        hook_invocations.append("context_added")

        return {
            "systemMessage": "Additional context provided by hook",
            "reason": "Hook providing monitoring feedback",
            "suppressOutput": False,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "The command executed successfully with hook monitoring",
            },
        }

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        hooks={
            "PostToolUse": [
                HookMatcher(matcher="Bash", hooks=[context_hook]),
            ],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Run: echo 'testing hooks'")

        async for message in client.receive_response():
            print(f"Got message: {message}")

    print(f"Hook invocations: {hook_invocations}")
    # Verify hook was called
    assert "context_added" in hook_invocations, "Hook with hookSpecificOutput should have been invoked"
