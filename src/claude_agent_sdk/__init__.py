"""Claude SDK for Python."""

import logging
import sys
import types as builtin_types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar, Union, get_args, get_origin

if sys.version_info >= (3, 11):
    from typing import get_type_hints as _get_type_hints
    from typing import is_typeddict
else:
    # On 3.10, stdlib is_typeddict doesn't recognize typing_extensions.TypedDict
    # subclasses, and stdlib get_type_hints doesn't strip NotRequired markers.
    from typing_extensions import get_type_hints as _get_type_hints
    from typing_extensions import is_typeddict

from mcp.types import ToolAnnotations

from ._errors import (
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
)
from ._internal.session_mutations import (
    ForkSessionResult,
    delete_session,
    fork_session,
    rename_session,
    tag_session,
)
from ._internal.sessions import get_session_info, get_session_messages, list_sessions
from ._internal.transport import Transport
from ._version import __version__
from .client import ClaudeSDKClient
from .query import query
from .types import (
    AgentDefinition,
    AssistantMessage,
    BaseHookInput,
    CanUseTool,
    ClaudeAgentOptions,
    ContentBlock,
    ContextUsageCategory,
    ContextUsageResponse,
    HookCallback,
    HookContext,
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
    SessionMessage,
    SettingSource,
    StopHookInput,
    StreamEvent,
    SubagentStartHookInput,
    SubagentStartHookSpecificOutput,
    SubagentStopHookInput,
    SystemMessage,
    TaskBudget,
    TaskNotificationMessage,
    TaskNotificationStatus,
    TaskProgressMessage,
    TaskStartedMessage,
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


@dataclass
class SdkMcpTool(Generic[T]):
    """Definition for an SDK MCP tool."""

    name: str
    description: str
    input_schema: type[T] | dict[str, Any]
    handler: Callable[[T], Awaitable[dict[str, Any]]]
    annotations: ToolAnnotations | None = None


def tool(
    name: str,
    description: str,
    input_schema: type | dict[str, Any],
    annotations: ToolAnnotations | None = None,
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

    Notes:
        - The tool function must be async (defined with async def)
        - The function receives a single dict argument with the input parameters
        - The function should return a dict with a "content" key containing the response
        - Errors can be indicated by including "is_error": True in the response
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
        ClaudeAgentOptions.mcp_servers. This config contains the server
        instance and metadata needed for the SDK to route tool calls.

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

    Notes:
        - The server runs in the same process as your Python application
        - Tools have direct access to your application's variables and state
        - No subprocess or IPC overhead for tool calls
        - Server lifecycle is managed automatically by the SDK

    See Also:
        - tool(): Decorator for creating tool functions
        - ClaudeAgentOptions: Configuration for using servers with query()
    """
    from mcp.server import Server
    from mcp.types import (
        AudioContent,
        CallToolResult,
        EmbeddedResource,
        ImageContent,
        ResourceLink,
        TextContent,
        Tool,
    )

    # Create MCP server instance
    server = Server(name, version=version)

    # Register tools if provided
    if tools:
        # Store tools for access in handlers
        tool_map = {tool_def.name: tool_def for tool_def in tools}

        # Pre-compute tool schemas once at creation time
        def _build_schema(tool_def: SdkMcpTool[Any]) -> dict[str, Any]:
            if isinstance(tool_def.input_schema, dict):
                if (
                    "type" in tool_def.input_schema
                    and "properties" in tool_def.input_schema
                    and isinstance(tool_def.input_schema["type"], str)
                ):
                    return tool_def.input_schema
                properties = {}
                for param_name, param_type in tool_def.input_schema.items():
                    properties[param_name] = _python_type_to_json_schema(param_type)
                return {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties.keys()),
                }
            if is_typeddict(tool_def.input_schema):
                return _typeddict_to_json_schema(tool_def.input_schema)
            return {"type": "object", "properties": {}}

        def _build_meta(tool_def: "SdkMcpTool[Any]") -> dict[str, Any] | None:
            # The MCP SDK's Zod schema strips unknown annotation fields, so
            # Anthropic-specific hints use _meta with namespaced keys instead.
            # maxResultSizeChars controls the CLI's layer-2 tool-result spill
            # threshold (toolResultStorage.ts maybePersistLargeToolResult).
            if tool_def.annotations is None:
                return None
            max_size = getattr(tool_def.annotations, "maxResultSizeChars", None)
            if max_size is None:
                return None
            return {"anthropic/maxResultSizeChars": max_size}

        cached_tool_list = [
            Tool.model_validate(
                {
                    "name": tool_def.name,
                    "description": tool_def.description,
                    "inputSchema": _build_schema(tool_def),
                    "annotations": tool_def.annotations,
                    "_meta": _build_meta(tool_def),
                }
            )
            for tool_def in tools
        ]

        # Register list_tools handler to expose available tools
        @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_tools() -> list[Tool]:
            """Return the list of available tools."""
            return cached_tool_list

        # Register call_tool handler to execute tools
        @server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
            """Execute a tool by name with given arguments."""
            if name not in tool_map:
                raise ValueError(f"Tool '{name}' not found")

            tool_def = tool_map[name]
            # Call the tool's handler with arguments
            result = await tool_def.handler(arguments)

            # Convert result to MCP format
            content: list[
                TextContent
                | ImageContent
                | AudioContent
                | ResourceLink
                | EmbeddedResource
            ] = []
            if "content" in result:
                for item in result["content"]:
                    item_type = item.get("type")
                    if item_type == "text":
                        content.append(TextContent(type="text", text=item["text"]))
                    elif item_type == "image":
                        content.append(
                            ImageContent(
                                type="image",
                                data=item["data"],
                                mimeType=item["mimeType"],
                            )
                        )
                    elif item_type == "resource_link":
                        parts = []
                        link_name = item.get("name")
                        uri = item.get("uri")
                        desc = item.get("description")
                        if link_name:
                            parts.append(link_name)
                        if uri:
                            parts.append(str(uri))
                        if desc:
                            parts.append(desc)
                        content.append(
                            TextContent(
                                type="text",
                                text="\n".join(parts) if parts else "Resource link",
                            )
                        )
                    elif item_type == "resource":
                        resource = item.get("resource") or {}
                        if "text" in resource:
                            content.append(
                                TextContent(type="text", text=resource["text"])
                            )
                        else:
                            logger.warning(
                                "Binary embedded resource cannot be converted to text, skipping"
                            )
                    else:
                        logger.warning(
                            "Unsupported content type %r in tool result, skipping",
                            item_type,
                        )

            return CallToolResult(
                content=content, isError=result.get("is_error", False)
            )

    # Return SDK server configuration
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
    "TaskNotificationMessage",
    "TaskNotificationStatus",
    "TaskUsage",
    "ResultMessage",
    "RateLimitEvent",
    "RateLimitInfo",
    "RateLimitStatus",
    "RateLimitType",
    "StreamEvent",
    "Message",
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
    "ContentBlock",
    "ContextUsageCategory",
    "ContextUsageResponse",
    # Tool callbacks
    "CanUseTool",
    "ToolPermissionContext",
    "PermissionResult",
    "PermissionResultAllow",
    "PermissionResultDeny",
    "PermissionUpdate",
    # Hook support
    "HookCallback",
    "HookContext",
    "HookInput",
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
    "SDKSessionInfo",
    "SessionMessage",
    # Session mutations
    "rename_session",
    "tag_session",
    "delete_session",
    "fork_session",
    "ForkSessionResult",
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
    "CLIJSONDecodeError",
]
