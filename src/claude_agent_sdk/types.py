"""Type definitions for Claude SDK."""

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from typing_extensions import NotRequired

if TYPE_CHECKING:
    from mcp.server import Server as McpServer
else:
    # Runtime placeholder for forward reference resolution in Pydantic 2.12+
    McpServer = Any

# Permission modes
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]

# SDK Beta features - see https://docs.anthropic.com/en/api/beta-headers
SdkBeta = Literal["context-1m-2025-08-07"]

# Agent definitions
SettingSource = Literal["user", "project", "local"]


class SystemPromptPreset(TypedDict):
    """System prompt preset configuration."""

    type: Literal["preset"]
    preset: Literal["claude_code"]
    append: NotRequired[str]


class ToolsPreset(TypedDict):
    """Tools preset configuration."""

    type: Literal["preset"]
    preset: Literal["claude_code"]


@dataclass
class AgentDefinition:
    """Agent definition configuration."""

    description: str
    prompt: str
    tools: list[str] | None = None
    model: Literal["sonnet", "opus", "haiku", "inherit"] | None = None


# Permission Update types (matching TypeScript SDK)
PermissionUpdateDestination = Literal[
    "userSettings", "projectSettings", "localSettings", "session"
]

PermissionBehavior = Literal["allow", "deny", "ask"]


@dataclass
class PermissionRuleValue:
    """Permission rule value."""

    tool_name: str
    rule_content: str | None = None


@dataclass
class PermissionUpdate:
    """Permission update configuration."""

    type: Literal[
        "addRules",
        "replaceRules",
        "removeRules",
        "setMode",
        "addDirectories",
        "removeDirectories",
    ]
    rules: list[PermissionRuleValue] | None = None
    behavior: PermissionBehavior | None = None
    mode: PermissionMode | None = None
    directories: list[str] | None = None
    destination: PermissionUpdateDestination | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert PermissionUpdate to dictionary format matching TypeScript control protocol."""
        result: dict[str, Any] = {
            "type": self.type,
        }

        # Add destination for all variants
        if self.destination is not None:
            result["destination"] = self.destination

        # Handle different type variants
        if self.type in ["addRules", "replaceRules", "removeRules"]:
            # Rules-based variants require rules and behavior
            if self.rules is not None:
                result["rules"] = [
                    {
                        "toolName": rule.tool_name,
                        "ruleContent": rule.rule_content,
                    }
                    for rule in self.rules
                ]
            if self.behavior is not None:
                result["behavior"] = self.behavior

        elif self.type == "setMode":
            # Mode variant requires mode
            if self.mode is not None:
                result["mode"] = self.mode

        elif self.type in ["addDirectories", "removeDirectories"]:
            # Directory variants require directories
            if self.directories is not None:
                result["directories"] = self.directories

        return result


# Tool callback types
@dataclass
class ToolPermissionContext:
    """Context information for tool permission callbacks."""

    signal: Any | None = None  # Future: abort signal support
    suggestions: list[PermissionUpdate] = field(
        default_factory=list
    )  # Permission suggestions from CLI


# Match TypeScript's PermissionResult structure
@dataclass
class PermissionResultAllow:
    """Allow permission result."""

    behavior: Literal["allow"] = "allow"
    updated_input: dict[str, Any] | None = None
    updated_permissions: list[PermissionUpdate] | None = None


@dataclass
class PermissionResultDeny:
    """Deny permission result."""

    behavior: Literal["deny"] = "deny"
    message: str = ""
    interrupt: bool = False


PermissionResult = PermissionResultAllow | PermissionResultDeny

CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext], Awaitable[PermissionResult]
]


##### Hook types
HookEvent = (
    Literal["PreToolUse"]
    | Literal["PostToolUse"]
    | Literal["PostToolUseFailure"]
    | Literal["UserPromptSubmit"]
    | Literal["Stop"]
    | Literal["SubagentStop"]
    | Literal["PreCompact"]
    | Literal["Notification"]
    | Literal["SubagentStart"]
    | Literal["PermissionRequest"]
)


# Hook input types - strongly typed for each hook event
class BaseHookInput(TypedDict):
    """Base hook input fields present across many hook events."""

    session_id: str
    transcript_path: str
    cwd: str
    permission_mode: NotRequired[str]


# agent_id/agent_type are present on BaseHookInput in the CLI's schema but are
# declared per-hook here because SubagentStartHookInput/SubagentStopHookInput
# need them as *required*, and PEP 655 forbids narrowing NotRequired->Required
# in a TypedDict subclass. The four tool-lifecycle types below are the only
# ones the CLI actually populates (the other BaseHookInput consumers don't
# have a toolUseContext in scope at their build site).
class _SubagentContextMixin(TypedDict, total=False):
    """Optional sub-agent attribution fields for tool-lifecycle hooks.

    agent_id: Sub-agent identifier. Present only when the hook fires from
    inside a Task-spawned sub-agent; absent on the main thread. Matches the
    agent_id emitted by that sub-agent's SubagentStart/SubagentStop hooks.
    When multiple sub-agents run in parallel their tool-lifecycle hooks
    interleave over the same control channel — this is the only reliable
    way to attribute each one to the correct sub-agent.

    agent_type: Agent type name (e.g. "general-purpose", "code-reviewer").
    Present inside a sub-agent (alongside agent_id), or on the main thread
    of a session started with --agent (without agent_id).
    """

    agent_id: str
    agent_type: str


class PreToolUseHookInput(BaseHookInput, _SubagentContextMixin):
    """Input data for PreToolUse hook events."""

    hook_event_name: Literal["PreToolUse"]
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str


class PostToolUseHookInput(BaseHookInput, _SubagentContextMixin):
    """Input data for PostToolUse hook events."""

    hook_event_name: Literal["PostToolUse"]
    tool_name: str
    tool_input: dict[str, Any]
    tool_response: Any
    tool_use_id: str


class PostToolUseFailureHookInput(BaseHookInput, _SubagentContextMixin):
    """Input data for PostToolUseFailure hook events."""

    hook_event_name: Literal["PostToolUseFailure"]
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str
    error: str
    is_interrupt: NotRequired[bool]


class UserPromptSubmitHookInput(BaseHookInput):
    """Input data for UserPromptSubmit hook events."""

    hook_event_name: Literal["UserPromptSubmit"]
    prompt: str


class StopHookInput(BaseHookInput):
    """Input data for Stop hook events."""

    hook_event_name: Literal["Stop"]
    stop_hook_active: bool


class SubagentStopHookInput(BaseHookInput):
    """Input data for SubagentStop hook events."""

    hook_event_name: Literal["SubagentStop"]
    stop_hook_active: bool
    agent_id: str
    agent_transcript_path: str
    agent_type: str


class PreCompactHookInput(BaseHookInput):
    """Input data for PreCompact hook events."""

    hook_event_name: Literal["PreCompact"]
    trigger: Literal["manual", "auto"]
    custom_instructions: str | None


class NotificationHookInput(BaseHookInput):
    """Input data for Notification hook events."""

    hook_event_name: Literal["Notification"]
    message: str
    title: NotRequired[str]
    notification_type: str


class SubagentStartHookInput(BaseHookInput):
    """Input data for SubagentStart hook events."""

    hook_event_name: Literal["SubagentStart"]
    agent_id: str
    agent_type: str


class PermissionRequestHookInput(BaseHookInput, _SubagentContextMixin):
    """Input data for PermissionRequest hook events."""

    hook_event_name: Literal["PermissionRequest"]
    tool_name: str
    tool_input: dict[str, Any]
    permission_suggestions: NotRequired[list[Any]]


# Union type for all hook inputs
HookInput = (
    PreToolUseHookInput
    | PostToolUseHookInput
    | PostToolUseFailureHookInput
    | UserPromptSubmitHookInput
    | StopHookInput
    | SubagentStopHookInput
    | PreCompactHookInput
    | NotificationHookInput
    | SubagentStartHookInput
    | PermissionRequestHookInput
)


# Hook-specific output types
class PreToolUseHookSpecificOutput(TypedDict):
    """Hook-specific output for PreToolUse events."""

    hookEventName: Literal["PreToolUse"]
    permissionDecision: NotRequired[Literal["allow", "deny", "ask"]]
    permissionDecisionReason: NotRequired[str]
    updatedInput: NotRequired[dict[str, Any]]
    additionalContext: NotRequired[str]


class PostToolUseHookSpecificOutput(TypedDict):
    """Hook-specific output for PostToolUse events."""

    hookEventName: Literal["PostToolUse"]
    additionalContext: NotRequired[str]
    updatedMCPToolOutput: NotRequired[Any]


class PostToolUseFailureHookSpecificOutput(TypedDict):
    """Hook-specific output for PostToolUseFailure events."""

    hookEventName: Literal["PostToolUseFailure"]
    additionalContext: NotRequired[str]


class UserPromptSubmitHookSpecificOutput(TypedDict):
    """Hook-specific output for UserPromptSubmit events."""

    hookEventName: Literal["UserPromptSubmit"]
    additionalContext: NotRequired[str]


class SessionStartHookSpecificOutput(TypedDict):
    """Hook-specific output for SessionStart events."""

    hookEventName: Literal["SessionStart"]
    additionalContext: NotRequired[str]


class NotificationHookSpecificOutput(TypedDict):
    """Hook-specific output for Notification events."""

    hookEventName: Literal["Notification"]
    additionalContext: NotRequired[str]


class SubagentStartHookSpecificOutput(TypedDict):
    """Hook-specific output for SubagentStart events."""

    hookEventName: Literal["SubagentStart"]
    additionalContext: NotRequired[str]


class PermissionRequestHookSpecificOutput(TypedDict):
    """Hook-specific output for PermissionRequest events."""

    hookEventName: Literal["PermissionRequest"]
    decision: dict[str, Any]


HookSpecificOutput = (
    PreToolUseHookSpecificOutput
    | PostToolUseHookSpecificOutput
    | PostToolUseFailureHookSpecificOutput
    | UserPromptSubmitHookSpecificOutput
    | SessionStartHookSpecificOutput
    | NotificationHookSpecificOutput
    | SubagentStartHookSpecificOutput
    | PermissionRequestHookSpecificOutput
)


# See https://docs.anthropic.com/en/docs/claude-code/hooks#advanced%3A-json-output
# for documentation of the output types.
#
# IMPORTANT: The Python SDK uses `async_` and `continue_` (with underscores) to avoid
# Python keyword conflicts. These fields are automatically converted to `async` and
# `continue` when sent to the CLI. You should use the underscore versions in your
# Python code.
class AsyncHookJSONOutput(TypedDict):
    """Async hook output that defers hook execution.

    Fields:
        async_: Set to True to defer hook execution. Note: This is converted to
            "async" when sent to the CLI - use "async_" in your Python code.
        asyncTimeout: Optional timeout in milliseconds for the async operation.
    """

    async_: Literal[
        True
    ]  # Using async_ to avoid Python keyword (converted to "async" for CLI)
    asyncTimeout: NotRequired[int]


class SyncHookJSONOutput(TypedDict):
    """Synchronous hook output with control and decision fields.

    This defines the structure for hook callbacks to control execution and provide
    feedback to Claude.

    Common Control Fields:
        continue_: Whether Claude should proceed after hook execution (default: True).
            Note: This is converted to "continue" when sent to the CLI.
        suppressOutput: Hide stdout from transcript mode (default: False).
        stopReason: Message shown when continue is False.

    Decision Fields:
        decision: Set to "block" to indicate blocking behavior.
        systemMessage: Warning message displayed to the user.
        reason: Feedback message for Claude about the decision.

    Hook-Specific Output:
        hookSpecificOutput: Event-specific controls (e.g., permissionDecision for
            PreToolUse, additionalContext for PostToolUse).

    Note: The CLI documentation shows field names without underscores ("async", "continue"),
    but Python code should use the underscore versions ("async_", "continue_") as they
    are automatically converted.
    """

    # Common control fields
    continue_: NotRequired[
        bool
    ]  # Using continue_ to avoid Python keyword (converted to "continue" for CLI)
    suppressOutput: NotRequired[bool]
    stopReason: NotRequired[str]

    # Decision fields
    # Note: "approve" is deprecated for PreToolUse (use permissionDecision instead)
    # For other hooks, only "block" is meaningful
    decision: NotRequired[Literal["block"]]
    systemMessage: NotRequired[str]
    reason: NotRequired[str]

    # Hook-specific outputs
    hookSpecificOutput: NotRequired[HookSpecificOutput]


HookJSONOutput = AsyncHookJSONOutput | SyncHookJSONOutput


class HookContext(TypedDict):
    """Context information for hook callbacks.

    Fields:
        signal: Reserved for future abort signal support. Currently always None.
    """

    signal: Any | None  # Future: abort signal support


HookCallback = Callable[
    # HookCallback input parameters:
    # - input: Strongly-typed hook input with discriminated unions based on hook_event_name
    # - tool_use_id: Optional tool use identifier
    # - context: Hook context with abort signal support (currently placeholder)
    [HookInput, str | None, HookContext],
    Awaitable[HookJSONOutput],
]


# Hook matcher configuration
@dataclass
class HookMatcher:
    """Hook matcher configuration."""

    # See https://docs.anthropic.com/en/docs/claude-code/hooks#structure for the
    # expected string value. For example, for PreToolUse, the matcher can be
    # a tool name like "Bash" or a combination of tool names like
    # "Write|MultiEdit|Edit".
    matcher: str | None = None

    # A list of Python functions with function signature HookCallback
    hooks: list[HookCallback] = field(default_factory=list)

    # Timeout in seconds for all hooks in this matcher (default: 60)
    timeout: float | None = None


# MCP Server config
class McpStdioServerConfig(TypedDict):
    """MCP stdio server configuration."""

    type: NotRequired[Literal["stdio"]]  # Optional for backwards compatibility
    command: str
    args: NotRequired[list[str]]
    env: NotRequired[dict[str, str]]


class McpSSEServerConfig(TypedDict):
    """MCP SSE server configuration."""

    type: Literal["sse"]
    url: str
    headers: NotRequired[dict[str, str]]


class McpHttpServerConfig(TypedDict):
    """MCP HTTP server configuration."""

    type: Literal["http"]
    url: str
    headers: NotRequired[dict[str, str]]


class McpSdkServerConfig(TypedDict):
    """SDK MCP server configuration."""

    type: Literal["sdk"]
    name: str
    instance: "McpServer"


McpServerConfig = (
    McpStdioServerConfig | McpSSEServerConfig | McpHttpServerConfig | McpSdkServerConfig
)


# MCP Server Status types (returned by get_mcp_status)
# These mirror the TypeScript SDK's McpServerStatus type and use wire-format
# field names (camelCase where applicable) since they come directly from CLI
# JSON output.


class McpSdkServerConfigStatus(TypedDict):
    """SDK MCP server config as returned in status responses.

    Unlike McpSdkServerConfig (which includes the in-process `instance`),
    this output-only type only has serializable fields.
    """

    type: Literal["sdk"]
    name: str


class McpClaudeAIProxyServerConfig(TypedDict):
    """Claude.ai proxy MCP server config.

    Output-only type that appears in status responses for servers proxied
    through Claude.ai.
    """

    type: Literal["claudeai-proxy"]
    url: str
    id: str


# Broader config type for status responses (includes claudeai-proxy which is
# output-only)
McpServerStatusConfig = (
    McpStdioServerConfig
    | McpSSEServerConfig
    | McpHttpServerConfig
    | McpSdkServerConfigStatus
    | McpClaudeAIProxyServerConfig
)


class McpToolAnnotations(TypedDict, total=False):
    """Tool annotations as returned in MCP server status.

    Wire format uses camelCase field names (from CLI JSON output).
    """

    readOnly: bool
    destructive: bool
    openWorld: bool


class McpToolInfo(TypedDict):
    """Information about a tool provided by an MCP server."""

    name: str
    description: NotRequired[str]
    annotations: NotRequired[McpToolAnnotations]


class McpServerInfo(TypedDict):
    """Server info from MCP initialize handshake (available when connected)."""

    name: str
    version: str


# Connection status values for an MCP server
McpServerConnectionStatus = Literal[
    "connected", "failed", "needs-auth", "pending", "disabled"
]


class McpServerStatus(TypedDict):
    """Status information for an MCP server connection.

    Returned by `ClaudeSDKClient.get_mcp_status()` in the `mcpServers` list.
    """

    name: str
    """Server name as configured."""

    status: McpServerConnectionStatus
    """Current connection status."""

    serverInfo: NotRequired[McpServerInfo]
    """Server information from MCP handshake (available when connected)."""

    error: NotRequired[str]
    """Error message (available when status is 'failed')."""

    config: NotRequired[McpServerStatusConfig]
    """Server configuration (includes URL for HTTP/SSE servers)."""

    scope: NotRequired[str]
    """Configuration scope (e.g., project, user, local, claudeai, managed)."""

    tools: NotRequired[list[McpToolInfo]]
    """Tools provided by this server (available when connected)."""


class McpStatusResponse(TypedDict):
    """Response from `ClaudeSDKClient.get_mcp_status()`.

    Wraps the list of server statuses under the `mcpServers` key, matching
    the wire-format response shape.
    """

    mcpServers: list[McpServerStatus]


class SdkPluginConfig(TypedDict):
    """SDK plugin configuration.

    Currently only local plugins are supported via the 'local' type.
    """

    type: Literal["local"]
    path: str


# Sandbox configuration types
class SandboxNetworkConfig(TypedDict, total=False):
    """Network configuration for sandbox.

    Attributes:
        allowUnixSockets: Unix socket paths accessible in sandbox (e.g., SSH agents).
        allowAllUnixSockets: Allow all Unix sockets (less secure).
        allowLocalBinding: Allow binding to localhost ports (macOS only).
        httpProxyPort: HTTP proxy port if bringing your own proxy.
        socksProxyPort: SOCKS5 proxy port if bringing your own proxy.
    """

    allowUnixSockets: list[str]
    allowAllUnixSockets: bool
    allowLocalBinding: bool
    httpProxyPort: int
    socksProxyPort: int


class SandboxIgnoreViolations(TypedDict, total=False):
    """Violations to ignore in sandbox.

    Attributes:
        file: File paths for which violations should be ignored.
        network: Network hosts for which violations should be ignored.
    """

    file: list[str]
    network: list[str]


class SandboxSettings(TypedDict, total=False):
    """Sandbox settings configuration.

    This controls how Claude Code sandboxes bash commands for filesystem
    and network isolation.

    **Important:** Filesystem and network restrictions are configured via permission
    rules, not via these sandbox settings:
    - Filesystem read restrictions: Use Read deny rules
    - Filesystem write restrictions: Use Edit allow/deny rules
    - Network restrictions: Use WebFetch allow/deny rules

    Attributes:
        enabled: Enable bash sandboxing (macOS/Linux only). Default: False
        autoAllowBashIfSandboxed: Auto-approve bash commands when sandboxed. Default: True
        excludedCommands: Commands that should run outside the sandbox (e.g., ["git", "docker"])
        allowUnsandboxedCommands: Allow commands to bypass sandbox via dangerouslyDisableSandbox.
            When False, all commands must run sandboxed (or be in excludedCommands). Default: True
        network: Network configuration for sandbox.
        ignoreViolations: Violations to ignore.
        enableWeakerNestedSandbox: Enable weaker sandbox for unprivileged Docker environments
            (Linux only). Reduces security. Default: False

    Example:
        ```python
        sandbox_settings: SandboxSettings = {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "excludedCommands": ["docker"],
            "network": {
                "allowUnixSockets": ["/var/run/docker.sock"],
                "allowLocalBinding": True
            }
        }
        ```
    """

    enabled: bool
    autoAllowBashIfSandboxed: bool
    excludedCommands: list[str]
    allowUnsandboxedCommands: bool
    network: SandboxNetworkConfig
    ignoreViolations: SandboxIgnoreViolations
    enableWeakerNestedSandbox: bool


# Content block types
@dataclass
class TextBlock:
    """Text content block."""

    text: str


@dataclass
class ThinkingBlock:
    """Thinking content block."""

    thinking: str
    signature: str


@dataclass
class ToolUseBlock:
    """Tool use content block."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    """Tool result content block."""

    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


# Message types
AssistantMessageError = Literal[
    "authentication_failed",
    "billing_error",
    "rate_limit",
    "invalid_request",
    "server_error",
    "unknown",
]


@dataclass
class UserMessage:
    """User message."""

    content: str | list[ContentBlock]
    uuid: str | None = None
    parent_tool_use_id: str | None = None
    tool_use_result: dict[str, Any] | None = None


@dataclass
class AssistantMessage:
    """Assistant message with content blocks."""

    content: list[ContentBlock]
    model: str
    parent_tool_use_id: str | None = None
    error: AssistantMessageError | None = None


@dataclass
class SystemMessage:
    """System message with metadata."""

    subtype: str
    data: dict[str, Any]


class TaskUsage(TypedDict):
    """Usage statistics reported in task_progress and task_notification messages."""

    total_tokens: int
    tool_uses: int
    duration_ms: int


# Possible status values for a task_notification message.
TaskNotificationStatus = Literal["completed", "failed", "stopped"]


@dataclass
class TaskStartedMessage(SystemMessage):
    """System message emitted when a task starts.

    Subclass of SystemMessage: existing ``isinstance(msg, SystemMessage)`` and
    ``case SystemMessage()`` checks continue to match. The base ``subtype``
    and ``data`` fields remain populated with the raw payload.
    """

    task_id: str
    description: str
    uuid: str
    session_id: str
    tool_use_id: str | None = None
    task_type: str | None = None


@dataclass
class TaskProgressMessage(SystemMessage):
    """System message emitted while a task is in progress.

    Subclass of SystemMessage: existing ``isinstance(msg, SystemMessage)`` and
    ``case SystemMessage()`` checks continue to match. The base ``subtype``
    and ``data`` fields remain populated with the raw payload.
    """

    task_id: str
    description: str
    usage: TaskUsage
    uuid: str
    session_id: str
    tool_use_id: str | None = None
    last_tool_name: str | None = None


@dataclass
class TaskNotificationMessage(SystemMessage):
    """System message emitted when a task completes, fails, or is stopped.

    Subclass of SystemMessage: existing ``isinstance(msg, SystemMessage)`` and
    ``case SystemMessage()`` checks continue to match. The base ``subtype``
    and ``data`` fields remain populated with the raw payload.
    """

    task_id: str
    status: TaskNotificationStatus
    output_file: str
    summary: str
    uuid: str
    session_id: str
    tool_use_id: str | None = None
    usage: TaskUsage | None = None


@dataclass
class ResultMessage:
    """Result message with cost and usage information."""

    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    stop_reason: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    result: str | None = None
    structured_output: Any = None


@dataclass
class StreamEvent:
    """Stream event for partial message updates during streaming."""

    uuid: str
    session_id: str
    event: dict[str, Any]  # The raw Anthropic API stream event
    parent_tool_use_id: str | None = None


# Rate limit types — see https://docs.claude.com/en/docs/claude-code/rate-limits
RateLimitStatus = Literal["allowed", "allowed_warning", "rejected"]
RateLimitType = Literal[
    "five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "overage"
]


@dataclass
class RateLimitInfo:
    """Rate limit status emitted by the CLI when rate limit state changes.

    Attributes:
        status: Current rate limit status. ``allowed_warning`` means approaching
            the limit; ``rejected`` means the limit has been hit.
        resets_at: Unix timestamp when the rate limit window resets.
        rate_limit_type: Which rate limit window applies.
        utilization: Fraction of the rate limit consumed (0.0 - 1.0).
        overage_status: Status of overage/pay-as-you-go usage if applicable.
        overage_resets_at: Unix timestamp when overage window resets.
        overage_disabled_reason: Why overage is unavailable if status is rejected.
        raw: Full raw dict from the CLI, including any fields not modeled above.
    """

    status: RateLimitStatus
    resets_at: int | None = None
    rate_limit_type: RateLimitType | None = None
    utilization: float | None = None
    overage_status: RateLimitStatus | None = None
    overage_resets_at: int | None = None
    overage_disabled_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitEvent:
    """Rate limit event emitted when rate limit info changes.

    The CLI emits this whenever the rate limit status transitions (e.g. from
    ``allowed`` to ``allowed_warning``). Use this to warn users before they
    hit a hard limit, or to gracefully back off when ``status == "rejected"``.
    """

    rate_limit_info: RateLimitInfo
    uuid: str
    session_id: str


Message = (
    UserMessage
    | AssistantMessage
    | SystemMessage
    | ResultMessage
    | StreamEvent
    | RateLimitEvent
)


# ---------------------------------------------------------------------------
# Session Listing Types
# ---------------------------------------------------------------------------


@dataclass
class SDKSessionInfo:
    """Session metadata returned by ``list_sessions()``.

    Contains only data extractable from stat + head/tail reads — no full
    JSONL parsing required.

    Attributes:
        session_id: Unique session identifier (UUID).
        summary: Display title for the session — custom title, auto-generated
            summary, or first prompt.
        last_modified: Last modified time in milliseconds since epoch.
        file_size: Session file size in bytes.
        custom_title: User-set session title via /rename.
        first_prompt: First meaningful user prompt in the session.
        git_branch: Git branch at the end of the session.
        cwd: Working directory for the session.
    """

    session_id: str
    summary: str
    last_modified: int
    file_size: int
    custom_title: str | None = None
    first_prompt: str | None = None
    git_branch: str | None = None
    cwd: str | None = None


@dataclass
class SessionMessage:
    """A user or assistant message from a session transcript.

    Returned by ``get_session_messages()`` for reading historical session
    data. Fields match the SDK wire protocol types (SDKUserMessage /
    SDKAssistantMessage).

    Attributes:
        type: Message type — ``"user"`` or ``"assistant"``.
        uuid: Unique message identifier.
        session_id: ID of the session this message belongs to.
        message: Raw Anthropic API message dict (role, content, etc.).
        parent_tool_use_id: Always ``None`` for top-level conversation
            messages (tool-use sidechain messages are filtered out).
    """

    type: Literal["user", "assistant"]
    uuid: str
    session_id: str
    message: Any
    parent_tool_use_id: None = None


class ThinkingConfigAdaptive(TypedDict):
    type: Literal["adaptive"]


class ThinkingConfigEnabled(TypedDict):
    type: Literal["enabled"]
    budget_tokens: int


class ThinkingConfigDisabled(TypedDict):
    type: Literal["disabled"]


ThinkingConfig = ThinkingConfigAdaptive | ThinkingConfigEnabled | ThinkingConfigDisabled


@dataclass
class ClaudeAgentOptions:
    """Query options for Claude SDK."""

    tools: list[str] | ToolsPreset | None = None
    allowed_tools: list[str] = field(default_factory=list)
    system_prompt: str | SystemPromptPreset | None = None
    mcp_servers: dict[str, McpServerConfig] | str | Path = field(default_factory=dict)
    permission_mode: PermissionMode | None = None
    continue_conversation: bool = False
    resume: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    fallback_model: str | None = None
    # Beta features - see https://docs.anthropic.com/en/api/beta-headers
    betas: list[SdkBeta] = field(default_factory=list)
    permission_prompt_tool_name: str | None = None
    cwd: str | Path | None = None
    cli_path: str | Path | None = None
    settings: str | None = None
    add_dirs: list[str | Path] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    extra_args: dict[str, str | None] = field(
        default_factory=dict
    )  # Pass arbitrary CLI flags
    max_buffer_size: int | None = None  # Max bytes when buffering CLI stdout
    debug_stderr: Any = (
        sys.stderr
    )  # Deprecated: File-like object for debug output. Use stderr callback instead.
    stderr: Callable[[str], None] | None = None  # Callback for stderr output from CLI

    # Tool permission callback
    can_use_tool: CanUseTool | None = None

    # Hook configurations
    hooks: dict[HookEvent, list[HookMatcher]] | None = None

    user: str | None = None

    # Partial message streaming support
    include_partial_messages: bool = False
    # When true resumed sessions will fork to a new session ID rather than
    # continuing the previous session.
    fork_session: bool = False
    # Agent definitions for custom agents
    agents: dict[str, AgentDefinition] | None = None
    # Setting sources to load (user, project, local)
    setting_sources: list[SettingSource] | None = None
    # Sandbox configuration for bash command isolation.
    # Filesystem and network restrictions are derived from permission rules (Read/Edit/WebFetch),
    # not from these sandbox settings.
    sandbox: SandboxSettings | None = None
    # Plugin configurations for custom plugins
    plugins: list[SdkPluginConfig] = field(default_factory=list)
    # Max tokens for thinking blocks
    # @deprecated Use `thinking` instead.
    max_thinking_tokens: int | None = None
    # Controls extended thinking behavior. Takes precedence over max_thinking_tokens.
    thinking: ThinkingConfig | None = None
    # Effort level for thinking depth.
    effort: Literal["low", "medium", "high", "max"] | None = None
    # Output format for structured outputs (matches Messages API structure)
    # Example: {"type": "json_schema", "schema": {"type": "object", "properties": {...}}}
    output_format: dict[str, Any] | None = None
    # Enable file checkpointing to track file changes during the session.
    # When enabled, files can be rewound to their state at any user message
    # using `ClaudeSDKClient.rewind_files()`.
    enable_file_checkpointing: bool = False


# SDK Control Protocol
class SDKControlInterruptRequest(TypedDict):
    subtype: Literal["interrupt"]


class SDKControlPermissionRequest(TypedDict):
    subtype: Literal["can_use_tool"]
    tool_name: str
    input: dict[str, Any]
    # TODO: Add PermissionUpdate type here
    permission_suggestions: list[Any] | None
    blocked_path: str | None


class SDKControlInitializeRequest(TypedDict):
    subtype: Literal["initialize"]
    hooks: dict[HookEvent, Any] | None
    agents: NotRequired[dict[str, dict[str, Any]]]


class SDKControlSetPermissionModeRequest(TypedDict):
    subtype: Literal["set_permission_mode"]
    # TODO: Add PermissionMode
    mode: str


class SDKHookCallbackRequest(TypedDict):
    subtype: Literal["hook_callback"]
    callback_id: str
    input: Any
    tool_use_id: str | None


class SDKControlMcpMessageRequest(TypedDict):
    subtype: Literal["mcp_message"]
    server_name: str
    message: Any


class SDKControlRewindFilesRequest(TypedDict):
    subtype: Literal["rewind_files"]
    user_message_id: str


class SDKControlMcpReconnectRequest(TypedDict):
    """Reconnects a disconnected or failed MCP server."""

    subtype: Literal["mcp_reconnect"]
    # Note: wire protocol uses camelCase for this field
    serverName: str


class SDKControlMcpToggleRequest(TypedDict):
    """Enables or disables an MCP server."""

    subtype: Literal["mcp_toggle"]
    # Note: wire protocol uses camelCase for this field
    serverName: str
    enabled: bool


class SDKControlStopTaskRequest(TypedDict):
    subtype: Literal["stop_task"]
    task_id: str


class SDKControlRequest(TypedDict):
    type: Literal["control_request"]
    request_id: str
    request: (
        SDKControlInterruptRequest
        | SDKControlPermissionRequest
        | SDKControlInitializeRequest
        | SDKControlSetPermissionModeRequest
        | SDKHookCallbackRequest
        | SDKControlMcpMessageRequest
        | SDKControlRewindFilesRequest
        | SDKControlMcpReconnectRequest
        | SDKControlMcpToggleRequest
        | SDKControlStopTaskRequest
    )


class ControlResponse(TypedDict):
    subtype: Literal["success"]
    request_id: str
    response: dict[str, Any] | None


class ControlErrorResponse(TypedDict):
    subtype: Literal["error"]
    request_id: str
    error: str


class SDKControlResponse(TypedDict):
    type: Literal["control_response"]
    response: ControlResponse | ControlErrorResponse
