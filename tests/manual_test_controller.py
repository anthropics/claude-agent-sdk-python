"""
Manual test script for UDS Agent Controller.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from mcps.UnixDomainSocketAgentController import UDSAgentController


async def test_basic_operations():
    """Test basic controller operations."""
    print("=" * 60)
    print("Starting UDS Agent Controller Tests")
    print("=" * 60)

    async with UDSAgentController() as controller:
        # Test 1: Spawn a single agent
        print("\n[Test 1] Spawning a single agent...")
        try:
            agent = await controller.spawn("test_agent")
            print("✓ Agent spawned successfully!")
            print(f"  - Agent ID: {agent.agent_id}")
            print(f"  - PID: {agent.pid}")
            print(f"  - Socket: {agent.socket_path}")
            print(f"  - Log: {agent.log_file}")
        except Exception as e:
            print(f"✗ Failed to spawn agent: {e}")
            return

        # Test 2: List agents
        print("\n[Test 2] Listing agents...")
        agents = controller.list_agents()
        print(f"✓ Found {len(agents)} agent(s)")

        # Test 3: Get agent status
        print("\n[Test 3] Getting agent status...")
        try:
            status = await controller.status(agent.agent_id)
            print("✓ Status retrieved:")
            print(f"  - OK: {status.get('ok')}")
            if status.get("ok"):
                result = status.get("result", {})
                print(f"  - Uptime: {result.get('uptime_seconds', 0):.2f} seconds")
        except Exception as e:
            print(f"✗ Failed to get status: {e}")

        # Test 4: Send a simple query
        print("\n[Test 4] Sending a query to the agent...")
        try:
            response = await controller.query(
                agent.agent_id,
                "Say hello!",
                wait_for_completion=True
            )
            print("✓ Query sent successfully!")
            print(f"  - Response: {response}")
        except Exception as e:
            print(f"✗ Failed to send query: {e}")

        # Test 5: Kill the agent
        print("\n[Test 5] Killing the agent...")
        try:
            success = await controller.kill(agent.agent_id)
            print(f"✓ Agent killed: {success}")

            # Verify agent is gone
            agents = controller.list_agents()
            print(f"  - Remaining agents: {len(agents)}")
        except Exception as e:
            print(f"✗ Failed to kill agent: {e}")

    print("\n" + "=" * 60)
    print("Tests completed!")
    print("=" * 60)


async def test_multiple_agents():
    """Test spawning and managing multiple agents."""
    print("\n" + "=" * 60)
    print("Testing Multiple Agents")
    print("=" * 60)

    async with UDSAgentController() as controller:
        print("\n[Test 6] Spawning 3 agents in parallel...")
        try:
            agents = await asyncio.gather(
                controller.spawn("test_agent"),
                controller.spawn("test_agent"),
                controller.spawn("test_agent")
            )
            print(f"✓ Spawned {len(agents)} agents successfully!")
            for i, agent in enumerate(agents, 1):
                print(f"  - Agent {i}: {agent.agent_id} (PID: {agent.pid})")
        except Exception as e:
            print(f"✗ Failed to spawn multiple agents: {e}")
            return

        # Test 7: Get status of all agents
        print("\n[Test 7] Getting status of all agents...")
        try:
            all_status = await controller.status_all()
            print(f"✓ Retrieved status for {len(all_status)} agents")
            for agent_id, status in all_status.items():
                if isinstance(status, dict) and status.get("ok"):
                    result = status.get("result", {})
                    uptime = result.get("uptime_seconds", 0)
                    print(f"  - {agent_id}: uptime={uptime:.2f}s")
        except Exception as e:
            print(f"✗ Failed to get all statuses: {e}")

        # Test 8: Send queries to multiple agents
        print("\n[Test 8] Sending queries to all agents in parallel...")
        try:
            queries = [
                (agent.agent_id, f"Task {i+1}")
                for i, agent in enumerate(agents)
            ]
            responses = await controller.query_many(queries)
            success_count = sum(1 for r in responses if isinstance(r, dict) and r.get("ok"))
            print(f"✓ Sent {len(queries)} queries, {success_count} succeeded")
        except Exception as e:
            print(f"✗ Failed to send queries: {e}")

    print("\n" + "=" * 60)
    print("Multiple agent tests completed!")
    print("=" * 60)


async def main():
    """Run all tests."""
    try:
        await test_basic_operations()
        await asyncio.sleep(1)
        await test_multiple_agents()
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user.")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
