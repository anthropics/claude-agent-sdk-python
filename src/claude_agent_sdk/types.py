"""Type definitions for Claude SDK."""

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

if sys.version_info >= (3, 11):
    from typing import NotRequired, Required, TypedDict
else:
    # PEP 655: stdlib TypedDict on 3.10 doesn't process NotRequired/Required,
    # so __required_keys__ would be wrong. typing_extensions backports the
    # correct behavior.
    from typing_extensions import NotRequired, Required, TypedDict

if TYPE_CHECKING:
    from mcp.server import Server as McpServer
else:
    # Runtime placeholder for forward reference resolution in Pydantic 2.12+
    McpServer = Any

# Permission modes
PermissionMode = Literal[
    "default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"
]

# SDK Beta features - see https://docs.anthropic.com/en/api/beta-headers
SdkBeta = Literal["context-1m-2025-08-07"]

# Agent definitions
SettingSource = Literal["user", "project", "local"]
EffortLevel: TypeAlias = Literal["low", "medium", "high", "xhigh", "max"]


class SystemPromptPreset(TypedDict):
    """System prompt preset configuration."""

    type: Literal["preset"]
    preset: Literal["claude_code"]
    append: NotRequired[str]
    exclude_dynamic_sections: NotRequired[bool]
    """Strip per-user dynamic sections (working directory, auto-memory, git
    status) from the system prompt so it stays static and cacheable across
    users. The stripped content is re-injected into the first user message
    so the model still has access to it.

    Use this when many users share the same preset system prompt and you
    want the prompt-caching prefix to hit cross-user.

    Requires a Claude Code CLI version that supports this option; older
    CLIs silently ignore it.
    """


class SystemPromptFile(TypedDict):
    """System prompt file configuration."""

    type: Literal["file"]
    path: str


class TaskBudget(TypedDict):
    """API-side task budget in tokens.

    When set, the model is made aware of its remaining token budget so it can
    pace tool use and wrap up before the limit. Sent as
    ``output_config.task_budget`` with the ``task-budgets-2026-03-13`` beta
    header.
    """

    total: int


class ToolsPreset(TypedDict):
    """Tools preset configuration."""

    type: Literal["preset"]
    preset: Literal["claude_code"]


@dataclass
class AgentDefinition:
    """Agent definition configuration."""

    description: str
    prompt: str
    # Deprecated: passing "Skill" here is deprecated; use `skills` instead.
    tools: list[str] | None = None
    disallowedTools: list[str] | None = None  # noqa: N815
    # Model alias ("sonnet", "opus", "haiku", "inherit") or a full model ID.
    model: str | None = None
    skills: list[str] | None = None
    memory: Literal["user", "project", "local"] | None = None
    # Each entry is a server name (str) or an inline {name: config} dict.
    mcpServers: list[str | dict[str, Any]] | None = None  # noqa: N815
    initialPrompt: str | None = None  # noqa: N815
    maxTurns: int | None = None  # noqa: N815
    background: bool | None = None
    effort: EffortLevel | int | None = None
    permissionMode: PermissionMode | None = None  # noqa: N815


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PermissionUpdate":
        """Construct a PermissionUpdate from the control protocol dict format (inverse of to_dict)."""
        rules = None
        if data.get("rules") is not None:
            rules = [
                PermissionRuleValue(
                    tool_name=r["toolName"],
                    rule_content=r.get("ruleContent"),
                )
                for r in data["rules"]
            ]
        return cls(
            type=data["type"],
            rules=rules,
            behavior=data.get("behavior"),
            mode=data.get("mode"),
            directories=data.get("directories"),
            destination=data.get("destination"),
        )


# Tool callback types
@dataclass
class ToolPermissionContext:
    """Context information for tool permission callbacks."""

    signal: Any | None = None  # Future: abort signal support
    suggestions: list[PermissionUpdate] = field(
        default_factory=list
    )  # Permission suggestions from CLI
    tool_use_id: str | None = None
    """Unique identifier for this specific tool call within the assistant message.
    Multiple tool calls in the same assistant message will have different tool_use_ids.

    Always a non-empty string when delivered to a ``can_use_tool`` callback (the
    wire protocol guarantees it); the ``Optional`` is only for dataclass
    field-ordering compatibility, so callers do not need to handle ``None``."""
    agent_id: str | None = None
    """If running within the context of a sub-agent, the sub-agent's ID."""
    blocked_path: str | None = None
    """The file path that triggered the permission request, if applicable.
    For example, when a Bash command tries to access a path outside allowed directories."""
    decision_reason: str | None = None
    """Explains why this permission request was triggered.
    When a PreToolUse hook returns ``permissionDecision: "ask"`` with a
    ``permissionDecisionReason``, that reason is forwarded here."""
    title: str | None = None
    """Full permission prompt sentence (e.g. "Claude wants to read foo.txt").
    Use this as the primary prompt text when present instead of reconstructing
    from tool name + input."""
    display_name: str | None = None
    """Short noun phrase for the tool action (e.g. "Read file"), suitable for
    button labels or compact UI."""
    description: str | None = None
    """Human-readable subtitle for the permission UI."""


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
    permissionDecision: NotRequired[Literal["allow", "deny", "ask", "defer"]]
    permissionDecisionReason: NotRequired[str]
    updatedInput: NotRequired[dict[str, Any]]
    additionalContext: NotRequired[str]


class PostToolUseHookSpecificOutput(TypedDict):
    """Hook-specific output for PostToolUse events."""

    hookEventName: Literal["PostToolUse"]
    additionalContext: NotRequired[str]
    updatedToolOutput: NotRequired[Any]
    """Replaces the tool output before it is sent to the model.

    For built-in tools (Bash, Read, Edit, etc.) the value must match the tool's
    output schema (e.g. ``{"stdout": ..., "stderr": ..., "interrupted": ...}``
    for Bash); a mismatched shape is rejected and the original output is kept.
    """
    updatedMCPToolOutput: NotRequired[Any]
    """Replaces the output for MCP tools only. Prefer ``updatedToolOutput``,
    which works for all tools.
    """


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


class ContextUsageCategory(TypedDict):
    """A single context usage category (system prompt, tools, messages, etc.)."""

    name: str
    tokens: int
    color: str
    isDeferred: NotRequired[bool]


class ContextUsageResponse(TypedDict):
    """Response from `ClaudeSDKClient.get_context_usage()`.

    Provides a breakdown of current context window usage by category,
    matching the data shown by the `/context` command in the CLI.
    """

    categories: list[ContextUsageCategory]
    """Token usage broken down by category (system prompt, tools, messages, etc.)."""

    totalTokens: int
    """Total tokens currently in the context window."""

    maxTokens: int
    """Effective maximum tokens (may be reduced by autocompact buffer)."""

    rawMaxTokens: int
    """Raw model context window size."""

    percentage: float
    """Percentage of context window used (0-100)."""

    model: str
    """Model name the context usage is calculated for."""

    isAutoCompactEnabled: bool
    """Whether autocompact is enabled for this session."""

    memoryFiles: list[dict[str, Any]]
    """CLAUDE.md and memory files loaded, with path, type, and token counts."""

    mcpTools: list[dict[str, Any]]
    """MCP tools with name, serverName, tokens, and isLoaded status."""

    agents: list[dict[str, Any]]
    """Agent definitions with agentType, source, and token counts."""

    gridRows: list[list[dict[str, Any]]]
    """Visual grid representation used by the CLI context display."""

    autoCompactThreshold: NotRequired[int]
    """Token threshold at which autocompact triggers."""

    deferredBuiltinTools: NotRequired[list[dict[str, Any]]]
    """Built-in tools deferred from the initial tool list."""

    systemTools: NotRequired[list[dict[str, Any]]]
    """System (built-in) tools with name and token counts."""

    systemPromptSections: NotRequired[list[dict[str, Any]]]
    """System prompt sections with name and token counts."""

    slashCommands: NotRequired[dict[str, Any]]
    """Slash command usage summary."""

    skills: NotRequired[dict[str, Any]]
    """Skill usage summary with frontmatter breakdown."""

    messageBreakdown: NotRequired[dict[str, Any]]
    """Detailed breakdown of message tokens by type (tool calls, results, etc.)."""

    apiUsage: NotRequired[dict[str, Any] | None]
    """Cumulative API usage for the session."""


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
        allowedDomains: Domain names that sandboxed processes can access.
        deniedDomains: Domains that are always blocked, even if matched by allowedDomains.
        allowManagedDomainsOnly: When true in managed settings, only managed-settings allowedDomains are respected.
        allowUnixSockets: Unix socket paths accessible in sandbox (e.g., SSH agents).
        allowAllUnixSockets: Allow all Unix sockets (less secure).
        allowLocalBinding: Allow binding to localhost ports (macOS only).
        allowMachLookup: macOS only: XPC/Mach service names to allow (supports trailing wildcard).
        httpProxyPort: HTTP proxy port if bringing your own proxy.
        socksProxyPort: SOCKS5 proxy port if bringing your own proxy.
    """

    allowedDomains: list[str]
    deniedDomains: list[str]
    allowManagedDomainsOnly: bool
    allowUnixSockets: list[str]
    allowAllUnixSockets: bool
    allowLocalBinding: bool
    allowMachLookup: list[str]
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


ServerToolName = Literal[
    "advisor",
    "web_search",
    "web_fetch",
    "code_execution",
    "bash_code_execution",
    "text_editor_code_execution",
    "tool_search_tool_regex",
    "tool_search_tool_bm25",
]


@dataclass
class ServerToolUseBlock:
    """Server-side tool use block (e.g. advisor, web_search, web_fetch).

    These are tools the API executes server-side on the model's behalf, so they
    appear in the message stream alongside regular `tool_use` blocks but the
    caller never needs to return a result. `name` is a discriminator — branch
    on it to know which server tool was invoked.
    """

    id: str
    name: ServerToolName
    input: dict[str, Any]


@dataclass
class ServerToolResultBlock:
    """Result block returned for a server-side tool call.

    Mirrors `ToolResultBlock`'s shape. `content` is the raw dict from the
    API, opaque to this layer — callers that care about a specific server
    tool's result schema can inspect `content["type"]`.
    """

    tool_use_id: str
    content: dict[str, Any]


ContentBlock = (
    TextBlock
    | ThinkingBlock
    | ToolUseBlock
    | ToolResultBlock
    | ServerToolUseBlock
    | ServerToolResultBlock
)


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
    usage: dict[str, Any] | None = None
    message_id: str | None = None
    stop_reason: str | None = None
    session_id: str | None = None
    uuid: str | None = None


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
class MirrorErrorMessage(SystemMessage):
    """System message emitted when a :meth:`SessionStore.append` call fails.

    Non-fatal — the local-disk transcript is already durable, so the session
    continues unaffected. The mirrored copy in the external store will be
    missing the failed batch.

    Subclass of SystemMessage: existing ``isinstance(msg, SystemMessage)`` and
    ``case SystemMessage()`` checks continue to match. The base ``subtype``
    field is ``"mirror_error"`` and ``data`` carries the raw payload.
    """

    key: "SessionKey | None" = None
    error: str = ""


@dataclass
class DeferredToolUse:
    """Tool use that was deferred by a PreToolUse hook returning ``"defer"``.

    When a PreToolUse hook returns ``permissionDecision: "defer"``, the run
    stops and the result message carries the deferred tool call here so the
    caller can inspect it and decide whether to resume.
    """

    id: str
    name: str
    input: dict[str, Any]


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
    model_usage: dict[str, Any] | None = None
    permission_denials: list[Any] | None = None
    deferred_tool_use: DeferredToolUse | None = None
    errors: list[str] | None = None
    # HTTP status code (e.g. 429, 500, 529) of the failing API call when
    # ``is_error`` is True and ``subtype`` is "success"; None otherwise.
    # Emitted by the CLI since v2.1.110. Safe to log (no message content).
    api_error_status: int | None = None
    uuid: str | None = None


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


@dataclass
class HookEventMessage(SystemMessage):
    """Hook event emitted by the CLI when ``include_hook_events`` is enabled.

    When ``ClaudeAgentOptions.include_hook_events`` is ``True``, the CLI emits
    hook lifecycle events (PreToolUse, PostToolUse, Stop, etc.) into the
    message stream. Each event is identified by ``hook_event_name`` and the
    full raw payload is available in ``data``.

    These arrive on the wire as ``{"type": "system", "subtype":
    "hook_started" | "hook_response", "hook_event": "PreToolUse", ...}``.

    Subclass of SystemMessage: existing ``isinstance(msg, SystemMessage)`` and
    ``case SystemMessage()`` checks continue to match. The base ``subtype``
    and ``data`` fields remain populated with the raw payload.

    Attributes:
        subtype: Lifecycle phase — ``"hook_started"`` when a hook begins
            executing, ``"hook_response"`` when it completes (the latter
            carries ``output``, ``exit_code``, and ``outcome`` keys in
            ``data``).
        hook_event_name: Name of the hook event (e.g. ``"PreToolUse"``,
            ``"PostToolUse"``, ``"Stop"``).
        data: Full raw event dict from the CLI, including any
            event-specific fields not modeled here.
        session_id: Session ID the event belongs to, if present.
        uuid: Unique ID of the event, if present.
    """

    hook_event_name: str = ""
    session_id: str | None = None
    uuid: str | None = None


Message = (
    UserMessage
    | AssistantMessage
    | SystemMessage
    | ResultMessage
    | StreamEvent
    | RateLimitEvent
)


# ---------------------------------------------------------------------------
# Session Store Types
# ---------------------------------------------------------------------------


class SessionKey(TypedDict):
    """Identifies a session transcript or subagent transcript in a store.

    Main transcripts have no ``subpath``; subagent transcripts include a
    ``subpath`` like ``"subagents/agent-{id}"`` that mirrors the on-disk
    directory structure.
    """

    project_key: str
    """Caller-defined scope. Default: sanitized cwd. Multi-tenant deployments
    should set this to a tenant ID or project name. Paths longer than 200
    characters are truncated and suffixed with a portable djb2 hash so the
    same path yields the same key across runtimes."""

    session_id: str

    subpath: NotRequired[str]
    """Omit for the main transcript; set for subagent files. Empty string is
    invalid — omit the field for the main transcript. Opaque to the adapter —
    just use it as a storage key suffix."""


class SessionStoreEntry(TypedDict, total=False):
    """One JSONL transcript line as observed by a :class:`SessionStore` adapter.

    The concrete shape is the CLI's on-disk transcript format (a large
    discriminated union). That union is internal, so this is a minimal
    structural supertype — adapters should treat entries as pass-through
    blobs; round-tripping ``json.dumps``/``json.loads`` is the only
    required invariant.
    """

    type: Required[str]
    uuid: str
    timestamp: str
    # Additional fields are opaque JSON — adapters must pass them through.


class SessionStoreListEntry(TypedDict):
    """Entry returned by :meth:`SessionStore.list_sessions`."""

    session_id: str
    mtime: int
    """Last-modified time in Unix epoch milliseconds. Adapters without native
    modification time (e.g. Redis) must maintain their own index."""


class SessionSummaryEntry(TypedDict):
    """Incrementally-maintained session summary.

    Stores obtain this from :func:`fold_session_summary` inside
    :meth:`SessionStore.append` and persist it verbatim; they return the
    full set from :meth:`SessionStore.list_session_summaries`. The ``data``
    field is opaque SDK-owned state — stores MUST NOT interpret it.
    """

    session_id: str
    mtime: int
    """Storage write time of the sidecar, in Unix epoch milliseconds. Must use
    the same clock source as the ``mtime`` returned by
    :meth:`SessionStore.list_sessions` for this session — typically file
    mtime, S3 ``LastModified``, Postgres ``updated_at``, or whatever native
    timestamp the adapter surfaces. Do NOT derive this from entry ISO
    timestamps: adapters that write in batches with any persist latency
    (every real backend) would report storage times strictly later than the
    last entry's timestamp, making every sidecar appear stale and defeating
    the fast-path staleness check in ``list_sessions_from_store``.
    :func:`fold_session_summary` preserves whatever ``mtime`` the caller
    passes in via ``prev`` and does not set it itself; stamp it after
    persisting."""
    data: dict[str, Any]
    """Opaque SDK-owned summary state. Persist verbatim; do not interpret."""


class SessionListSubkeysKey(TypedDict):
    """Key argument to :meth:`SessionStore.list_subkeys` (no ``subpath``)."""

    project_key: str
    session_id: str


SessionStoreFlushMode = Literal["batched", "eager"]
"""Controls when transcript-mirror entries are flushed to a :class:`SessionStore`.

- ``"batched"`` (default): buffer entries and flush once per turn (on the
  ``result`` message) or when the pending buffer exceeds 500 entries / 1 MiB.
  Keeps adapter latency off the streaming hot path.
- ``"eager"``: trigger a background flush after every ``transcript_mirror``
  frame so ``SessionStore.append()`` sees entries in near real time. Appends
  are still serialized in enqueue order; a slow adapter will not stall the
  read loop but will see frames coalesced while it is busy.
"""


class SessionStore(Protocol):
    """Adapter for mirroring session transcripts to external storage.

    The subprocess still writes to local disk (set ``CLAUDE_CONFIG_DIR=/tmp``
    for an ephemeral local copy); the adapter receives a secondary copy.

    The SDK never deletes from your store unless you call
    ``delete_session_via_store()`` with :meth:`delete` implemented. Retention is
    the adapter's responsibility —
    implement TTL, object-storage lifecycle policies, or scheduled cleanup
    according to your compliance requirements (e.g. ZDR/HIPAA retention
    windows). Local-disk transcripts under ``CLAUDE_CONFIG_DIR`` are swept by
    the existing ``cleanupPeriodDays`` setting independently of this adapter.

    Only :meth:`append` and :meth:`load` are required. The remaining methods
    are optional: implementers may omit them, and call sites probe for their
    presence at runtime before invoking (the SDK never uses ``isinstance`` for
    this — a duck-typed adapter need not subclass ``SessionStore``). The
    default implementations on this Protocol raise :class:`NotImplementedError`
    so subclasses can inherit them as "absent" markers.
    """

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        """Mirror a batch of transcript entries.

        Called AFTER the subprocess's local write succeeds — durability is
        already guaranteed locally.

        Batches arrive at ~100ms cadence during active turns. Entries are
        JSON-safe plain objects — one per line in the local JSONL file.

        Within a single process, persist entries in append-call order; across
        concurrent processes, order is by storage commit time, not call time.

        Most entries carry a stable ``uuid`` that adapters should treat as an
        idempotency key (upsert / ignore-duplicate). Entries without a
        ``uuid`` (e.g. titles, tags, mode markers) should be appended without
        dedup. Exceptions are logged and the subprocess continues unaffected
        — failed batches are retried (3 attempts total) with short backoff
        before being dropped and surfaced as a ``MirrorErrorMessage``;
        timeouts are not retried since the in-flight call may still land.
        """
        ...

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        """Load a full session for resume.

        Called once, in the SDK parent, before subprocess spawn. The result is
        materialized to a temporary JSONL file; the subprocess resumes from
        that file using its existing resume code.

        Return ``None`` for a key that was never written; adapters that cannot
        distinguish "never written" from "emptied" (e.g. Redis ``LRANGE``) may
        return ``None`` for both. Returned entries must be deep-equal to what
        was appended — byte-equal serialization is NOT required (e.g. Postgres
        ``JSONB`` may reorder object keys); the SDK never hashes or
        byte-compares entries.
        """
        ...

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        """List sessions for a ``project_key``. Returns IDs + modification times.

        ``mtime`` is Unix epoch milliseconds; adapters without native
        modification time (e.g. Redis) must maintain their own index. Result
        order is unspecified — the SDK sorts by ``mtime`` descending.

        Optional — if unimplemented, ``list_sessions()`` with a session store
        raises.
        """
        raise NotImplementedError

    async def list_session_summaries(
        self, project_key: str
    ) -> list[SessionSummaryEntry]:
        """Return incrementally-maintained summaries for all sessions in one call.

        Stores should maintain these via :func:`fold_session_summary` inside
        :meth:`append`. Skip the fold for keys with a ``subpath`` — subagent
        transcripts must not contribute to the main session's summary.

        Like :meth:`list_sessions`, results are scoped to a single
        ``project_key`` and exclude ``subpath`` entries.

        Optional — if unimplemented, ``list_sessions_from_store()`` falls back
        to ``list_sessions()`` + per-session ``load()``.

        .. note::
            Stores that maintain summaries inside ``append()`` MUST serialize
            sidecar writes if ``append()`` calls can race for the same session
            — e.g., wrap the read-fold-write in a transaction/CAS, or hold a
            per-session lock. The SDK's :func:`fold_session_summary` is pure;
            concurrency control is the store's responsibility.
        """
        raise NotImplementedError

    async def delete(self, key: SessionKey) -> None:
        """Delete a session.

        Deleting a main-transcript key (no ``subpath``) must cascade to all
        subkeys under that session so subagent transcripts aren't orphaned. A
        targeted delete with an explicit ``subpath`` removes only that one
        entry.

        Optional — if unimplemented, deletion is a no-op (appropriate for
        WORM/append-only backends like object storage).
        """
        raise NotImplementedError

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        """List all subpath keys under a session (e.g. subagent transcripts).

        Used during resume to discover and materialize all subagent data.

        Optional — if unimplemented, resume only materializes the main
        transcript.
        """
        raise NotImplementedError


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
        file_size: Session file size in bytes. Only populated for local
            JSONL storage; may be ``None`` for remote storage backends.
        custom_title: Session title — user-set custom title or AI-generated title.
        first_prompt: First meaningful user prompt in the session.
        git_branch: Git branch at the end of the session.
        cwd: Working directory for the session.
        tag: User-set session tag.
        created_at: Creation time in milliseconds since epoch, extracted
            from the first entry's ISO timestamp field. More reliable
            than stat().birthtime which is unsupported on some filesystems.
    """

    session_id: str
    summary: str
    last_modified: int
    file_size: int | None = None
    custom_title: str | None = None
    first_prompt: str | None = None
    git_branch: str | None = None
    cwd: str | None = None
    tag: str | None = None
    created_at: int | None = None


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


# Controls whether thinking text is returned summarized or omitted. Opus 4.7+
# defaults to "omitted" (signature-only); pass "summarized" to receive text.
ThinkingDisplay = Literal["summarized", "omitted"]


class ThinkingConfigAdaptive(TypedDict):
    type: Literal["adaptive"]
    display: NotRequired[ThinkingDisplay]


class ThinkingConfigEnabled(TypedDict):
    type: Literal["enabled"]
    budget_tokens: int
    display: NotRequired[ThinkingDisplay]


class ThinkingConfigDisabled(TypedDict):
    type: Literal["disabled"]


ThinkingConfig = ThinkingConfigAdaptive | ThinkingConfigEnabled | ThinkingConfigDisabled


@dataclass
class ClaudeAgentOptions:
    """Query options for Claude SDK."""

    tools: list[str] | ToolsPreset | None = None
    """Specify the base set of available built-in tools.

    - ``list[str]`` — Specific tool names (e.g. ``["Bash", "Read", "Edit"]``).
    - ``[]`` (empty list) — Disable all built-in tools.
    - ``{"type": "preset", "preset": "claude_code"}`` — Use all default Claude Code tools.

    To restrict which tools the model may call without being prompted, use
    ``allowed_tools`` instead.
    """

    allowed_tools: list[str] = field(default_factory=list)
    """Tool names that are auto-allowed without prompting for permission.

    These tools execute automatically without asking the user for approval.
    To restrict which tools are available at all, use ``tools``.

    .. deprecated::
        Passing ``"Skill"`` here is deprecated. Use the :attr:`skills` option
        instead, which configures everything needed (including allowing the
        ``Skill`` tool).
    """

    system_prompt: str | SystemPromptPreset | SystemPromptFile | None = None
    """System prompt configuration.

    - ``str`` — Use a custom system prompt.
    - ``{"type": "preset", "preset": "claude_code"}`` — Use Claude Code's default
      system prompt.
    - ``{"type": "preset", "preset": "claude_code", "append": "..."}`` — Default
      prompt with appended instructions.
    """

    mcp_servers: dict[str, McpServerConfig] | str | Path = field(default_factory=dict)
    """MCP (Model Context Protocol) server configurations.

    Keys are server names, values are server configurations. May also be a path
    to an MCP config JSON file.
    """

    strict_mcp_config: bool = False
    """When ``True``, only use MCP servers passed via :attr:`mcp_servers`,
    ignoring all other MCP configurations the CLI would otherwise load (e.g.
    project ``.mcp.json``, user/global settings, plugin-provided servers).
    Maps to the CLI's ``--strict-mcp-config`` flag and matches the TypeScript
    SDK's ``strictMcpConfig`` option."""

    permission_mode: PermissionMode | None = None
    """Permission mode for the session.

    - ``"default"`` — Standard permission behavior; prompts for dangerous operations.
    - ``"acceptEdits"`` — Auto-accept file edit operations.
    - ``"bypassPermissions"`` — Bypass all permission checks.
    - ``"plan"`` — Planning mode, no execution of tools.
    - ``"dontAsk"`` — Don't prompt for permissions; deny if not pre-approved.
    """

    continue_conversation: bool = False
    """Continue the most recent conversation in the current directory instead of
    starting a new one. Mutually exclusive with ``resume``."""

    resume: str | None = None
    """Session ID to resume. Loads the conversation history from the specified session."""

    session_id: str | None = None
    """Use a specific session ID for the conversation instead of an auto-generated one.

    Must be a valid UUID. Cannot be used with ``continue_conversation`` or
    ``resume`` unless ``fork_session`` is also set.
    """

    max_turns: int | None = None
    """Maximum number of conversation turns before the query stops.

    A turn consists of a user message and assistant response.
    """

    max_budget_usd: float | None = None
    """Maximum budget in USD for the query.

    The query will stop if this budget is exceeded, returning an
    ``error_max_budget_usd`` result.
    """

    disallowed_tools: list[str] = field(default_factory=list)
    """Tool names that are disallowed.

    These tools are removed from the model's context and cannot be used, even
    if they would otherwise be allowed.
    """

    model: str | None = None
    """Claude model to use. Defaults to the CLI default model.

    Examples: ``"claude-sonnet-4-5"``, ``"claude-opus-4-5"``.
    """

    fallback_model: str | None = None
    """Fallback model to use if the primary model fails or is unavailable."""

    betas: list[SdkBeta] = field(default_factory=list)
    """Enable beta features.

    Currently supported:

    - ``"context-1m-2025-08-07"`` — Enable 1M token context window (Sonnet 4/4.5 only).

    See https://docs.anthropic.com/en/api/beta-headers.
    """

    permission_prompt_tool_name: str | None = None
    """MCP tool name to use for permission prompts.

    When set, permission requests are routed through this MCP tool instead of
    the default handler.
    """

    cwd: str | Path | None = None
    """Current working directory for the session. Defaults to the process cwd."""

    cli_path: str | Path | None = None
    """Path to the Claude Code CLI executable.

    Uses the bundled executable if not specified.
    """

    settings: str | None = None
    """Path to an additional settings JSON file to load.

    These are loaded into the "flag settings" layer, which has the highest
    priority among user-controlled settings. Equivalent to the ``--settings``
    CLI flag.
    """

    add_dirs: list[str | Path] = field(default_factory=list)
    """Additional directories Claude can access beyond the current working directory.

    Paths should be absolute.
    """

    env: dict[str, str] = field(default_factory=dict)
    """Environment variables to pass to the Claude Code subprocess.

    SDK consumers can identify their app/library in the User-Agent header by
    setting ``CLAUDE_AGENT_SDK_CLIENT_APP`` (e.g. ``"my-app/1.0.0"``).
    """

    extra_args: dict[str, str | None] = field(default_factory=dict)
    """Additional CLI arguments to pass to Claude Code.

    Keys are argument names (without ``--``), values are argument values. Use
    ``None`` for boolean flags.
    """

    max_buffer_size: int | None = None
    """Maximum bytes to buffer when reading the CLI subprocess stdout."""

    debug_stderr: Any = sys.stderr
    """Deprecated and no longer read by the transport. Use the ``stderr`` callback."""

    stderr: Callable[[str], None] | None = None
    """Callback for stderr output from the Claude Code subprocess.

    Useful for debugging and logging.
    """

    can_use_tool: CanUseTool | None = None
    """Custom permission handler for tool calls that would otherwise prompt the user.

    Invoked when the CLI's permission rules evaluate to "ask" for a tool call —
    it is the SDK replacement for the interactive permission prompt. It is *not*
    invoked for tool calls already permitted by ``allowed_tools``,
    ``permission_mode`` (e.g. ``"acceptEdits"`` / ``"bypassPermissions"``), or
    ``permissions.allow`` rules in settings, since those never reach a prompt.
    To observe or gate *every* tool call regardless of permission rules, use a
    ``PreToolUse`` hook via ``hooks`` instead.
    """

    hooks: dict[HookEvent, list[HookMatcher]] | None = None
    """Hook callbacks for responding to various events during execution.

    Hooks can modify behavior, add context, or implement custom logic. See
    https://docs.anthropic.com/en/docs/claude-code/hooks.
    """

    user: str | None = None
    """Optional user identifier associated with the session."""

    include_partial_messages: bool = False
    """Include partial/streaming message events in the output.

    When true, ``SDKPartialAssistantMessage`` events are emitted during streaming.
    """

    include_hook_events: bool = False
    """Include hook lifecycle events in the message stream.

    When true, the CLI emits hook events (PreToolUse, PostToolUse, Stop,
    etc.) as ``HookEventMessage`` objects in the message stream. Matches the
    TypeScript SDK's ``includeHookEvents``.
    """

    fork_session: bool = False
    """When true, resumed sessions fork to a new session ID rather than
    continuing the previous session. Use with ``resume``."""

    agents: dict[str, AgentDefinition] | None = None
    """Programmatically define custom subagents invokable via the Agent tool.

    Keys are agent names, values are agent definitions.
    """

    setting_sources: list[SettingSource] | None = None
    """Control which filesystem settings to load.

    - ``"user"`` — Global user settings (``~/.claude/settings.json``).
    - ``"project"`` — Project settings (``.claude/settings.json``).
    - ``"local"`` — Local settings (``.claude/settings.local.json``).

    When ``None``, all sources are loaded (matches CLI defaults). Pass ``[]``
    to disable filesystem settings (SDK isolation mode). Must include
    ``"project"`` to load CLAUDE.md files.
    """

    skills: list[str] | Literal["all"] | None = None
    """Skills to enable for the main session.

    This is the single place to turn skills on; you do not need to add
    ``"Skill"`` to ``allowed_tools`` or set ``setting_sources`` yourself — the
    SDK does both when this is set.

    - ``None`` (default): no SDK auto-configuration. The CLI's own defaults
      still apply, so this is **not** "skills off" — to suppress every skill
      from the listing, use ``[]``.
    - ``"all"``: enable every discovered skill.
    - ``list[str]``: enable only the listed skills. Names match the SKILL.md
      ``name`` / directory name, or ``plugin:skill`` for plugin-qualified skills.

    This is a **context filter**, not a sandbox: unlisted skills are hidden
    from the model's listing and rejected by the Skill tool, but their files
    remain on disk and are reachable via Read/Bash. Do not store secrets in
    skill files.
    """

    sandbox: SandboxSettings | None = None
    """Sandbox settings for command execution isolation.

    When enabled, commands execute in a sandboxed environment that restricts
    filesystem and network access. Filesystem and network restrictions are
    configured via permission rules (Read/Edit for filesystem, WebFetch for
    network), not via these sandbox settings — sandbox settings control
    sandbox behavior (enabled, auto-allow, etc.).

    See https://docs.anthropic.com/en/docs/claude-code/settings#sandbox-settings.
    """

    plugins: list[SdkPluginConfig] = field(default_factory=list)
    """Load plugins for this session.

    Plugins provide custom commands, agents, skills, and hooks that extend
    Claude Code's capabilities. Currently only local plugins are supported.
    """

    max_thinking_tokens: int | None = None
    """Maximum tokens the model may use for its thinking/reasoning process.

    .. deprecated::
       Use ``thinking`` instead. On newer models, this is treated as on/off
       (0 = disabled, any other value = adaptive). For explicit control, use
       ``thinking={"type": "adaptive"}`` or
       ``thinking={"type": "enabled", "budget_tokens": N}``.
    """

    thinking: ThinkingConfig | None = None
    """Controls Claude's thinking/reasoning behavior.

    - ``{"type": "adaptive"}`` — Claude decides when and how much to think
      (Opus 4.6+). Default for models that support it.
    - ``{"type": "enabled", "budget_tokens": N}`` — Fixed thinking token budget
      (older models).
    - ``{"type": "disabled"}`` — No extended thinking.

    When set, takes precedence over the deprecated ``max_thinking_tokens``.
    See https://docs.anthropic.com/en/docs/build-with-claude/adaptive-thinking.
    """

    effort: EffortLevel | None = None
    """Controls how much effort Claude puts into its response.

    Works with adaptive thinking to guide thinking depth.

    - ``"low"`` — Minimal thinking, fastest responses.
    - ``"medium"`` — Moderate thinking.
    - ``"high"`` — Deep reasoning (default).
    - ``"xhigh"`` — Extended reasoning depth (Opus 4.7 only; falls back to
      ``"high"`` on other models).
    - ``"max"`` — Maximum effort.

    See https://docs.anthropic.com/en/docs/build-with-claude/effort.
    """

    output_format: dict[str, Any] | None = None
    """Output format configuration for structured responses.

    When specified, the agent returns structured data matching the schema.
    Matches the Messages API structure, e.g.
    ``{"type": "json_schema", "schema": {"type": "object", "properties": {...}}}``.
    """

    enable_file_checkpointing: bool = False
    """Enable file checkpointing to track file changes during the session.

    When enabled, files can be rewound to their state at any user message
    using ``ClaudeSDKClient.rewind_files()``. File checkpointing creates
    backups of files before they are modified so they can be restored later.
    """

    session_store: SessionStore | None = None
    """Mirror session transcripts to an external store.

    When set, every transcript line written locally is also passed to
    ``session_store.append()``, and ``resume`` can materialize from the store
    when the local file is absent.
    """

    session_store_flush: SessionStoreFlushMode = "batched"
    """When to flush mirrored transcript entries to ``session_store``.

    ``"batched"`` (default) coalesces entries and flushes once per turn or when
    the buffer exceeds 500 entries / 1 MiB. ``"eager"`` triggers a background
    flush after every frame for near-real-time delivery (each flush still runs
    off the read loop, so a slow adapter does not stall message streaming).
    Ignored when ``session_store`` is ``None``.
    """

    load_timeout_ms: int = 60_000
    """Timeout for each ``session_store.load()`` / ``list_subkeys()`` call during
    resume materialization, in milliseconds.

    If the adapter doesn't settle within this window the query fails with a
    clear error instead of hanging the iterator forever. A value of 0 means
    immediate timeout; use a large value to effectively disable.
    """

    task_budget: TaskBudget | None = None
    """API-side task budget in tokens.

    When set, the model is made aware of its remaining token budget so it can
    pace tool use and wrap up before the limit. Sent as
    ``output_config.task_budget`` with the ``task-budgets-2026-03-13`` beta
    header.
    """


# SDK Control Protocol
class SDKControlInterruptRequest(TypedDict):
    subtype: Literal["interrupt"]


class SDKControlPermissionRequest(TypedDict):
    subtype: Literal["can_use_tool"]
    tool_name: str
    input: dict[str, Any]
    permission_suggestions: list[dict[str, Any]] | None
    blocked_path: str | None
    decision_reason: NotRequired[str]
    title: NotRequired[str]
    display_name: NotRequired[str]
    description: NotRequired[str]
    tool_use_id: str
    agent_id: NotRequired[str]


class SDKControlInitializeRequest(TypedDict):
    subtype: Literal["initialize"]
    hooks: dict[HookEvent, Any] | None
    agents: NotRequired[dict[str, dict[str, Any]]]


class SDKControlSetPermissionModeRequest(TypedDict):
    subtype: Literal["set_permission_mode"]
    mode: PermissionMode


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
