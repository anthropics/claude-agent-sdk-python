"""
UDS Agent Controller Demo - showcasing practical usage.

Updated to demonstrate AgentCard.build_agent() pattern.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from claude_agent_sdk.controller import AgentCard, UDSAgentController


async def agent_card_demo():
    """Demonstrate AgentCard usage and registration."""
    print("=" * 70)
    print("DEMO 0: AgentCard Registration and Usage")
    print("=" * 70)

    # Create agent cards
    creator_card = AgentCard(
        agent_type="creator_agent",
        description="Creates web content, designs, and visual assets"
    )

    browser_card = AgentCard(
        agent_type="browser_agent",
        description="Browses web, scrapes data, and monitors sites"
    )

    leader_card = AgentCard(
        agent_type="leader_agent",
        description="Orchestrates and coordinates multiple agents"
    )

    # Initialize controller with cards
    print("\n1. Initializing controller with 3 agent cards...")
    controller = UDSAgentController(
        base_dir="/tmp",
        agent_cards=[creator_card, browser_card, leader_card]
    )

    # List available agent types
    print("\n2. Available agent types:")
    for card in controller.list_available_agents():
        print(f"   - {card.agent_type}: {card.description}")

    # Show agent card to_dict() format
    print("\n3. AgentCard serialization:")
    print(f"   {creator_card.to_dict()}")

    # Demonstrate dynamic registration
    print("\n4. Dynamically registering a new agent type...")
    controller.register_agent_card(
        AgentCard("researcher_agent", "Researches topics and gathers information")
    )

    print(f"\n5. Now have {len(controller.agent_cards)} registered agent types")

    # Demonstrate validation
    print("\n6. Testing spawn validation (should fail for unregistered type)...")
    try:
        await controller.spawn("unknown_agent")
    except ValueError as e:
        print(f"   ✓ Correctly rejected: {str(e)[:50]}...")

    print("\n7. AgentCard demo complete!")


async def simple_usage_demo():
    """Simple usage example - single agent."""
    print("=" * 70)
    print("DEMO 1: Simple Usage - Single Agent")
    print("=" * 70)

    # Register agent card first
    test_card = AgentCard("test_agent", "General purpose test agent")

    async with UDSAgentController(agent_cards=[test_card]) as controller:
        # Spawn an agent
        print("\n1. Spawning a test agent...")
        agent = await controller.spawn("test_agent")
        print(f"   Agent ID: {agent.agent_id}")

        # Send a query
        print("\n2. Sending a simple query...")
        response = await controller.query(agent.agent_id, "What's 2+2?")
        print(f"   Response: {response.get('ok')}")

        # Check status
        print("\n3. Checking agent status...")
        status = await controller.status(agent.agent_id)
        if status.get("ok"):
            result = status.get("result", {})
            print(f"   Uptime: {result.get('uptime_seconds', 0):.2f}s")

        print("\n4. Agent will be automatically cleaned up on exit...")


async def orchestration_demo():
    """Orchestration example - multiple agents working together."""
    print("\n" + "=" * 70)
    print("DEMO 2: Orchestration - Multiple Agents in Parallel")
    print("=" * 70)

    # Register agent card first
    test_card = AgentCard("test_agent", "General purpose test agent")

    async with UDSAgentController(agent_cards=[test_card]) as controller:
        # Spawn multiple agents
        print("\n1. Spawning 3 agents in parallel...")
        agents = await asyncio.gather(
            controller.spawn("test_agent"),
            controller.spawn("test_agent"),
            controller.spawn("test_agent")
        )
        print(f"   Spawned {len(agents)} agents")

        # Assign different tasks to each agent
        print("\n2. Assigning different tasks to each agent...")
        tasks = [
            "Calculate the sum of numbers 1 to 100",
            "List 5 programming languages",
            "Explain what is AI in one sentence"
        ]

        queries = [(agent.agent_id, task) for agent, task in zip(agents, tasks)]

        # Execute all queries in parallel
        print("\n3. Executing all tasks in parallel...")
        responses = await controller.query_many(queries)

        print(f"\n4. Results:")
        success_count = sum(1 for r in responses if isinstance(r, dict) and r.get("ok"))
        print(f"   {success_count}/{len(responses)} tasks completed successfully")

        # Get status of all agents
        print("\n5. Getting status of all agents...")
        all_status = await controller.status_all()
        for i, (agent_id, status) in enumerate(all_status.items(), 1):
            if isinstance(status, dict) and status.get("ok"):
                result = status.get("result", {})
                print(f"   Agent {i}: uptime={result.get('uptime_seconds', 0):.2f}s")


async def dynamic_scaling_demo():
    """Dynamic scaling - spawn agents on demand."""
    print("\n" + "=" * 70)
    print("DEMO 3: Dynamic Scaling - Spawn Agents on Demand")
    print("=" * 70)

    # Register agent card first
    test_card = AgentCard("test_agent", "General purpose test agent")

    async with UDSAgentController(agent_cards=[test_card]) as controller:
        print("\n1. Starting with no agents...")
        print(f"   Active agents: {len(controller.list_agents())}")

        # Simulate workload that requires scaling
        workload = ["Task A", "Task B", "Task C", "Task D", "Task E"]

        print(f"\n2. Received {len(workload)} tasks, scaling up...")

        # Spawn agents dynamically
        agent_handles = []
        for i, task in enumerate(workload, 1):
            print(f"   Spawning agent {i} for '{task}'...")
            agent = await controller.spawn("test_agent")
            agent_handles.append(agent)

        print(f"\n3. Now running {len(controller.list_agents())} agents")

        # Execute tasks
        print("\n4. Executing tasks...")
        queries = [(agent.agent_id, task) for agent, task in zip(agent_handles, workload)]
        responses = await controller.query_many(queries)

        success_count = sum(1 for r in responses if isinstance(r, dict) and r.get("ok"))
        print(f"   Completed: {success_count}/{len(workload)} tasks")

        # Scale down - kill specific agents
        print("\n5. Scaling down - killing 2 agents...")
        await controller.kill(agent_handles[0].agent_id)
        await controller.kill(agent_handles[1].agent_id)
        print(f"   Remaining agents: {len(controller.list_agents())}")


async def main():
    """Run all demos."""
    try:
        # New: AgentCard demo
        await agent_card_demo()
        await asyncio.sleep(1)

        await simple_usage_demo()
        await asyncio.sleep(1)

        await orchestration_demo()
        await asyncio.sleep(1)

        await dynamic_scaling_demo()

        print("\n" + "=" * 70)
        print("All demos completed successfully!")
        print("=" * 70)

    except KeyboardInterrupt:
        print("\n\nDemos interrupted by user.")
    except Exception as e:
        print(f"\n\nError during demo: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
