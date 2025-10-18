import json
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk.types import (
    AssistantMessage,
    HookEvent,
    HookMatcher,
    Message,
    ResultMessage,
    TextBlock,
    UserMessage,
)

# ========== Abstract Base Class ==========

class LogProtocol(ABC):
    """Abstract base class for logging protocols.

    Controls both message logging and hooks generation.
    All concrete implementations must provide:
    - init(): Initialize with log file path
    - log_message(): Handle Message logging
    - 7 hook functions: pre_tool_use_hook, post_tool_use_hook, etc.

    get_hooks() is implemented in the base class using the 7 hook functions.
    """

    @abstractmethod
    def init(self, log_file_path: Path | str | None = None) -> None:
        """Initialize logging with optional log file path.

        Args:
            log_file_path: Path to log file. If None, implementation creates default path.
        """
        pass

    @abstractmethod
    async def log_message(self, message: Message) -> None:
        """Log a Claude SDK message.

        Args:
            message: Message to log (UserMessage, AssistantMessage, ResultMessage, etc.)
        """
        pass

    # ========== Hook Functions (Abstract) ==========

    @abstractmethod
    async def pre_tool_use_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any  # HookContext
    ) -> dict[str, Any]:  # HookJSONOutput
        """Hook callback for PreToolUse event.

        Args:
            input_data: Tool use data (tool_name, tool_input, etc.)
            tool_use_id: Optional tool use ID
            context: Hook context

        Returns:
            Hook JSON output (empty dict if no modifications)
        """
        pass

    @abstractmethod
    async def post_tool_use_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any  # HookContext
    ) -> dict[str, Any]:  # HookJSONOutput
        """Hook callback for PostToolUse event."""
        pass

    @abstractmethod
    async def user_prompt_submit_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any  # HookContext
    ) -> dict[str, Any]:  # HookJSONOutput
        """Hook callback for UserPromptSubmit event."""
        pass

    @abstractmethod
    async def stop_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any  # HookContext
    ) -> dict[str, Any]:  # HookJSONOutput
        """Hook callback for Stop event."""
        pass

    @abstractmethod
    async def subagent_stop_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any  # HookContext
    ) -> dict[str, Any]:  # HookJSONOutput
        """Hook callback for SubagentStop event."""
        pass

    @abstractmethod
    async def pre_compact_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any  # HookContext
    ) -> dict[str, Any]:  # HookJSONOutput
        """Hook callback for PreCompact event."""
        pass

    @abstractmethod
    async def post_compact_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any  # HookContext
    ) -> dict[str, Any]:  # HookJSONOutput
        """Hook callback for PostCompact event."""
        pass

    # ========== Hooks Configuration (Concrete Implementation) ==========

    def get_hooks(self) -> dict[HookEvent, list[HookMatcher]]:
        """Generate hooks configuration for SDK.

        Uses the 7 abstract hook functions to build the configuration.
        This method is concrete and implemented in the base class.

        Returns:
            Hook configuration dict for ClaudeAgentOptions
        """
        return {
            "PreToolUse": [HookMatcher(matcher=None, hooks=[self.pre_tool_use_hook])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[self.post_tool_use_hook])],
            "UserPromptSubmit": [HookMatcher(matcher=None, hooks=[self.user_prompt_submit_hook])],
            "Stop": [HookMatcher(matcher=None, hooks=[self.stop_hook])],
            "SubagentStop": [HookMatcher(matcher=None, hooks=[self.subagent_stop_hook])],
            "PreCompact": [HookMatcher(matcher=None, hooks=[self.pre_compact_hook])],
            "PostCompact": [HookMatcher(matcher=None, hooks=[self.post_compact_hook])]
        }


# ========== Type Definitions ==========

# Log Type: Primary classification for client-side filtering
# This is the MAIN category that Electron clients use to organize logs
# DO NOT change these values without updating the Electron client
LogType = Literal["message", "hook", "system"]

# ========== Base Log Structures ==========

@dataclass
class BaseLog:
    """Base log structure with common fields.
    
    log_type: Primary classification (message/hook/system) - used by clients for filtering
    socket_path, agent_id, pid, log_file: Agent context information
    """
    log_type: LogType  # Primary classification - DO NOT use for UI styling
    socket_path: str
    agent_id: str
    pid: int
    log_file: str

@dataclass
class LogMessage(BaseLog):
    subtype: Literal["user", "assistant", "system", "result"]
    content: str
    # Optional metadata for result messages
    result_metadata: dict[str, Any] | None = None

@dataclass
class LogHook(BaseLog):
    subtype: Literal["PreToolUse","PostToolUse"]
    tool_name: Literal["Bash","Read","MCP","DeployNewAgent"]
    content: dict[str, Any]


@dataclass
class LogSystem(BaseLog):
    event: str
    data: dict[str, Any]

# ========== DefaultLogProtocol Implementation ==========

class DefaultLogProtocol(LogProtocol):
    """Unified logging protocol handling both stdout and file logging.

    Combines functionality from:
    - Original LogProtocol (stdout logging)
    - DefaultAgentLogger (file logging, hooks generation)

    Features:
    - Logs messages to both stdout (JSON format) and file (JSONL format)
    - Generates hooks for tool use tracking
    - Maintains turn counter for structured logging
    """

    def __init__(self, agent_id: str):
        """Initialize with agent_id. Call init() to set log file path.

        Args:
            agent_id: Unique agent identifier
        """
        self.agent_id = agent_id
        self.log_file_path: Path | None = None
        self.socket_path: str | None = None
        self.pid: int | None = None
        self.turn = 0

    def init(self, log_file_path: Path | str | None = None) -> None:
        """Initialize logging with optional log file path.

        Args:
            log_file_path: Path to log file. If None, creates default path in logs/ directory.
                          Can also accept dict with connection_info for backward compatibility.
        """
        # Handle dict input (backward compatibility with UDS protocol)
        if isinstance(log_file_path, dict):
            connection_info = log_file_path
            log_file = connection_info.get("log_file")
            self.log_file_path = Path(log_file) if log_file else self._create_default_log_path()
            self.socket_path = connection_info.get("socket_path")
            self.pid = connection_info.get("pid")
        elif log_file_path is None:
            self.log_file_path = self._create_default_log_path()
        else:
            self.log_file_path = Path(log_file_path)

        # Ensure parent directory exists
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)

    def _create_default_log_path(self) -> Path:
        """Create default log file path in logs/ directory.

        Returns:
            Path to log file: logs/{agent_id}.jsonl
        """
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{self.agent_id}.jsonl"

    # ========== File Logging Helper ==========

    def _write_to_file(self, log_type: str, subtype: str, data: dict[str, Any]) -> None:
        """Write log entry to JSONL file.

        Args:
            log_type: "message" or "hook"
            subtype: Specific type (user, assistant, PreToolUse, etc.)
            data: Event data
        """
        if self.log_file_path is None:
            return

        entry = {
            "turn": self.turn,
            "type": log_type,
            "subtype": subtype,
            "data": data
        }
        with open(self.log_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ========== Stdout Logging Helper ==========

    async def _log_to_stdout(self, log: LogMessage | LogHook | LogSystem) -> None:
        """Write log entry to stdout as JSON with timestamp.

        Args:
            log: Log entry (LogMessage, LogHook, or LogSystem)
        """
        # Convert dataclass to dict
        log_dict = asdict(log)
        # Add timestamp
        log_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
        # Output as JSON
        sys.stdout.write(json.dumps(log_dict, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    # ========== Message Logging ==========

    async def log_message(self, message: Message) -> None:
        """Log Claude SDK message to both stdout and file.

        Args:
            message: Message to log (UserMessage, AssistantMessage, ResultMessage, etc.)
        """
        log_type = "message"
        subtype = None
        content = ""
        result_metadata = None

        # Extract message content
        if isinstance(message, UserMessage):
            subtype = "user"
            for block in message.content:
                if isinstance(block, TextBlock):
                    content += block.text
        elif isinstance(message, AssistantMessage):
            subtype = "assistant"
            for block in message.content:
                if isinstance(block, TextBlock):
                    content += block.text
        elif isinstance(message, ResultMessage):
            subtype = "result"
            content += message.result or ""
            result_metadata = {
                "cost": message.total_cost_usd,
                "duration_ms": message.duration_ms,
                "duration_api_ms": message.duration_api_ms,
                "num_turns": message.num_turns,
                "session_id": message.session_id,
                "is_error": message.is_error,
                "stop_reason": message.subtype
            }

        if subtype and content:
            # 1. Write to file
            self._write_to_file("message", subtype, {
                "content": content,
                "result_metadata": result_metadata
            })

            # 2. Write to stdout
            log_entry = LogMessage(
                log_type=log_type,
                socket_path=self.socket_path or "",
                agent_id=self.agent_id,
                pid=self.pid or 0,
                log_file=str(self.log_file_path) if self.log_file_path else "",
                subtype=subtype,
                content=content,
                result_metadata=result_metadata
            )
            await self._log_to_stdout(log_entry)

    # ========== System Logging ==========

    async def log_system(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Log system events to both stdout and file.

        Args:
            event: Event name (e.g., 'server_stopping', 'client_connected')
            data: Optional additional data
        """
        # 1. Write to file
        self._write_to_file("system", event, data or {})

        # 2. Write to stdout
        log_entry = LogSystem(
            log_type="system",
            socket_path=self.socket_path or "",
            agent_id=self.agent_id,
            pid=self.pid or 0,
            log_file=str(self.log_file_path) if self.log_file_path else "",
            event=event,
            data=data or {}
        )
        await self._log_to_stdout(log_entry)

    # ========== Hook Logging Helper Methods ==========

    def _write_hook_to_file(self, event_type: str, data: dict[str, Any]) -> None:
        """Write hook event to file.

        Args:
            event_type: Hook event type (PreToolUse, PostToolUse, etc.)
            data: Hook data
        """
        self.turn += 1
        self._write_to_file("hook", event_type, data)

    async def log_pre_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str | None = None
    ) -> None:
        """Log PreToolUse hook to both stdout and file."""
        # 1. Write to file
        self._write_hook_to_file("PreToolUse", {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "tool_input": tool_input
        })

        # 2. Write to stdout (only for specific tool types)
        content = None
        normalized_tool_name = None

        if tool_name == 'Bash' and 'command' in tool_input:
            content = {
                'command': tool_input.get('command'),
                'description': tool_input.get('description', '')
            }
            normalized_tool_name = 'Bash'
        elif tool_name == 'Read' and 'file_path' in tool_input:
            content = {
                'file_path': tool_input.get('file_path')
            }
            normalized_tool_name = 'Read'
        elif tool_name.startswith('mcp__'):
            content = {
                'mcp_name': tool_name,
                'mcp_input': tool_input
            }
            normalized_tool_name = 'MCP'

        if content and normalized_tool_name:
            log_entry = LogHook(
                log_type="hook",
                socket_path=self.socket_path or "",
                agent_id=self.agent_id,
                pid=self.pid or 0,
                log_file=str(self.log_file_path) if self.log_file_path else "",
                subtype="PreToolUse",
                tool_name=normalized_tool_name,
                content=content
            )
            await self._log_to_stdout(log_entry)

    async def log_post_tool_use(
        self,
        tool_name: str,
        tool_response: Any,
        tool_use_id: str | None = None,
        is_error: bool = False
    ) -> None:
        """Log PostToolUse hook to both stdout and file."""
        # 1. Write to file
        self._write_hook_to_file("PostToolUse", {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "is_error": is_error,
            "tool_response": tool_response
        })

        # 2. Stdout logging is minimal for PostToolUse (can be extended if needed)

    # ========== Hook Functions Implementation ==========

    async def pre_tool_use_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Hook callback for PreToolUse event."""
        tool_name = input_data.get('tool_name', 'Unknown')
        tool_input = input_data.get('tool_input', {})
        await self.log_pre_tool_use(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=tool_use_id
        )
        return {}

    async def post_tool_use_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Hook callback for PostToolUse event."""
        tool_name = input_data.get('tool_name', 'Unknown')
        tool_response = input_data.get('tool_response')
        is_error = input_data.get('is_error', False)
        await self.log_post_tool_use(
            tool_name=tool_name,
            tool_response=tool_response,
            tool_use_id=tool_use_id,
            is_error=is_error
        )
        return {}

    async def user_prompt_submit_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Hook callback for UserPromptSubmit event."""
        self._write_hook_to_file("UserPromptSubmit", {
            "prompt": input_data.get("prompt"),
            "parent_tool_use_id": input_data.get("parentToolUseId")
        })
        return {}

    async def stop_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Hook callback for Stop event."""
        self._write_hook_to_file("Stop", {
            "stop_reason": input_data.get("stopReason"),
            "num_turns": input_data.get("numTurns"),
            "duration_ms": input_data.get("durationMs"),
            "total_cost_usd": input_data.get("totalCostUsd"),
            "is_error": input_data.get("isError", False)
        })
        return {}

    async def subagent_stop_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Hook callback for SubagentStop event."""
        self._write_hook_to_file("SubagentStop", {
            "subagent_tool_use_id": input_data.get("subagentToolUseId"),
            "stop_reason": input_data.get("stopReason"),
            "num_turns": input_data.get("numTurns"),
            "is_error": input_data.get("isError", False)
        })
        return {}

    async def pre_compact_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Hook callback for PreCompact event."""
        self._write_hook_to_file("PreCompact", {
            "message_count": input_data.get("messageCount"),
            "reason": input_data.get("reason")
        })
        return {}

    async def post_compact_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Hook callback for PostCompact event."""
        self._write_hook_to_file("PostCompact", {
            "original_count": input_data.get("originalCount"),
            "compacted_count": input_data.get("compactedCount"),
            "tokens_saved": input_data.get("tokensSaved")
        })
        return {}
