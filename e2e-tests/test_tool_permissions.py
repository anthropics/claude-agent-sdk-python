"""End-to-end tests for tool permission callbacks with real Claude API calls."""

import uuid
from pathlib import Path

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_permission_callback_gets_called():
    """Test that can_use_tool callback gets invoked for non-read-only commands.

    Note: The CLI auto-allows certain read-only commands (like 'echo') without
    consulting the SDK callback. We use 'touch' which requires permission.
    """
    callback_invocations: list[tuple[str, dict]] = []

    # Use a unique file path to avoid conflicts
    unique_id = uuid.uuid4().hex[:8]
    test_file = f"/tmp/sdk_permission_test_{unique_id}.txt"
    test_path = Path(test_file)

    async def permission_callback(
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Track callback invocation and allow all operations."""
        print(f"Permission callback called for: {tool_name}, input: {input_data}")
        callback_invocations.append((tool_name, input_data))
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        can_use_tool=permission_callback,
    )

    try:
        async with ClaudeSDKClient(options=options) as client:
            # Use 'touch' command which is NOT auto-allowed (not read-only)
            await client.query(f"Run the command: touch {test_file}")

            async for message in client.receive_response():
                print(f"Got message: {message}")

        print(f"Callback invocations: {[name for name, _ in callback_invocations]}")

        # Verify the callback was invoked for Bash
        tool_names = [name for name, _ in callback_invocations]
        assert "Bash" in tool_names, (
            f"Permission callback should have been invoked for Bash, got: {tool_names}"
        )

    finally:
        # Clean up
        if test_path.exists():
            test_path.unlink()
