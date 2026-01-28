"""Internal client implementation."""

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import replace
from typing import Any

from .._errors import (
    APIError,
    AuthenticationError,
    BillingError,
    InvalidRequestError,
    RateLimitError,
    ServerError,
)
from ..types import (
    AssistantMessage,
    AssistantMessageError,
    ClaudeAgentOptions,
    HookEvent,
    HookMatcher,
    Message,
    TextBlock,
)
from .message_parser import parse_message
from .query import Query
from .transport import Transport
from .transport.subprocess_cli import SubprocessCLITransport


def _extract_error_message(message: AssistantMessage) -> str:
    """Extract the error message text from an AssistantMessage.

    When the API returns an error, the error text is typically in the
    first TextBlock of the message content.

    Args:
        message: The AssistantMessage containing the error.

    Returns:
        The error message text, or a default message if none found.
    """
    for block in message.content:
        if isinstance(block, TextBlock):
            return block.text
    return "An API error occurred"


def _raise_api_error(message: AssistantMessage) -> None:
    """Raise the appropriate API exception for an AssistantMessage with an error.

    This function converts the error field on an AssistantMessage into a proper
    Python exception that can be caught and handled programmatically.

    Args:
        message: The AssistantMessage with error field set.

    Raises:
        AuthenticationError: For authentication_failed errors (401).
        BillingError: For billing_error errors.
        RateLimitError: For rate_limit errors (429).
        InvalidRequestError: For invalid_request errors (400).
        ServerError: For server_error errors (500/529).
        APIError: For unknown error types.
    """
    error_type: AssistantMessageError = message.error  # type: ignore[assignment]
    error_message = _extract_error_message(message)
    model = message.model

    match error_type:
        case "authentication_failed":
            raise AuthenticationError(error_message, model)
        case "billing_error":
            raise BillingError(error_message, model)
        case "rate_limit":
            raise RateLimitError(error_message, model)
        case "invalid_request":
            raise InvalidRequestError(error_message, model)
        case "server_error":
            raise ServerError(error_message, model)
        case _:
            # Handle "unknown" or any future error types
            raise APIError(error_message, error_type, model)


class InternalClient:
    """Internal client implementation."""

    def __init__(self) -> None:
        """Initialize the internal client."""

    def _convert_hooks_to_internal_format(
        self, hooks: dict[HookEvent, list[HookMatcher]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Convert HookMatcher format to internal Query format."""
        internal_hooks: dict[str, list[dict[str, Any]]] = {}
        for event, matchers in hooks.items():
            internal_hooks[event] = []
            for matcher in matchers:
                # Convert HookMatcher to internal dict format
                internal_matcher: dict[str, Any] = {
                    "matcher": matcher.matcher if hasattr(matcher, "matcher") else None,
                    "hooks": matcher.hooks if hasattr(matcher, "hooks") else [],
                }
                if hasattr(matcher, "timeout") and matcher.timeout is not None:
                    internal_matcher["timeout"] = matcher.timeout
                internal_hooks[event].append(internal_matcher)
        return internal_hooks

    async def process_query(
        self,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: ClaudeAgentOptions,
        transport: Transport | None = None,
    ) -> AsyncIterator[Message]:
        """Process a query through transport and Query."""

        # Validate and configure permission settings (matching TypeScript SDK logic)
        configured_options = options
        if options.can_use_tool:
            # canUseTool callback requires streaming mode (AsyncIterable prompt)
            if isinstance(prompt, str):
                raise ValueError(
                    "can_use_tool callback requires streaming mode. "
                    "Please provide prompt as an AsyncIterable instead of a string."
                )

            # canUseTool and permission_prompt_tool_name are mutually exclusive
            if options.permission_prompt_tool_name:
                raise ValueError(
                    "can_use_tool callback cannot be used with permission_prompt_tool_name. "
                    "Please use one or the other."
                )

            # Automatically set permission_prompt_tool_name to "stdio" for control protocol
            configured_options = replace(options, permission_prompt_tool_name="stdio")

        # Use provided transport or create subprocess transport
        if transport is not None:
            chosen_transport = transport
        else:
            chosen_transport = SubprocessCLITransport(
                prompt=prompt,
                options=configured_options,
            )

        # Connect transport
        await chosen_transport.connect()

        # Extract SDK MCP servers from configured options
        sdk_mcp_servers = {}
        if configured_options.mcp_servers and isinstance(
            configured_options.mcp_servers, dict
        ):
            for name, config in configured_options.mcp_servers.items():
                if isinstance(config, dict) and config.get("type") == "sdk":
                    sdk_mcp_servers[name] = config["instance"]  # type: ignore[typeddict-item]

        # Create Query to handle control protocol
        is_streaming = not isinstance(prompt, str)
        query = Query(
            transport=chosen_transport,
            is_streaming_mode=is_streaming,
            can_use_tool=configured_options.can_use_tool,
            hooks=self._convert_hooks_to_internal_format(configured_options.hooks)
            if configured_options.hooks
            else None,
            sdk_mcp_servers=sdk_mcp_servers,
        )

        try:
            # Start reading messages
            await query.start()

            # Initialize if streaming
            if is_streaming:
                await query.initialize()

            # Stream input if it's an AsyncIterable
            if isinstance(prompt, AsyncIterable) and query._tg:
                # Start streaming in background
                # Create a task that will run in the background
                query._tg.start_soon(query.stream_input, prompt)
            # For string prompts, the prompt is already passed via CLI args

            # Yield parsed messages, checking for API errors
            async for data in query.receive_messages():
                message = parse_message(data)

                # Check if this is an AssistantMessage with an API error
                if isinstance(message, AssistantMessage) and message.error is not None:
                    _raise_api_error(message)

                yield message

        finally:
            await query.close()
