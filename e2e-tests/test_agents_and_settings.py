"""End-to-end tests for agents and setting sources with real Claude API calls."""

import asyncio
import json
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


def generate_large_agents(
    num_agents: int = 20, prompt_size_kb: int = 12
) -> dict[str, AgentDefinition]:
    """Generate multiple agents with large prompts for testing.

    Args:
        num_agents: Number of agents to generate
        prompt_size_kb: Size of each agent's prompt in KB

    Returns:
        Dictionary of agent name -> AgentDefinition
    """
    agents = {}
    for i in range(num_agents):
        # Generate a large prompt with some structure
        prompt_content = f"You are test agent #{i}. " + ("x" * (prompt_size_kb * 1024))
        agents[f"large-agent-{i}"] = AgentDefinition(
            description=f"Large test agent #{i} for stress testing",
            prompt=prompt_content,
        )
    return agents


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_agent_definition():
    """Test that custom agent definitions work in streaming mode."""
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
                assert isinstance(agents, list), (
                    f"agents should be a list of strings, got: {type(agents)}"
                )
                assert "test-agent" in agents, (
                    f"test-agent should be available, got: {agents}"
                )
                break


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_agent_definition_with_query_function():
    """Test that custom agent definitions work with the query() function.

    Both ClaudeSDKClient and query() now use streaming mode internally,
    sending agents via the initialize request.
    """
    from claude_agent_sdk import query

    options = ClaudeAgentOptions(
        agents={
            "test-agent-query": AgentDefinition(
                description="A test agent for query function verification",
                prompt="You are a test agent.",
            )
        },
        max_turns=1,
    )

    # Use query() with string prompt
    found_agent = False
    async for message in query(prompt="What is 2 + 2?", options=options):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            agents = message.data.get("agents", [])
            assert "test-agent-query" in agents, (
                f"test-agent-query should be available, got: {agents}"
            )
            found_agent = True
            break

    assert found_agent, "Should have received init message with agents"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_large_agents_with_query_function():
    """Test large agent definitions (260KB+) work with query() function.

    Since we now always use streaming mode internally (matching TypeScript SDK),
    large agents are sent via the initialize request through stdin with no
    size limits.
    """
    from claude_agent_sdk import query

    # Generate 20 agents with 13KB prompts each = ~260KB total
    agents = generate_large_agents(num_agents=20, prompt_size_kb=13)

    options = ClaudeAgentOptions(
        agents=agents,
        max_turns=1,
    )

    # Use query() with string prompt - agents still go via initialize
    found_agents = []
    async for message in query(prompt="What is 2 + 2?", options=options):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            found_agents = message.data.get("agents", [])
            break

    # Check all our agents are registered
    for agent_name in agents:
        assert agent_name in found_agents, (
            f"{agent_name} should be registered. "
            f"Found: {found_agents[:5]}... ({len(found_agents)} total)"
        )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_filesystem_agent_loading():
    """Test that filesystem-based agents load via setting_sources and produce full response.

    This is the core test for issue #406. It verifies that when using
    setting_sources=["project"] with a .claude/agents/ directory containing
    agent definitions, the SDK:
    1. Loads the agents (they appear in init message)
    2. Produces a full response with AssistantMessage
    3. Completes with a ResultMessage

    The bug in #406 causes the iterator to complete after only the
    init SystemMessage, never yielding AssistantMessage or ResultMessage.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a temporary project with a filesystem agent
        project_dir = Path(tmpdir)
        agents_dir = project_dir / ".claude" / "agents"
        agents_dir.mkdir(parents=True)

        # Create a test agent file
        agent_file = agents_dir / "fs-test-agent.md"
        agent_file.write_text(
            """---
name: fs-test-agent
description: A filesystem test agent for SDK testing
tools: Read
---

# Filesystem Test Agent

You are a simple test agent. When asked a question, provide a brief, helpful answer.
"""
        )

        options = ClaudeAgentOptions(
            setting_sources=["project"],
            cwd=project_dir,
            max_turns=1,
        )

        messages = []
        async with ClaudeSDKClient(options=options) as client:
            await client.query("Say hello in exactly 3 words")
            async for msg in client.receive_response():
                messages.append(msg)

        # Must have at least init, assistant, result
        message_types = [type(m).__name__ for m in messages]

        assert "SystemMessage" in message_types, "Missing SystemMessage (init)"
        assert "AssistantMessage" in message_types, (
            f"Missing AssistantMessage - got only: {message_types}. "
            "This may indicate issue #406 (silent failure with filesystem agents)."
        )
        assert "ResultMessage" in message_types, "Missing ResultMessage"

        # Find the init message and check for the filesystem agent
        for msg in messages:
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                agents = msg.data.get("agents", [])
                # Agents are returned as strings (just names)
                assert "fs-test-agent" in agents, (
                    f"fs-test-agent not loaded from filesystem. Found: {agents}"
                )
                break

        # On Windows, wait for file handles to be released before cleanup
        if sys.platform == "win32":
            await asyncio.sleep(0.5)


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
                    assert output_style != "local-test-style", (
                        f"outputStyle should NOT be from local settings (default is no settings), got: {output_style}"
                    )
                    assert output_style == "default", (
                        f"outputStyle should be 'default', got: {output_style}"
                    )
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
                    assert "testcmd" not in commands, (
                        f"testcmd should NOT be available with user-only sources, got: {commands}"
                    )
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
                    assert output_style == "local-test-style", (
                        f"outputStyle should be from local settings, got: {output_style}"
                    )
                    break

        # On Windows, wait for file handles to be released before cleanup
        if sys.platform == "win32":
            await asyncio.sleep(0.5)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_large_agent_definitions_via_initialize():
    """Test that large agent definitions (250KB+) are sent via initialize request.

    This test verifies the fix for the issue where large agent definitions
    would previously trigger a temp file workaround with @filepath. Now they
    are sent via the initialize control request through stdin, which has no
    size limit.

    The test:
    1. Generates 20 agents with ~13KB prompts each (~260KB total)
    2. Creates an SDK client with these agents
    3. Verifies all agents are registered and available
    """
    from dataclasses import asdict

    # Generate 20 agents with 13KB prompts each = ~260KB total
    agents = generate_large_agents(num_agents=20, prompt_size_kb=13)

    # Calculate total size to verify we're testing the right thing
    total_size = sum(
        len(json.dumps({k: v for k, v in asdict(agent).items() if v is not None}))
        for agent in agents.values()
    )
    assert total_size > 250_000, (
        f"Test agents should be >250KB, got {total_size / 1024:.1f}KB"
    )

    options = ClaudeAgentOptions(
        agents=agents,
        max_turns=1,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("List available agents")

        # Check that all agents are available in init message
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                registered_agents = message.data.get("agents", [])
                assert isinstance(registered_agents, list), (
                    f"agents should be a list, got: {type(registered_agents)}"
                )

                # Verify all our agents are registered
                for agent_name in agents:
                    assert agent_name in registered_agents, (
                        f"{agent_name} should be registered. "
                        f"Found: {registered_agents[:5]}... ({len(registered_agents)} total)"
                    )

                # All agents should be there
                assert len(registered_agents) >= len(agents), (
                    f"Expected at least {len(agents)} agents, got {len(registered_agents)}"
                )
                break
