"""Internal client implementation."""

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import asdict, replace
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

# Map error types to exception classes
_ERROR_TYPE_TO_EXCEPTION: dict[str, type[APIError]] = {
    "authentication_failed": AuthenticationError,
    "billing_error": BillingError,
    "rate_limit": RateLimitError,
    "invalid_request": InvalidRequestError,
    "server_error": ServerError,
    "unknown": APIError,
}


def _raise_if_api_error(message: Message) -> None:
    """Check if a message contains an API error and raise the appropriate exception.

    Args:
        message: The parsed message to check

    Raises:
        APIError: If the message contains an API error
    """
    if isinstance(message, AssistantMessage) and message.error:
        # Extract error text from message content
        error_text = None
        if message.content:
            for block in message.content:
                if isinstance(block, TextBlock):
                    error_text = block.text
                    break

        # Get the appropriate exception class
        exc_class = _ERROR_TYPE_TO_EXCEPTION.get(message.error, APIError)

        # Build error message
        error_message = f"API error ({message.error})"
        if error_text:
            error_message = f"{error_message}: {error_text}"

        raise exc_class(
            message=error_message,
            error_type=message.error,
            error_text=error_text,
        )


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

        # Convert agents to dict format for initialize request
        agents_dict = None
        if configured_options.agents:
            agents_dict = {
                name: {k: v for k, v in asdict(agent_def).items() if v is not None}
                for name, agent_def in configured_options.agents.items()
            }

        # Create Query to handle control protocol
        # Always use streaming mode internally (matching TypeScript SDK)
        # This ensures agents are always sent via initialize request
        query = Query(
            transport=chosen_transport,
            is_streaming_mode=True,  # Always streaming internally
            can_use_tool=configured_options.can_use_tool,
            hooks=self._convert_hooks_to_internal_format(configured_options.hooks)
            if configured_options.hooks
            else None,
            sdk_mcp_servers=sdk_mcp_servers,
            agents=agents_dict,
        )

        try:
            # Start reading messages
            await query.start()

            # Always initialize to send agents via stdin (matching TypeScript SDK)
            await query.initialize()

            # Handle prompt input
            if isinstance(prompt, str):
                # For string prompts, write user message to stdin after initialize
                # (matching TypeScript SDK behavior)
                import json

                user_message = {
                    "type": "user",
                    "session_id": "",
                    "message": {"role": "user", "content": prompt},
                    "parent_tool_use_id": None,
                }
                await chosen_transport.write(json.dumps(user_message) + "\n")
                await chosen_transport.end_input()
            elif isinstance(prompt, AsyncIterable) and query._tg:
                # Stream input in background for async iterables
                query._tg.start_soon(query.stream_input, prompt)

            # Yield parsed messages
            async for data in query.receive_messages():
                message = parse_message(data)
                # Check for API errors and raise appropriate exceptions
                _raise_if_api_error(message)
                yield message

        finally:
            await query.close()
