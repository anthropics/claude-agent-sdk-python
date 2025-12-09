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


# Subagent execution modes
SubagentExecutionMode = Literal["sequential", "parallel", "auto"]

# Multi-invocation handling modes
MultiInvocationMode = Literal["sequential", "parallel", "error"]

# Error handling modes for subagent execution
SubagentErrorHandling = Literal["fail_fast", "continue"]


@dataclass
class SubagentExecutionConfig:
    """Configuration for subagent execution behavior.

    This controls how the SDK handles multiple Task tool calls (subagent invocations)
    when they occur in the same turn.

    Attributes:
        multi_invocation: How to handle multiple Task tool calls in the same turn.
            - "sequential": Execute subagents one at a time in order (default)
            - "parallel": Execute subagents concurrently
            - "error": Raise an error if multiple Task tools are invoked in same turn
        max_concurrent: Maximum number of subagents to run concurrently when
            multi_invocation is "parallel". Default is 3.
        error_handling: How to handle errors from individual subagents.
            - "fail_fast": Stop execution on first subagent error
            - "continue": Continue executing remaining subagents on error (default)

    Example:
        >>> config = SubagentExecutionConfig(
        ...     multi_invocation="parallel",
        ...     max_concurrent=5,
        ...     error_handling="fail_fast"
        ... )
        >>> options = ClaudeAgentOptions(subagent_execution=config)
    """

    multi_invocation: MultiInvocationMode = "sequential"
    max_concurrent: int = 3
    error_handling: SubagentErrorHandling = "continue"


@dataclass
class AgentDefinition:
    """Agent definition configuration.

    Defines a custom agent (subagent) that can be invoked via the Task tool.

    Attributes:
        description: A short description of what this agent does. This is shown
            to the parent agent to help it decide when to use this subagent.
        prompt: The system prompt or instructions for this agent.
        tools: Optional list of tool names this agent can use. If None, inherits
            from parent agent.
        model: Optional model override for this agent.
            - "sonnet": Use Claude Sonnet
            - "opus": Use Claude Opus
            - "haiku": Use Claude Haiku (faster, lower cost)
            - "inherit": Use the same model as the parent agent
            - None: Use default model
        execution_mode: How this agent should be executed when invoked alongside
            other subagents in the same turn.
            - "sequential": This agent must complete before the next starts
            - "parallel": This agent can run concurrently with others
            - "auto": SDK decides based on global SubagentExecutionConfig (default)
            - None: Same as "auto"

    Example:
        >>> agent = AgentDefinition(
        ...     description="Analyzes code for security issues",
        ...     prompt="You are a security analyst...",
        ...     tools=["Read", "Grep", "Glob"],
        ...     model="haiku",
        ...     execution_mode="parallel"
        ... )
    """

    description: str
    prompt: str
    tools: list[str] | None = None
    model: Literal["sonnet", "opus", "haiku", "inherit"] | None = None
    execution_mode: SubagentExecutionMode | None = None


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
# Supported hook event types. Due to setup limitations, the Python SDK does not
# support SessionStart, SessionEnd, and Notification hooks.
HookEvent = (
    Literal["PreToolUse"]
    | Literal["PostToolUse"]
    | Literal["UserPromptSubmit"]
    | Literal["Stop"]
    | Literal["SubagentStop"]
    | Literal["PreCompact"]
)


# Hook input types - strongly typed for each hook event
class BaseHookInput(TypedDict):
    """Base hook input fields present across many hook events."""

    session_id: str
    transcript_path: str
    cwd: str
    permission_mode: NotRequired[str]


class PreToolUseHookInput(BaseHookInput):
    """Input data for PreToolUse hook events."""

    hook_event_name: Literal["PreToolUse"]
    tool_name: str
    tool_input: dict[str, Any]


class PostToolUseHookInput(BaseHookInput):
    """Input data for PostToolUse hook events."""

    hook_event_name: Literal["PostToolUse"]
    tool_name: str
    tool_input: dict[str, Any]
    tool_response: Any


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


class PreCompactHookInput(BaseHookInput):
    """Input data for PreCompact hook events."""

    hook_event_name: Literal["PreCompact"]
    trigger: Literal["manual", "auto"]
    custom_instructions: str | None


# Union type for all hook inputs
HookInput = (
    PreToolUseHookInput
    | PostToolUseHookInput
    | UserPromptSubmitHookInput
    | StopHookInput
    | SubagentStopHookInput
    | PreCompactHookInput
)


# Hook-specific output types
class PreToolUseHookSpecificOutput(TypedDict):
    """Hook-specific output for PreToolUse events."""

    hookEventName: Literal["PreToolUse"]
    permissionDecision: NotRequired[Literal["allow", "deny", "ask"]]
    permissionDecisionReason: NotRequired[str]
    updatedInput: NotRequired[dict[str, Any]]


class PostToolUseHookSpecificOutput(TypedDict):
    """Hook-specific output for PostToolUse events."""

    hookEventName: Literal["PostToolUse"]
    additionalContext: NotRequired[str]


class UserPromptSubmitHookSpecificOutput(TypedDict):
    """Hook-specific output for UserPromptSubmit events."""

    hookEventName: Literal["UserPromptSubmit"]
    additionalContext: NotRequired[str]


class SessionStartHookSpecificOutput(TypedDict):
    """Hook-specific output for SessionStart events."""

    hookEventName: Literal["SessionStart"]
    additionalContext: NotRequired[str]


HookSpecificOutput = (
    PreToolUseHookSpecificOutput
    | PostToolUseHookSpecificOutput
    | UserPromptSubmitHookSpecificOutput
    | SessionStartHookSpecificOutput
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
    """Hook matcher configuration for matching tool invocations.

    The matcher field supports regex patterns for filtering which tools trigger
    the associated hooks. This is particularly useful for PreToolUse and
    PostToolUse hook events.

    Attributes:
        matcher: Regex pattern for matching tool names. If None, matches all tools.
        hooks: List of async callback functions to execute when a tool matches.
        timeout: Timeout in seconds for all hooks in this matcher (default: 60).

    Pattern Examples:
        Built-in tools:
            - "Bash" - Match only the Bash tool
            - "Read" - Match only the Read tool
            - "Write|Edit" - Match Write OR Edit tools
            - "Write|MultiEdit|Edit" - Match any file writing tool

        MCP tools (format: mcp__<server>__<tool>):
            - "mcp__.*" - Match all MCP tools from any server
            - "mcp__slack__.*" - Match all tools from the slack MCP server
            - "mcp__github__create_issue" - Match specific github tool
            - "mcp__.*__delete.*" - Match any MCP tool with "delete" in the name
            - "mcp__db__.*write.*" - Match db server tools containing "write"

        Combined patterns:
            - "Bash|mcp__.*__execute.*" - Match Bash or any MCP execute tools
            - None - Match all tools (universal matcher)

    Example:
        >>> from claude_agent_sdk import HookMatcher, HookCallback
        >>>
        >>> async def log_mcp_calls(input, tool_use_id, context):
        ...     print(f"MCP tool called: {input['tool_name']}")
        ...     return {"continue_": True}
        >>>
        >>> # Match all MCP tools
        >>> mcp_matcher = HookMatcher(
        ...     matcher="mcp__.*",
        ...     hooks=[log_mcp_calls],
        ...     timeout=30.0
        ... )
        >>>
        >>> # Match dangerous operations
        >>> dangerous_matcher = HookMatcher(
        ...     matcher="mcp__.*__delete.*|mcp__.*__drop.*",
        ...     hooks=[confirm_dangerous_operation],
        ...     timeout=60.0
        ... )

    See Also:
        https://docs.anthropic.com/en/docs/claude-code/hooks#structure
    """

    matcher: str | None = None
    hooks: list[HookCallback] = field(default_factory=list)
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
    parent_tool_use_id: str | None = None


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


@dataclass
class ResultMessage:
    """Result message with cost and usage information."""

    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
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


Message = UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent


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
    # Subagent execution configuration (controls parallel vs sequential execution)
    subagent_execution: SubagentExecutionConfig | None = None
    # Setting sources to load (user, project, local)
    setting_sources: list[SettingSource] | None = None
    # Sandbox configuration for bash command isolation.
    # Filesystem and network restrictions are derived from permission rules (Read/Edit/WebFetch),
    # not from these sandbox settings.
    sandbox: SandboxSettings | None = None
    # Plugin configurations for custom plugins
    plugins: list[SdkPluginConfig] = field(default_factory=list)
    # Max tokens for thinking blocks
    max_thinking_tokens: int | None = None
    # Output format for structured outputs (matches Messages API structure)
    # Example: {"type": "json_schema", "schema": {"type": "object", "properties": {...}}}
    output_format: dict[str, Any] | None = None


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
