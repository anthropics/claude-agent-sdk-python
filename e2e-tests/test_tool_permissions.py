"""End-to-end tests for tool permission callbacks with real Claude API calls."""

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
    """Test that can_use_tool callback gets invoked."""
    callback_invocations = []
    
    async def permission_callback(
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Track callback invocation."""
        print(f"Permission callback called for: {tool_name}, input: {input_data}")
        callback_invocations.append(tool_name)
        return PermissionResultAllow()
    
    options = ClaudeAgentOptions(
        can_use_tool=permission_callback,
    )
    
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Write 'hello world' to /tmp/test.txt")
        
        async for message in client.receive_response():
            print(f"Got message: {message}")
            pass  # Just consume messages
    
    print(f'Callback invocations: {callback_invocations}')
    # Verify callback was invoked
    assert "Write" in callback_invocations, f"can_use_tool callback should have been invoked for Write tool, got: {callback_invocations}"