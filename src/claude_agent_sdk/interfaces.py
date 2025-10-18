"""
Data classes and types for UDS Agent Process IO system.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from claude_agent_sdk import SdkMcpTool
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    McpStdioServerConfig,
)

if TYPE_CHECKING:
    from claude_agent_sdk.log_protocol import LogProtocol

AGENT_TYPE = Literal["creator_agent", "browser_agent", "leader_agent"]

@dataclass
class UDSConnectionInfo:
    agent_id: str
    pid: int
    socket_path: str
    log_file: str
    started_at: str

@dataclass
class SDKMcpParams:
    name: str
    available_tool_names: list[str]
    tools: list[SdkMcpTool]

@dataclass
class MCPParams:
    name: str
    available_tool_names: list[str]
    mcp_config: McpStdioServerConfig

AgentAvailableMCPServers = list[MCPParams|SDKMcpParams]

@dataclass
class AgentOptions:
    """
    Options for the agent.
    All configuration including dependencies are bundled here.
    """
    agent_name: str
    session_id: str | None = None
    base_options: ClaudeAgentOptions | None = None
    log_protocol: "LogProtocol | None" = None
    available_mcps_servers: AgentAvailableMCPServers | None = None

@dataclass
class SessionMessage:
    """
    Message for the session.
    """
    generated_session_id: str


class UDPMessage:
    udp_path: str
    agent_type: AGENT_TYPE
    pid: int



from abc import ABC, abstractmethod
from pathlib import Path


class IAgent(ABC):
    """Abstract interface for agents that process queries."""
    @abstractmethod
    def agent_id(self) -> str:
        """
        Get the agent ID.
        """
        pass

    @abstractmethod
    def log_file(self) -> Path:
        """
        Get the log directory.
        """
        pass

    @abstractmethod
    async def init(self) -> None:
        """
        Initialize the agent.
        """
        pass

    @abstractmethod
    async def query(self, prompt: str) :
        """
        Process a query and yield results as a stream.
        
        Args:
            prompt: The input prompt/query
            
        Yields:
            Dictionary messages representing the processing stream
        """
        pass

    @abstractmethod
    async def monitor(self):
        """
        Monitor the agent and yield results as a stream.
        """
        pass

    @abstractmethod
    def status(self) -> str:
        """
        Get current agent status.
        
        Returns:
            Dictionary containing status information
        """
        pass

    @abstractmethod
    async def interrupt(self, prompt: str):
        """
        Interrupt the agent and throw new prompt.

        Args:
            prompt: The input prompt/query

        Yields:
            Dictionary messages representing the processing stream
        """
        pass


class IUDSAgentProcessIO(ABC):
    """Abstract interface for UDS server with integrated logging."""

    @abstractmethod
    async def start(self, agent: IAgent) -> None:
        """
        Start the UDS server with the given agent.
        
        Args:
            agent: The agent instance to handle queries
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """
        Stop the UDS server.
        """
        pass

    @abstractmethod
    def get_server_info(self) -> dict:
        """
        Get server connection information for reconnection.
        
        Returns:
            Dictionary with server_id, pid, socket_path, log_file, etc.
        """
        pass
