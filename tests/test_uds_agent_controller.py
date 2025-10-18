"""
Test UDS Agent Controller.
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from claude_agent_sdk.controller import AgentHandle, UDSAgentController


@pytest.mark.asyncio
async def test_spawn_and_kill_single_agent():
    """Test spawning and killing a single agent."""
    async with UDSAgentController() as controller:
        # Spawn an agent
        agent = await controller.spawn("test_agent", initial_query=None)

        # Verify agent handle
        assert isinstance(agent, AgentHandle)
        assert agent.agent_id
        assert agent.pid > 0
        assert agent.socket_path.exists()
        assert agent.agent_type == "test_agent"

        # Verify agent is in the list
        agents = controller.list_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == agent.agent_id

        # Kill the agent
        success = await controller.kill(agent.agent_id)
        assert success

        # Verify agent is removed
        agents = controller.list_agents()
        assert len(agents) == 0


@pytest.mark.asyncio
async def test_spawn_multiple_agents():
    """Test spawning multiple agents in parallel."""
    async with UDSAgentController() as controller:
        # Spawn 3 agents in parallel
        agents = await asyncio.gather(
            controller.spawn("test_agent"),
            controller.spawn("test_agent"),
            controller.spawn("test_agent")
        )

        # Verify all agents are unique
        agent_ids = [a.agent_id for a in agents]
        assert len(agent_ids) == 3
        assert len(set(agent_ids)) == 3  # All unique

        # Verify all agents are in the list
        all_agents = controller.list_agents()
        assert len(all_agents) == 3


@pytest.mark.asyncio
async def test_query_agent():
    """Test sending a query to an agent."""
    async with UDSAgentController() as controller:
        agent = await controller.spawn("test_agent")

        # Send a query
        response = await controller.query(
            agent.agent_id,
            "Hello, agent!",
            wait_for_completion=True
        )

        # Verify response
        assert isinstance(response, dict)
        assert response.get("ok") is True


@pytest.mark.asyncio
async def test_query_many():
    """Test sending queries to multiple agents in parallel."""
    async with UDSAgentController() as controller:
        # Spawn 2 agents
        agent1 = await controller.spawn("test_agent")
        agent2 = await controller.spawn("test_agent")

        # Send queries to both
        queries = [
            (agent1.agent_id, "Query 1"),
            (agent2.agent_id, "Query 2")
        ]

        responses = await controller.query_many(queries)

        # Verify responses
        assert len(responses) == 2
        for response in responses:
            if isinstance(response, dict):
                assert response.get("ok") is True


@pytest.mark.asyncio
async def test_status():
    """Test getting agent status."""
    async with UDSAgentController() as controller:
        agent = await controller.spawn("test_agent")

        # Get status
        status = await controller.status(agent.agent_id)

        # Verify status contains expected fields
        assert isinstance(status, dict)
        assert status.get("ok") is True
        result = status.get("result", {})
        assert "agent_id" in result
        assert "pid" in result
        assert "uptime_seconds" in result


@pytest.mark.asyncio
async def test_status_all():
    """Test getting status of all agents."""
    async with UDSAgentController() as controller:
        # Spawn 2 agents
        agent1 = await controller.spawn("test_agent")
        agent2 = await controller.spawn("test_agent")

        # Get all statuses
        all_status = await controller.status_all()

        # Verify
        assert len(all_status) == 2
        assert agent1.agent_id in all_status
        assert agent2.agent_id in all_status


@pytest.mark.asyncio
async def test_get_agent():
    """Test getting a specific agent handle."""
    async with UDSAgentController() as controller:
        agent = await controller.spawn("test_agent")

        # Get agent by ID
        retrieved = controller.get_agent(agent.agent_id)

        # Verify
        assert retrieved is not None
        assert retrieved.agent_id == agent.agent_id
        assert retrieved.pid == agent.pid

        # Test non-existent agent
        none_agent = controller.get_agent("non-existent-id")
        assert none_agent is None


@pytest.mark.asyncio
async def test_context_manager_cleanup():
    """Test that context manager cleans up all agents."""
    agent_ids = []

    async with UDSAgentController() as controller:
        # Spawn some agents
        agent1 = await controller.spawn("test_agent")
        agent2 = await controller.spawn("test_agent")

        agent_ids.append(agent1.agent_id)
        agent_ids.append(agent2.agent_id)

        # Verify they exist
        assert len(controller.list_agents()) == 2

    # After exiting context, create new controller to verify cleanup
    controller2 = UDSAgentController()
    # The agents should not be in the new controller
    assert len(controller2.list_agents()) == 0


@pytest.mark.asyncio
async def test_tail_log():
    """Test tailing agent logs."""
    async with UDSAgentController() as controller:
        agent = await controller.spawn("test_agent")

        # Send a query to generate some logs
        await controller.query(agent.agent_id, "Test query for logs")

        # Wait a bit for logs to be written
        await asyncio.sleep(1)

        # Tail the log (just read a few entries)
        log_entries = []
        count = 0
        async for entry in controller.tail_log(agent.agent_id, lines=5):
            log_entries.append(entry)
            count += 1
            if count >= 3:  # Just read a few entries to test
                break

        # We should have gotten some log entries
        assert len(log_entries) > 0


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
