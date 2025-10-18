from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    create_sdk_mcp_server,
    query,
)
from claude_agent_sdk.interfaces import AgentOptions, IAgent, MCPParams, SDKMcpParams
from claude_agent_sdk.log_protocol import DefaultLogProtocol, LogProtocol
from claude_agent_sdk.types import (
    Message,
    ResultMessage,
)
from claude_agent_sdk.utils import generate_agent_id, get_session_id, merge_options

default_options = ClaudeAgentOptions(
    allowed_tools=["Glob", "Grep", "Read", "WebFetch", "TodoWrite", "WebSearch", "BashOutput", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool", "Edit", "Write", "NotebookEdit", "Bash"],
    permission_mode="default"
)


def create_claude_agent(options: AgentOptions) -> "ClaudeAgent":
    """Factory function to create a ClaudeAgent instance.

    Handles all initialization logic including:
    - agent_id generation
    - log_protocol initialization
    - MCP servers configuration
    - hooks setup
    - ClaudeAgentOptions construction

    Args:
        options: AgentOptions containing all configuration and dependencies

    Returns:
        Configured ClaudeAgent instance
    """
    # 1. Generate agent_id
    agent_id = generate_agent_id(options.agent_name)

    # 2. Initialize log_protocol
    if options.log_protocol is None:
        # Determine log file path
        base_cwd = options.base_options.cwd if options.base_options else Path.cwd()
        log_dir = base_cwd / "logs"
        log_file = log_dir / f"{agent_id}.jsonl"

        # Create default log protocol
        log_protocol = DefaultLogProtocol(agent_id=agent_id)
        log_protocol.init(log_file_path=log_file)
    else:
        log_protocol = options.log_protocol

    # 3. Build MCP servers from available_mcps_servers list
    mcp_servers = {}
    all_tool_names = []

    available_mcps_servers = options.available_mcps_servers or []
    for mcp_param in available_mcps_servers:
        if isinstance(mcp_param, SDKMcpParams):
            # SDK MCP server
            server = create_sdk_mcp_server(
                name=mcp_param.name,
                tools=mcp_param.tools
            )
            mcp_servers[mcp_param.name] = server
            all_tool_names.extend(mcp_param.available_tool_names)
        elif isinstance(mcp_param, MCPParams):
            # Stdio MCP server
            mcp_servers[mcp_param.name] = mcp_param.mcp_config
            all_tool_names.extend(mcp_param.available_tool_names)

    # 4. Get hooks from log_protocol
    hooks = log_protocol.get_hooks()

    # 5. Build final ClaudeAgentOptions
    patch_options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        allowed_tools=all_tool_names + ["Glob", "Grep", "Read", "WebFetch", "TodoWrite", "WebSearch", "BashOutput", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool", "Edit", "Write", "NotebookEdit", "Bash"],
        hooks=hooks
    )
    final_options = merge_options(options.base_options or default_options, patch_options)

    # 6. Create and return ClaudeAgent
    return ClaudeAgent(
        options=final_options,
        agent_id=agent_id,
        log_protocol=log_protocol
    )


class ClaudeAgent:
    def __init__(
        self,
        options: ClaudeAgentOptions,
        agent_id: str,
        log_protocol: LogProtocol
    ):
        """Initialize ClaudeAgent with finalized options.

        Args:
            options: Fully configured ClaudeAgentOptions
            agent_id: Unique agent identifier
            log_protocol: Initialized LogProtocol instance
        """
        self.options = options
        self.agent_id = agent_id
        self.log_protocol = log_protocol
        self.session_id = None
        if options.hooks is None and log_protocol is not None:
            self.options.hooks = log_protocol.get_hooks()
        self.client = ClaudeSDKClient(self.options)

    async def query(self, prompt: str|AsyncIterable[dict[str, Any]])->str:
        if self.session_id is None:
            await self.client.connect()
            await self.client.query(prompt)
            async for message in self.client.receive_response():
                session_id = get_session_id(message)
                if session_id is not None:
                    self.session_id = session_id
                    break
        else:
            await self.client.query(prompt, session_id=self.session_id)
        return self.session_id

    async def interrupt(self):
        await self.client.interrupt()

    async def receive(self,is_monitor:bool=False,is_sub_agent:bool=False) -> AsyncIterator[Message]:
        if self.session_id is None:
            await self.client.connect()
        async for message in self.client.receive_messages() if is_monitor else self.client.receive_response():
            await self.log_protocol.log_message(message)
            yield message

    async def deepthinking(self, prompt: str)->str:
        deepthinking_options = ClaudeAgentOptions(
            hooks=self.log_protocol.get_hooks(),
            mcp_servers=self.options.mcp_servers,
            allowed_tools=self.options.allowed_tools,
            model="claude-sonnet-4-5",
            max_turns=20,
            resume=self.session_id if self.session_id else None,
            continue_conversation=True if self.session_id else False,
            env={
                "MAX_THINKING_TOKENS": "9000",
            }
        )
        merged_options = merge_options(self.client.options, deepthinking_options)
        result = ""
        async for message in query(prompt=prompt, options=merged_options):
            await self.log_protocol.log_message(message)
            if isinstance(message, ResultMessage):
                result = message.result
        return result


DEFAULT_PATH = Path(__file__).parent.parent.parent

def default_agent_factory(agent_type: str, log_protocol: "LogProtocol | None" = None) -> ClaudeAgent:
    """Create a ClaudeAgent with default configuration.

    Args:
        agent_type: Name/type of the agent
        log_protocol: Optional LogProtocol instance for logging

    Returns:
        Configured ClaudeAgent instance
    """
    return create_claude_agent(
        AgentOptions(
            agent_name=agent_type,
            base_options=ClaudeAgentOptions(cwd=DEFAULT_PATH),
            log_protocol=log_protocol
        )
    )


class ClaudeAgentForUDSIO(IAgent):
    def __init__(self, agent: ClaudeAgent):
        self.agent = agent
        self._log_file_path = agent.log_protocol.log_file_path
        self._agent_id = agent.agent_id

    def log_file(self) -> Path:
        return self._log_file_path

    def agent_id(self) -> str:
        return self._agent_id

    async def init(self) -> None:
        await self.query("@Claude.md read claude.md of your directory")

    async def query(self, prompt: str|AsyncIterable[dict[str, Any]]):
        await self.agent.query(prompt)

    async def monitor(self)->AsyncIterator[Message]:
        async for message in self.agent.receive(is_monitor=True,is_sub_agent=True):
            yield message
            if isinstance(message, ResultMessage):
                print("ResultMessage",message)

    def status(self) -> str:
        return "read src/clients/claude_agent_client.py and understand how it works"

    async def interrupt(self, prompt: str) -> AsyncIterator[Message]:
        await self.agent.interrupt(prompt)
        await self.agent.query(prompt)
        async for message in self.agent.receive():
            yield message


def default_agent_factory_for_uds_io(agent_type: str, log_protocol: LogProtocol | None = None) -> ClaudeAgentForUDSIO:
    """Create a ClaudeAgentForUDSIO with default configuration.

    Args:
        agent_type: Name/type of the agent
        log_protocol: Optional LogProtocol instance for logging

    Returns:
        Configured ClaudeAgentForUDSIO instance
    """
    agent = default_agent_factory(agent_type, log_protocol)
    return ClaudeAgentForUDSIO(agent)
