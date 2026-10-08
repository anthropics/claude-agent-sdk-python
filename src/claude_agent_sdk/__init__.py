"""Claude SDK for Python."""

import logging
import sys
import types as builtin_types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

if sys.version_info >= (3, 11):
    from typing import get_type_hints as _get_type_hints
    from typing import is_typeddict
else:
    # On 3.10, stdlib is_typeddict doesn't recognize typing_extensions.TypedDict
    # subclasses, and stdlib get_type_hints doesn't strip NotRequired markers.
    from typing_extensions import get_type_hints as _get_type_hints
    from typing_extensions import is_typeddict

import jsonschema
from mcp.types import CallToolResult, Tool
from mcp.types import ToolAnnotations as _McpToolAnnotations

from ._errors import (
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultError,
)
from ._internal._mcp_compat import build_tool_server
from ._internal.session_import import import_session_to_store
from ._internal.session_mutations import (
    ForkSessionResult,
    delete_session,
    delete_session_via_store,
    fork_session,
    fork_session_via_store,
    rename_session,
    rename_session_via_store,
    tag_session,
    tag_session_via_store,
)
from ._internal.session_store import InMemorySessionStore, project_key_for_directory
from ._internal.session_summary import fold_session_summary
from ._internal.sessions import (
    get_session_info,
    get_session_info_from_store,
    get_session_messages,
    get_session_messages_from_store,
    get_subagent_messages,
    get_subagent_messages_from_store,
    list_sessions,
    list_sessions_from_store,
    list_subagents,
    list_subagents_from_store,
)
from ._internal.transport import Transport
from ._version import __version__
from .client import ClaudeSDKClient
from .query import query
from .types import (
    TERMINAL_TASK_STATUSES,
    AgentDefinition,
    AssistantMessage,
    BaseHookInput,
    CanUseTool,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ContentBlock,
    ContextUsageCategory,
    ContextUsageResponse,
    ConversationResetMessage,
    DeferredToolUse,
    EffortLevel,
    HookCallback,
    HookContext,
    HookEventMessage,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    McpSdkServerConfig,
    McpServerConfig,
    McpServerConnectionStatus,
    McpServerInfo,
    McpServerStatus,
    McpServerStatusConfig,
    McpStatusResponse,
    McpToolAnnotations,
    McpToolInfo,
    Message,
    MessageOrigin,
    MessageOriginKind,
    MirrorErrorMessage,
    ModelUsage,
    NotificationHookInput,
    NotificationHookSpecificOutput,
    PermissionMode,
    PermissionRequestHookInput,
    PermissionRequestHookSpecificOutput,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    PostToolUseFailureHookInput,
    PostToolUseFailureHookSpecificOutput,
    PostToolUseHookInput,
    PreCompactHookInput,
    PreToolUseHookInput,
    RateLimitEvent,
    RateLimitInfo,
    RateLimitStatus,
    RateLimitType,
    ResultMessage,
    SandboxIgnoreViolations,
    SandboxNetworkConfig,
    SandboxSettings,
    SdkBeta,
    SdkPluginConfig,
    SDKSessionInfo,
    ServerToolName,
    ServerToolResultBlock,
    ServerToolUseBlock,
    SessionKey,
    SessionListSubkeysKey,
    SessionMessage,
    SessionStore,
    SessionStoreEntry,
    SessionStoreFlushMode,
    SessionStoreListEntry,
    SessionSummaryEntry,
    SettingSource,
    StopHookInput,
    StreamEvent,
    SubagentStartHookInput,
    SubagentStartHookSpecificOutput,
    SubagentStopHookInput,
    SystemMessage,
    TaskBudget,
    TaskNotificationMessage,
    TaskNotificationOriginSubkind,
    TaskNotificationStatus,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TaskUpdatedStatus,
    TaskUsage,
    TextBlock,
    ThinkingBlock,
    ThinkingConfig,
    ThinkingConfigAdaptive,
    ThinkingConfigDisabled,
    ThinkingConfigEnabled,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    UserPromptSubmitHookInput,
)

# MCP Server Support

logger = logging.getLogger(__name__)

T = TypeVar("T")


# The snake_case spelling of each hint, mapped to its wire (camelCase) name.
_HINT_WIRE_NAMES = {
    "read_only_hint": "readOnlyHint",
    "destructive_hint": "destructiveHint",
    "idempotent_hint": "idempotentHint",
    "open_world_hint": "openWorldHint",
    "max_result_size_chars": "maxResultSizeChars",
}


class ToolAnnotations(_McpToolAnnotations, extra="allow"):
    """Hints about a tool's behavior, plus ``maxResultSizeChars``.

    A ``mcp.types.ToolAnnotations`` that takes every hint in either spelling
    on every supported mcp version: ``ToolAnnotations(readOnlyHint=True)``
    and ``ToolAnnotations(read_only_hint=True)`` are the same thing, and both
    type-check. (mcp's own class differs between majors here: 1.x only knows
    the camelCase names and 2.x prefers snake_case.) Attribute access follows
    the installed mcp: ``.readOnlyHint`` on 1.x, ``.read_only_hint`` on 2.x.

    ``maxResultSizeChars`` (``max_result_size_chars``) is not an MCP hint but
    a Claude Code one: the size, in characters, up to which Claude Code keeps
    this tool's result inline instead of persisting it to a file and showing
    a preview. It travels to the CLI in the tool's ``_meta``.

    Either this class or a plain ``mcp.types.ToolAnnotations`` is accepted
    wherever the SDK takes annotations.
    """

    maxResultSizeChars: int | None = None  # noqa: N815 - the wire spelling

    if TYPE_CHECKING:

        def __init__(  # both spellings, deliberately
            self,
            *,
            title: str | None = None,
            readOnlyHint: bool | None = None,  # noqa: N803
            read_only_hint: bool | None = None,
            destructiveHint: bool | None = None,  # noqa: N803
            destructive_hint: bool | None = None,
            idempotentHint: bool | None = None,  # noqa: N803
            idempotent_hint: bool | None = None,
            openWorldHint: bool | None = None,  # noqa: N803
            open_world_hint: bool | None = None,
            maxResultSizeChars: int | None = None,  # noqa: N803
            max_result_size_chars: int | None = None,
            **extra: Any,
        ) -> None: ...

    else:

        def __init__(self, **data: Any) -> None:
            # Fold the snake_case spellings into the wire names, which every
            # mcp version accepts (as field names on 1.x, as aliases on 2.x).
            # Given both, the wire name wins.
            for snake, wire in _HINT_WIRE_NAMES.items():
                if snake in data:
                    value = data.pop(snake)
                    data.setdefault(wire, value)
            super().__init__(**data)


@dataclass
class SdkMcpTool(Generic[T]):
    """Definition for an SDK MCP tool."""

    name: str
    description: str
    input_schema: type[T] | dict[str, Any]
    handler: Callable[[T], Awaitable[dict[str, Any]]]
    annotations: _McpToolAnnotations | None = None


def tool(
    name: str,
    description: str,
    input_schema: type | dict[str, Any],
    annotations: _McpToolAnnotations | None = None,
) -> Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], SdkMcpTool[Any]]:
    """Decorator for defining MCP tools with type safety.

    Creates a tool that can be used with SDK MCP servers. The tool runs
    in-process within your Python application, providing better performance
    than external MCP servers.

    Args:
        name: Unique identifier for the tool. This is what Claude will use
            to reference the tool in function calls.
        description: Human-readable description of what the tool does.
            This helps Claude understand when to use the tool.
        input_schema: Schema defining the tool's input parameters.
            Can be either:
            - A dictionary mapping parameter names to types (e.g., {"text": str})
            - A TypedDict class for more complex schemas
            - A JSON Schema dictionary for full validation
            Use ``Annotated[type, "description"]`` to add a description to a
            parameter in either dict-style or TypedDict schemas.
        annotations: Optional MCP tool annotations (hints such as
            ``readOnlyHint`` or ``destructiveHint``) advertised to Claude.
            ``ToolAnnotations(maxResultSizeChars=N)`` additionally raises the
            size up to which Claude Code keeps this tool's result inline
            instead of persisting it to a file and showing a preview.

    Returns:
        A decorator function that wraps the tool implementation and returns
        an SdkMcpTool instance ready for use with create_sdk_mcp_server().

    Example:
        Basic tool with simple schema:
        >>> @tool("greet", "Greet a user", {"name": str})
        ... async def greet(args):
        ...     return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}

        Tool with multiple parameters:
        >>> @tool("add", "Add two numbers", {"a": float, "b": float})
        ... async def add_numbers(args):
        ...     result = args["a"] + args["b"]
        ...     return {"content": [{"type": "text", "text": f"Result: {result}"}]}

        Tool with error handling:
        >>> @tool("divide", "Divide two numbers", {"a": float, "b": float})
        ... async def divide(args):
        ...     if args["b"] == 0:
        ...         return {"content": [{"type": "text", "text": "Error: Division by zero"}], "is_error": True}
        ...     return {"content": [{"type": "text", "text": f"Result: {args['a'] / args['b']}"}]}

        Read-only tool that may return a large result:
        >>> @tool(
        ...     "get_schema",
        ...     "Return the database schema",
        ...     {},
        ...     annotations=ToolAnnotations(readOnlyHint=True, maxResultSizeChars=500_000),
        ... )
        ... async def get_schema(args):
        ...     return {"content": [{"type": "text", "text": load_schema()}]}

    Notes:
        - The tool function must be async (defined with async def)
        - The function receives a single dict argument with the input parameters
        - Arguments are validated against the input schema before the function
          is called; invalid input is reported back to Claude as an error result
        - The function should return a dict with a "content" key containing the response
        - Errors can be indicated by including "is_error": True in the response;
          an exception raised by the function is reported the same way
    """

    def decorator(
        handler: Callable[[Any], Awaitable[dict[str, Any]]],
    ) -> SdkMcpTool[Any]:
        return SdkMcpTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            annotations=annotations,
        )

    return decorator


def _python_type_to_json_schema(py_type: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema dict."""
    origin = get_origin(py_type)

    # NotRequired/Required/ReadOnly survive include_extras=True; unwrap them
    if getattr(origin, "_name", None) in ("NotRequired", "Required", "ReadOnly"):
        return _python_type_to_json_schema(get_args(py_type)[0])

    if origin is Annotated:
        args = get_args(py_type)
        schema = _python_type_to_json_schema(args[0])
        for meta in args[1:]:
            if isinstance(meta, str):
                schema["description"] = meta
                break
        return schema

    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}

    origin = getattr(py_type, "__origin__", None)

    if origin is Union or isinstance(py_type, builtin_types.UnionType):
        args = py_type.__args__
        non_none = [a for a in args if a is not builtin_types.NoneType]
        if len(non_none) == 1:
            return _python_type_to_json_schema(non_none[0])
        return {"anyOf": [_python_type_to_json_schema(a) for a in non_none]}

    if origin is list:
        item_args = getattr(py_type, "__args__", None)
        if item_args:
            return {"type": "array", "items": _python_type_to_json_schema(item_args[0])}
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}

    if py_type is list:
        return {"type": "array"}
    if py_type is dict:
        return {"type": "object"}

    if is_typeddict(py_type):
        return _typeddict_to_json_schema(py_type)

    return {"type": "string"}


def _typeddict_to_json_schema(td_class: type) -> dict[str, Any]:
    """Convert a TypedDict class to a JSON Schema dict."""
    hints = _get_type_hints(td_class, include_extras=True)

    properties: dict[str, Any] = {}
    for field_name, field_type in hints.items():
        properties[field_name] = _python_type_to_json_schema(field_type)

    required_keys = getattr(td_class, "__required_keys__", set(properties.keys()))
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required_keys:
        schema["required"] = sorted(required_keys)
    return schema


def _build_input_schema(tool_def: SdkMcpTool[Any]) -> dict[str, Any]:
    """Turn a tool's declared input_schema into the JSON Schema sent on the wire."""
    if isinstance(tool_def.input_schema, dict):
        if (
            "type" in tool_def.input_schema
            and "properties" in tool_def.input_schema
            and isinstance(tool_def.input_schema["type"], str)
        ):
            return tool_def.input_schema
        properties = {
            param_name: _python_type_to_json_schema(param_type)
            for param_name, param_type in tool_def.input_schema.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties.keys()),
        }
    if is_typeddict(tool_def.input_schema):
        return _typeddict_to_json_schema(tool_def.input_schema)
    return {"type": "object", "properties": {}}


def _build_meta(tool_def: SdkMcpTool[Any]) -> dict[str, Any] | None:
    # Client-specific hints travel in _meta under namespaced keys because MCP
    # clients drop annotation fields they do not know. maxResultSizeChars is
    # the size up to which Claude Code keeps a tool result inline rather than
    # persisting it and showing a preview; it rides on the annotations object
    # as an extra (or subclass-declared) field.
    max_size = getattr(tool_def.annotations, "maxResultSizeChars", None)
    if max_size is None:
        return None
    return {"anthropic/maxResultSizeChars": max_size}


def _tool_error_result(message: str) -> CallToolResult:
    return CallToolResult.model_validate(
        {"content": [{"type": "text", "text": message}], "isError": True}
    )


def _convert_tool_content(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map the content of a tool handler's result dict to MCP content blocks.

    Text and image blocks pass through. Resource links and text resources are
    flattened to text because that is what the CLI renders; binary resources
    and unknown block types are dropped with a warning.
    """
    content: list[dict[str, Any]] = []
    for item in items:
        item_type = item.get("type")
        if item_type == "text":
            content.append({"type": "text", "text": item["text"]})
        elif item_type == "image":
            content.append(
                {"type": "image", "data": item["data"], "mimeType": item["mimeType"]}
            )
        elif item_type == "resource_link":
            parts = [
                str(part)
                for part in (item.get("name"), item.get("uri"), item.get("description"))
                if part
            ]
            content.append(
                {"type": "text", "text": "\n".join(parts) if parts else "Resource link"}
            )
        elif item_type == "resource":
            resource = item.get("resource") or {}
            if "text" in resource:
                content.append({"type": "text", "text": resource["text"]})
            else:
                logger.warning(
                    "Binary embedded resource cannot be converted to text, skipping"
                )
        else:
            logger.warning(
                "Unsupported content type %r in tool result, skipping", item_type
            )
    return content


def create_sdk_mcp_server(
    name: str, version: str = "1.0.0", tools: list[SdkMcpTool[Any]] | None = None
) -> McpSdkServerConfig:
    """Create an in-process MCP server that runs within your Python application.

    Unlike external MCP servers that run as separate processes, SDK MCP servers
    run directly in your application's process. This provides:
    - Better performance (no IPC overhead)
    - Simpler deployment (single process)
    - Easier debugging (same process)
    - Direct access to your application's state

    Args:
        name: Unique identifier for the server. This name is used to reference
            the server in the mcp_servers configuration.
        version: Server version string. Defaults to "1.0.0". This is for
            informational purposes and doesn't affect functionality.
        tools: List of SdkMcpTool instances created with the @tool decorator.
            These are the functions that Claude can call through this server.
            If None or empty, the server will have no tools (rarely useful).

    Returns:
        McpSdkServerConfig: A configuration object that can be passed to
        ClaudeAgentOptions.mcp_servers. Its ``instance`` is a regular
        ``mcp.server.Server`` that the SDK connects to Claude Code over an
        in-memory MCP transport.

    Example:
        Simple calculator server:
        >>> @tool("add", "Add numbers", {"a": float, "b": float})
        ... async def add(args):
        ...     return {"content": [{"type": "text", "text": f"Sum: {args['a'] + args['b']}"}]}
        >>>
        >>> @tool("multiply", "Multiply numbers", {"a": float, "b": float})
        ... async def multiply(args):
        ...     return {"content": [{"type": "text", "text": f"Product: {args['a'] * args['b']}"}]}
        >>>
        >>> calculator = create_sdk_mcp_server(
        ...     name="calculator",
        ...     version="2.0.0",
        ...     tools=[add, multiply]
        ... )
        >>>
        >>> # Use with Claude
        >>> options = ClaudeAgentOptions(
        ...     mcp_servers={"calc": calculator},
        ...     allowed_tools=["add", "multiply"]
        ... )

        Server with application state access:
        >>> class DataStore:
        ...     def __init__(self):
        ...         self.items = []
        ...
        >>> store = DataStore()
        >>>
        >>> @tool("add_item", "Add item to store", {"item": str})
        ... async def add_item(args):
        ...     store.items.append(args["item"])
        ...     return {"content": [{"type": "text", "text": f"Added: {args['item']}"}]}
        >>>
        >>> server = create_sdk_mcp_server("store", tools=[add_item])

        Bringing your own server: any ``mcp.server.Server`` built with the
        installed ``mcp`` package can be used in place of this helper, and
        the requests it handles (tools, resources, prompts, ...) are served.
        Requests and notifications the server sends to the client (sampling,
        elicitation, roots, logging, progress) are not forwarded yet:
        >>> from mcp.server import Server
        >>> my_server = Server("mine")  # register handlers with the mcp API
        >>> config = McpSdkServerConfig(type="sdk", name="mine", instance=my_server)

    Notes:
        - The server runs in the same process as your Python application
        - Tools have direct access to your application's variables and state
        - No subprocess or IPC overhead for tool calls
        - Server lifecycle is managed automatically by the SDK
        - Tool arguments are validated against the tool's input schema, and
          unknown tools, invalid arguments and exceptions raised by a handler
          are all reported to Claude as error results rather than failing the
          request
        - Works with both mcp 1.x and mcp 2.x

    See Also:
        - tool(): Decorator for creating tool functions
        - ClaudeAgentOptions: Configuration for using servers with query()
    """
    tools = tools or []
    tools_by_name = {tool_def.name: tool_def for tool_def in tools}
    schemas = {tool_def.name: _build_input_schema(tool_def) for tool_def in tools}
    wire_tools = [
        Tool.model_validate(
            {
                "name": tool_def.name,
                "description": tool_def.description,
                "inputSchema": schemas[tool_def.name],
                "annotations": tool_def.annotations,
                "_meta": _build_meta(tool_def),
            }
        )
        for tool_def in tools
    ]

    async def run_tool(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        # The SDK owns these semantics so a tool behaves the same on every
        # supported mcp version: unknown tools, invalid arguments and handler
        # failures all come back as isError results the model can read, never
        # as protocol errors.
        tool_def = tools_by_name.get(tool_name)
        if tool_def is None:
            return _tool_error_result(f"Tool '{tool_name}' not found")
        try:
            try:
                jsonschema.validate(instance=arguments, schema=schemas[tool_name])
            except jsonschema.ValidationError as e:
                return _tool_error_result(f"Input validation error: {e.message}")
            result = await tool_def.handler(arguments)
            return CallToolResult.model_validate(
                {
                    "content": _convert_tool_content(result.get("content", [])),
                    "isError": result.get("is_error", False),
                }
            )
        except Exception as e:
            return _tool_error_result(str(e))

    server = build_tool_server(name, version, wire_tools, run_tool)

    return McpSdkServerConfig(type="sdk", name=name, instance=server)


__all__ = [
    # Main exports
    "query",
    "__version__",
    # Transport
    "Transport",
    "ClaudeSDKClient",
    # Types
    "PermissionMode",
    "EffortLevel",
    "McpServerConfig",
    "McpSdkServerConfig",
    "McpServerStatus",
    "McpServerStatusConfig",
    "McpServerConnectionStatus",
    "McpServerInfo",
    "McpStatusResponse",
    "McpToolAnnotations",
    "McpToolInfo",
    "UserMessage",
    "AssistantMessage",
    "SystemMessage",
    "TaskStartedMessage",
    "TaskProgressMessage",
    "TaskUpdatedMessage",
    "TaskNotificationMessage",
    "TaskNotificationStatus",
    "TaskUpdatedStatus",
    "TERMINAL_TASK_STATUSES",
    "TaskUsage",
    "ResultMessage",
    "ModelUsage",
    "DeferredToolUse",
    "RateLimitEvent",
    "RateLimitInfo",
    "RateLimitStatus",
    "RateLimitType",
    "StreamEvent",
    "ConversationResetMessage",
    "Message",
    "MessageOrigin",
    "MessageOriginKind",
    "TaskNotificationOriginSubkind",
    "ClaudeAgentOptions",
    "TaskBudget",
    "TextBlock",
    "ThinkingBlock",
    "ThinkingConfig",
    "ThinkingConfigAdaptive",
    "ThinkingConfigEnabled",
    "ThinkingConfigDisabled",
    "ToolUseBlock",
    "ToolResultBlock",
    "ServerToolName",
    "ServerToolUseBlock",
    "ServerToolResultBlock",
    "ContentBlock",
    "ContextUsageCategory",
    "ContextUsageResponse",
    # Tool callbacks
    "CanUseTool",
    "CanUseToolShadowedWarning",
    "ToolPermissionContext",
    "PermissionResult",
    "PermissionResultAllow",
    "PermissionResultDeny",
    "PermissionUpdate",
    # Hook support
    "HookCallback",
    "HookContext",
    "HookInput",
    "HookEventMessage",
    "BaseHookInput",
    "PreToolUseHookInput",
    "PostToolUseHookInput",
    "PostToolUseFailureHookInput",
    "PostToolUseFailureHookSpecificOutput",
    "UserPromptSubmitHookInput",
    "StopHookInput",
    "SubagentStopHookInput",
    "PreCompactHookInput",
    "NotificationHookInput",
    "SubagentStartHookInput",
    "PermissionRequestHookInput",
    "NotificationHookSpecificOutput",
    "SubagentStartHookSpecificOutput",
    "PermissionRequestHookSpecificOutput",
    "HookJSONOutput",
    "HookMatcher",
    # Agent support
    "AgentDefinition",
    "SettingSource",
    # Plugin support
    "SdkPluginConfig",
    # Session listing
    "list_sessions",
    "get_session_info",
    "get_session_messages",
    "list_subagents",
    "get_subagent_messages",
    "SDKSessionInfo",
    "SessionMessage",
    # Session store
    "SessionKey",
    "SessionStore",
    "SessionStoreEntry",
    "SessionStoreFlushMode",
    "SessionStoreListEntry",
    "SessionSummaryEntry",
    "SessionListSubkeysKey",
    "InMemorySessionStore",
    "fold_session_summary",
    "MirrorErrorMessage",
    "project_key_for_directory",
    "import_session_to_store",
    # Session listing (SessionStore-backed async variants)
    "list_sessions_from_store",
    "get_session_info_from_store",
    "get_session_messages_from_store",
    "list_subagents_from_store",
    "get_subagent_messages_from_store",
    # Session mutations
    "rename_session",
    "tag_session",
    "delete_session",
    "fork_session",
    "ForkSessionResult",
    # Session mutations (SessionStore-backed async variants)
    "rename_session_via_store",
    "tag_session_via_store",
    "delete_session_via_store",
    "fork_session_via_store",
    # Beta support
    "SdkBeta",
    # Sandbox support
    "SandboxSettings",
    "SandboxNetworkConfig",
    "SandboxIgnoreViolations",
    # MCP Server Support
    "create_sdk_mcp_server",
    "tool",
    "SdkMcpTool",
    "ToolAnnotations",
    # Errors
    "ClaudeSDKError",
    "CLIConnectionError",
    "CLINotFoundError",
    "ProcessError",
    "ResultError",
    "CLIJSONDecodeError",
]
