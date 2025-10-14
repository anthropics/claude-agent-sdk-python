"""End-to-end tests for agents and setting sources with real Claude API calls."""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    SystemMessage,
)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_agent_definition():
    """Test that custom agent definitions work."""
    options = ClaudeAgentOptions(
        agents={
            "test-agent": AgentDefinition(
                description="A test agent for verification",
                prompt="You are a test agent. Always respond with 'Test agent activated'",
                tools=["Read"],
                model="sonnet",
            )
        },
        max_turns=1,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("What is 2 + 2?")

        # Check that agent is available in init message
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                agents = message.data.get("agents", [])
                assert isinstance(
                    agents, list
                ), f"agents should be a list of strings, got: {type(agents)}"
                assert (
                    "test-agent" in agents
                ), f"test-agent should be available, got: {agents}"
                break


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_setting_sources_default():
    """Test that default (no setting_sources) loads no settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a temporary project with local settings
        project_dir = Path(tmpdir)
        claude_dir = project_dir / ".claude"
        claude_dir.mkdir(parents=True)

        # Create local settings with custom outputStyle
        settings_file = claude_dir / "settings.local.json"
        settings_file.write_text('{"outputStyle": "local-test-style"}')

        # Don't provide setting_sources - should default to no settings
        options = ClaudeAgentOptions(
            cwd=project_dir,
            max_turns=1,
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query("What is 2 + 2?")

            # Check that settings were NOT loaded
            async for message in client.receive_response():
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    output_style = message.data.get("output_style")
                    assert (
                        output_style != "local-test-style"
                    ), f"outputStyle should NOT be from local settings (default is no settings), got: {output_style}"
                    assert (
                        output_style == "default"
                    ), f"outputStyle should be 'default', got: {output_style}"
                    break

        # On Windows, wait for file handles to be released before cleanup
        if sys.platform == "win32":
            await asyncio.sleep(0.5)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_setting_sources_user_only():
    """Test that setting_sources=['user'] excludes project settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a temporary project with a slash command
        project_dir = Path(tmpdir)
        commands_dir = project_dir / ".claude" / "commands"
        commands_dir.mkdir(parents=True)

        test_command = commands_dir / "testcmd.md"
        test_command.write_text(
            """---
description: Test command
---

This is a test command.
"""
        )

        # Use setting_sources=["user"] to exclude project settings
        options = ClaudeAgentOptions(
            setting_sources=["user"],
            cwd=project_dir,
            max_turns=1,
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query("What is 2 + 2?")

            # Check that project command is NOT available
            async for message in client.receive_response():
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    commands = message.data.get("slash_commands", [])
                    assert (
                        "testcmd" not in commands
                    ), f"testcmd should NOT be available with user-only sources, got: {commands}"
                    break

        # On Windows, wait for file handles to be released before cleanup
        if sys.platform == "win32":
            await asyncio.sleep(0.5)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_setting_sources_project_included():
    """Test that setting_sources=['user', 'project'] includes project settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a temporary project with local settings
        project_dir = Path(tmpdir)
        claude_dir = project_dir / ".claude"
        claude_dir.mkdir(parents=True)

        # Create local settings with custom outputStyle
        settings_file = claude_dir / "settings.local.json"
        settings_file.write_text('{"outputStyle": "local-test-style"}')

        # Use setting_sources=["user", "project", "local"] to include local settings
        options = ClaudeAgentOptions(
            setting_sources=["user", "project", "local"],
            cwd=project_dir,
            max_turns=1,
        )

        async with ClaudeSDKClient(options=options) as client:
            await client.query("What is 2 + 2?")

            # Check that settings WERE loaded
            async for message in client.receive_response():
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    output_style = message.data.get("output_style")
                    assert (
                        output_style == "local-test-style"
                    ), f"outputStyle should be from local settings, got: {output_style}"
                    break

        # On Windows, wait for file handles to be released before cleanup
        if sys.platform == "win32":
            await asyncio.sleep(0.5)