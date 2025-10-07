"""End-to-end tests for dynamic control features with real Claude API calls."""

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_set_permission_mode():
    """Test that permission mode can be changed dynamically during a session."""

    options = ClaudeAgentOptions(
        permission_mode="default",
    )

    async with ClaudeSDKClient(options=options) as client:
        # Change permission mode to acceptEdits
        await client.set_permission_mode("acceptEdits")

        # Make a query that would normally require permission
        await client.query("What is 2+2? Just respond with the number.")

        async for message in client.receive_response():
            print(f"Got message: {message}")
            pass  # Just consume messages

        # Change back to default
        await client.set_permission_mode("default")

        # Make another query
        await client.query("What is 3+3? Just respond with the number.")

        async for message in client.receive_response():
            print(f"Got message: {message}")
            pass  # Just consume messages


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_set_model():
    """Test that model can be changed dynamically during a session."""

    options = ClaudeAgentOptions()

    async with ClaudeSDKClient(options=options) as client:
        # Start with default model
        await client.query("What is 1+1? Just the number.")

        async for message in client.receive_response():
            print(f"Default model response: {message}")
            pass

        # Switch to Haiku model
        await client.set_model("claude-3-5-haiku-20241022")

        await client.query("What is 2+2? Just the number.")

        async for message in client.receive_response():
            print(f"Haiku model response: {message}")
            pass

        # Switch back to default (None means default)
        await client.set_model(None)

        await client.query("What is 3+3? Just the number.")

        async for message in client.receive_response():
            print(f"Back to default model: {message}")
            pass


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_interrupt():
    """Test that interrupt can be sent during a session."""

    options = ClaudeAgentOptions()

    async with ClaudeSDKClient(options=options) as client:
        # Start a query
        await client.query("Count from 1 to 100 slowly.")

        # Send interrupt (may or may not stop the response depending on timing)
        try:
            await client.interrupt()
            print("Interrupt sent successfully")
        except Exception as e:
            print(f"Interrupt resulted in: {e}")

        # Consume any remaining messages
        async for message in client.receive_response():
            print(f"Got message after interrupt: {message}")
            pass
