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
    """Test that can_use_tool callback gets invoked for file operations."""
    callback_invocations: list[tuple[str, dict]] = []

    # Use a unique file path to avoid conflicts with existing files
    unique_id = uuid.uuid4().hex[:8]
    test_file = f"/tmp/sdk_permission_test_{unique_id}.txt"

    # Ensure the file doesn't exist before the test
    test_path = Path(test_file)
    if test_path.exists():
        test_path.unlink()

    async def permission_callback(
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Track callback invocation."""
        print(f"Permission callback called for: {tool_name}, input: {input_data}")
        callback_invocations.append((tool_name, input_data))
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        can_use_tool=permission_callback,
    )

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                f"Create a new file at {test_file} with the content 'hello world'. "
                "Do not read the file first, just create it."
            )

            async for message in client.receive_response():
                print(f"Got message: {message}")

        print(f"Callback invocations: {[name for name, _ in callback_invocations]}")

        # Verify the permission callback was invoked for Write
        tool_names = [name for name, _ in callback_invocations]
        assert "Write" in tool_names, (
            f"can_use_tool callback should have been invoked for Write tool, got: {tool_names}"
        )

    finally:
        # Clean up
        if test_path.exists():
            test_path.unlink()
