"""
Simple test to debug spawn issues.
"""

import asyncio
import sys
import traceback
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from mcps.UnixDomainSocketAgentController import UDSAgentController


async def main():
    print("Testing spawn with multiprocessing...")

    try:
        controller = UDSAgentController()
        print("Controller created")

        print("Spawning agent...")
        agent = await controller.spawn("test_agent")

        print("Success! Agent spawned:")
        print(f"  - Agent ID: {agent.agent_id}")
        print(f"  - PID: {agent.pid}")
        print(f"  - Socket: {agent.socket_path}")

        # Clean up
        await controller.kill(agent.agent_id)
        print("Agent killed successfully")

    except Exception as e:
        print(f"Error: {e}")
        print("\nFull traceback:")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
