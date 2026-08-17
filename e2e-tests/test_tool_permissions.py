"""End-to-end tests for tool permission callbacks with real Claude API calls."""

import uuid
from pathlib import Path

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolPermissionContext,
    query,
)


@pytest.mark.e2e
@pytest.mark.anyio
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


async def _run_query_with_only_can_use_tool(prompt_factory):
    """Drive one-shot query() with can_use_tool as the *only* control-protocol
    consumer (no hooks, no SDK MCP servers) and return the callback log."""
    callback_invocations: list[str] = []
    unique_id = uuid.uuid4().hex[:8]
    # Outside cwd and /tmp so no sandbox or allow-rule can auto-approve it: the
    # Write must round-trip through the permission callback.
    test_path = Path.home() / f".agent_sdk_permission_e2e_{unique_id}.txt"

    async def permission_callback(
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        callback_invocations.append(tool_name)
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        can_use_tool=permission_callback,
        disallowed_tools=["Bash"],
        max_turns=3,
    )
    prompt_text = (
        f"Use the Write tool to create the file {test_path} containing exactly "
        "the text hello. Do not use any other tool."
    )

    try:
        result = None
        async for message in query(prompt=prompt_factory(prompt_text), options=options):
            if isinstance(message, ResultMessage):
                result = message
        assert result is not None, "query() should yield a ResultMessage"
        assert not result.is_error, f"run should succeed, got: {result}"
        assert "Write" in callback_invocations, (
            "can_use_tool should have been invoked for Write, "
            f"got: {callback_invocations}"
        )
        assert test_path.exists(), "the permitted Write should have run"
    finally:
        if test_path.exists():
            test_path.unlink()


@pytest.mark.e2e
@pytest.mark.anyio
async def test_query_async_iterable_prompt_with_only_can_use_tool():
    """query() with a finite AsyncIterable prompt and only can_use_tool must
    keep stdin open long enough for the permission verdict to reach the CLI.

    Regression: stdin used to close as soon as the prompt iterable finished
    unless hooks or SDK MCP servers were also configured, so the CLI's
    permission request failed with "Stream closed" and the callback never ran.
    """

    def make_stream(text: str):
        async def stream():
            yield {
                "type": "user",
                "message": {"role": "user", "content": text},
                "parent_tool_use_id": None,
                "session_id": "",
            }

        return stream()

    await _run_query_with_only_can_use_tool(make_stream)


@pytest.mark.e2e
@pytest.mark.anyio
async def test_query_string_prompt_with_can_use_tool():
    """query() accepts a plain string prompt together with can_use_tool."""
    await _run_query_with_only_can_use_tool(lambda text: text)
