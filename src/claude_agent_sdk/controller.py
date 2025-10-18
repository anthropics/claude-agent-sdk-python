"""
UDS Agent Controller implementation.

Provides a unified interface to manage multiple UDS agent processes.
"""

import asyncio
import json
import multiprocessing
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk.interfaces import AGENT_TYPE


class AgentCard:
    """
    Metadata card describing an available agent type.

    This provides information to clients about what agents can be spawned
    and what they're designed to do.

    Each AgentCard is responsible for building its own ClaudeAgent instance
    with appropriate configuration.
    """
    def __init__(self, agent_type: str, description: str):
        """
        Initialize AgentCard.

        Args:
            agent_type: Type identifier (e.g., "creator_agent", "browser_agent")
            description: What this agent does
        """
        self.agent_type = agent_type
        self.description = description

    def build_agent(self, base_dir: Path):
        """
        Build a ClaudeAgent instance based on agent_type.

        This method can be customized per agent_type to provide
        different configurations, MCP servers, tools, etc.

        Args:
            base_dir: Base directory for agent workspace

        Returns:
            Configured ClaudeAgent instance
        """
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.agent import create_claude_agent
        from claude_agent_sdk.interfaces import AgentOptions

        # Branch based on agent_type for specialized configurations
        if self.agent_type == "creator_agent":
            # Creator agent specific configuration
            # In the future, this could include specialized MCP servers, tools, etc.
            return create_claude_agent(
                AgentOptions(
                    agent_name=self.agent_type,
                    base_options=ClaudeAgentOptions(cwd=base_dir),
                    log_protocol=None  # Will use default
                )
            )
        elif self.agent_type == "browser_agent":
            # Browser agent specific configuration
            return create_claude_agent(
                AgentOptions(
                    agent_name=self.agent_type,
                    base_options=ClaudeAgentOptions(cwd=base_dir),
                    log_protocol=None
                )
            )
        elif self.agent_type == "leader_agent":
            # Leader agent specific configuration
            return create_claude_agent(
                AgentOptions(
                    agent_name=self.agent_type,
                    base_options=ClaudeAgentOptions(cwd=base_dir),
                    log_protocol=None
                )
            )
        else:
            # Default configuration for unknown agent types
            return create_claude_agent(
                AgentOptions(
                    agent_name=self.agent_type,
                    base_options=ClaudeAgentOptions(cwd=base_dir),
                    log_protocol=None
                )
            )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "agent_type": self.agent_type,
            "description": self.description
        }


def _run_agent_process(
    agent_type: str,
    description: str,
    base_dir: str,
    initial_query: str | None,
    ready_queue: multiprocessing.Queue
) -> None:
    """
    Entry point for agent subprocess.
    This function runs in a separate process.

    Args:
        agent_type: Type of agent to spawn
        description: Agent description
        base_dir: Base directory for socket files
        initial_query: Optional initial query to execute
        ready_queue: Queue to send server info back to parent
    """
    # Add project root to sys.path
    project_root = Path(__file__).parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Import here to avoid issues with multiprocessing
    import traceback

    from clients.uds_agent_process_io import UDSAgentProcessIO

    async def _start():
        try:
            # Reconstruct AgentCard
            agent_card = AgentCard(agent_type=agent_type, description=description)

            # Build agent using AgentCard.build_agent()
            agent = agent_card.build_agent(base_dir=project_root)

            # Create UDSAgentProcessIO
            io = UDSAgentProcessIO(
                agent=agent,
                base_dir=base_dir
            )

            # Get server info and send to parent
            data = io.get_server_info()
            ready_queue.put(data)

            # Start the server (this will keep running)
            await io.start(initial_query=initial_query)
        except Exception as e:
            # Send error back to parent
            ready_queue.put({"error": str(e), "traceback": traceback.format_exc()})
            raise

    # Run the async event loop
    asyncio.run(_start())


@dataclass
class AgentHandle:
    """Handle for a running agent instance."""
    agent_id: str
    pid: int
    socket_path: Path
    log_file: Path
    started_at: str
    agent_type: str


class UDSAgentController:
    """
    Controller for managing multiple UDS agent processes.

    Provides a simple, unified interface to:
    - Start/stop agent processes
    - Send queries to agents
    - Monitor agent status and logs
    - Orchestrate multiple agents
    """

    def __init__(self, base_dir: str = "/tmp", agent_cards: list[AgentCard] | None = None):
        """
        Initialize the UDS Agent Controller.

        Args:
            base_dir: Base directory for socket files
            agent_cards: Optional list of AgentCard objects describing available agents
        """
        self.base_dir = Path(base_dir)
        self.agents: dict[str, AgentHandle] = {}
        self.agent_cards: dict[str, AgentCard] = {}  # agent_type -> AgentCard
        self._lock = asyncio.Lock()

        # Register provided agent cards
        if agent_cards:
            for card in agent_cards:
                self.register_agent_card(card)

    # === Process Management ===

    async def spawn(
        self,
        agent_type: AGENT_TYPE,
        initial_query: str | None = None
    ) -> AgentHandle:
        """
        Spawn a new agent process using multiprocessing.

        Args:
            agent_type: Type of agent ("creator_agent", "browser_agent", etc.)
            initial_query: Optional initial query to execute

        Returns:
            AgentHandle for the spawned agent

        Raises:
            ValueError: If agent_type is not registered in agent_cards
        """
        # Get agent card for this type
        agent_card = self.agent_cards.get(agent_type)
        if not agent_card:
            raise ValueError(
                f"Unknown agent type: {agent_type}. "
                f"Please register an AgentCard for this type first. "
                f"Available types: {list(self.agent_cards.keys())}"
            )

        # Create a queue for receiving server_info from child process
        ready_queue = multiprocessing.Queue()

        # Create and start the process
        process = multiprocessing.Process(
            target=_run_agent_process,
            args=(
                agent_card.agent_type,
                agent_card.description,
                str(self.base_dir),
                initial_query,
                ready_queue
            ),
            daemon=False
        )
        process.start()

        # Read server_info from queue with timeout
        try:
            # Use asyncio to wait for queue data with timeout
            loop = asyncio.get_event_loop()
            server_info = await asyncio.wait_for(
                loop.run_in_executor(None, ready_queue.get),
                timeout=30.0
            )

            # Check if it's an error
            if isinstance(server_info, dict) and "error" in server_info:
                error_msg = server_info.get("error", "Unknown error")
                traceback_msg = server_info.get("traceback", "")
                raise RuntimeError(f"Agent process failed: {error_msg}\n{traceback_msg}")

        except asyncio.TimeoutError:
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
            raise RuntimeError(f"Timeout waiting for agent {agent_type} to start")
        except Exception as e:
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
            raise RuntimeError(f"Failed to spawn agent: {e}")

        # Create agent handle
        handle = AgentHandle(
            agent_id=server_info["agent_id"],
            pid=server_info["pid"],
            socket_path=Path(server_info["socket_path"]),
            log_file=Path(server_info["log_file"]),
            started_at=server_info["started_at"],
            agent_type=agent_type
        )

        # Register agent
        async with self._lock:
            self.agents[handle.agent_id] = handle

        # Wait a bit to ensure socket is ready
        await asyncio.sleep(0.5)

        return handle

    async def kill(self, agent_id: str) -> bool:
        """
        Gracefully stop an agent process.

        Args:
            agent_id: ID of the agent to stop

        Returns:
            True if successful
        """
        async with self._lock:
            agent = self.agents.get(agent_id)
            if not agent:
                return False

        try:
            # Try graceful shutdown via socket
            await self._send_command(agent.socket_path, {
                "id": f"stop-{agent_id}",
                "cmd": "stop"
            })

            # Wait for process to exit
            await asyncio.sleep(1.0)

            # Check if process is still running
            try:
                os.kill(agent.pid, 0)  # Check if process exists
                # Still running, force kill
                os.kill(agent.pid, signal.SIGTERM)
                await asyncio.sleep(0.5)
            except ProcessLookupError:
                pass  # Already dead

            async with self._lock:
                del self.agents[agent_id]

            return True

        except Exception:
            # Force kill if graceful shutdown fails
            try:
                os.kill(agent.pid, signal.SIGKILL)
                async with self._lock:
                    del self.agents[agent_id]
                return True
            except ProcessLookupError:
                async with self._lock:
                    del self.agents[agent_id]
                return True

    async def kill_all(self) -> None:
        """Stop all managed agent processes."""
        agent_ids = list(self.agents.keys())
        await asyncio.gather(
            *[self.kill(agent_id) for agent_id in agent_ids],
            return_exceptions=True
        )

    # === Communication ===

    async def query(
        self,
        agent_id: str,
        prompt: str,
        wait_for_completion: bool = True
    ) -> dict:
        """
        Send a query to an agent.

        Args:
            agent_id: Target agent ID
            prompt: Query prompt
            wait_for_completion: Wait for response if True

        Returns:
            Response from agent
        """
        agent = self.agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        request = {
            "id": f"query-{agent_id}-{asyncio.get_event_loop().time()}",
            "cmd": "query",
            "prompt": prompt
        }

        response = await self._send_command(agent.socket_path, request)

        if wait_for_completion:
            return response
        else:
            return {"ok": True, "message": "Query sent, not waiting for completion"}

    async def query_many(
        self,
        queries: list[tuple[str, str]]  # [(agent_id, prompt), ...]
    ) -> list[dict]:
        """
        Send queries to multiple agents in parallel.

        Args:
            queries: List of (agent_id, prompt) tuples

        Returns:
            List of responses
        """
        tasks = [
            self.query(agent_id, prompt, wait_for_completion=True)
            for agent_id, prompt in queries
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    # === Observation ===

    async def status(self, agent_id: str) -> dict:
        """
        Get current status of an agent.

        Returns:
            Status information including uptime, session_id, etc.
        """
        agent = self.agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        request = {
            "id": f"status-{agent_id}",
            "cmd": "status"
        }

        return await self._send_command(agent.socket_path, request)

    async def status_all(self) -> dict[str, dict]:
        """Get status of all managed agents."""
        results = {}
        for agent_id in self.agents.keys():
            try:
                results[agent_id] = await self.status(agent_id)
            except Exception as e:
                results[agent_id] = {"error": str(e)}
        return results

    async def tail_log(
        self,
        agent_id: str,
        lines: int = 10
    ) -> AsyncIterator[dict]:
        """
        Stream log entries from an agent.

        Args:
            agent_id: Target agent ID
            lines: Number of recent lines to start from

        Yields:
            Log entry dictionaries
        """
        agent = self.agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        if not agent.log_file.exists():
            return

        # Read last N lines
        proc = await asyncio.create_subprocess_exec(
            "tail", "-f", "-n", str(lines), str(agent.log_file),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

                try:
                    log_entry = json.loads(line.decode('utf-8').strip())
                    yield log_entry
                except json.JSONDecodeError:
                    continue
        finally:
            proc.kill()
            await proc.wait()

    # === Discovery ===

    def list_agents(self) -> list[AgentHandle]:
        """List all managed agent handles."""
        return list(self.agents.values())

    def get_agent(self, agent_id: str) -> AgentHandle | None:
        """Get handle for specific agent."""
        return self.agents.get(agent_id)

    # === Agent Card Management ===

    def register_agent_card(self, card: AgentCard) -> None:
        """
        Register an agent card describing an available agent type.

        Args:
            card: AgentCard with agent_type and description
        """
        self.agent_cards[card.agent_type] = card

    def list_available_agents(self) -> list[AgentCard]:
        """
        List all available agent types that can be spawned.

        Returns:
            List of AgentCard objects describing available agents
        """
        return list(self.agent_cards.values())

    def get_agent_card(self, agent_type: str) -> AgentCard | None:
        """
        Get the card for a specific agent type.

        Args:
            agent_type: Type of agent to look up

        Returns:
            AgentCard if found, None otherwise
        """
        return self.agent_cards.get(agent_type)

    # === Context Manager Support ===

    async def __aenter__(self):
        """Enable async context manager usage."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup all agents on exit."""
        await self.kill_all()

    # === Internal Helpers ===

    async def _send_command(self, socket_path: Path, request: dict) -> dict:
        """
        Send a command to a UDS socket and get response.

        Args:
            socket_path: Path to the Unix socket
            request: Command request as dictionary

        Returns:
            Response dictionary
        """
        if not socket_path.exists():
            raise RuntimeError(f"Socket {socket_path} does not exist")

        reader, writer = await asyncio.open_unix_connection(str(socket_path))

        try:
            # Send request
            request_bytes = (json.dumps(request) + '\n').encode('utf-8')
            writer.write(request_bytes)
            await writer.drain()

            # Read response
            response_line = await asyncio.wait_for(reader.readline(), timeout=60.0)
            response = json.loads(response_line.decode('utf-8').strip())

            return response

        finally:
            writer.close()
            await writer.wait_closed()
