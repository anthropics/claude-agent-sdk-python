"""Internal client implementation."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..types import (
    ClaudeAgentOptions,
    HookEvent,
    HookMatcher,
    Message,
)
from .message_parser import parse_message
from .query import Query
from .tracing import (
    SPAN_QUERY,
    TracingContext,
    end_span,
    record_exception,
    set_span_attributes,
)
from .transport import Transport
from .transport.subprocess_cli import SubprocessCLITransport

if TYPE_CHECKING:
    from opentelemetry.trace import Span


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

        # Set up tracing
        tracing = TracingContext(options.tracer)
        span: Span | None = None

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

        # Start query span
        span = tracing.start_span(
            SPAN_QUERY,
            attributes={
                "query.streaming_mode": not isinstance(prompt, str),
                "query.model": configured_options.model or "default",
            },
        )

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
            tracer=configured_options.tracer,
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

            # Yield parsed messages
            message_count = 0
            async for data in query.receive_messages():
                message_count += 1
                msg = parse_message(data)

                # Track result message info in span
                if data.get("type") == "result":
                    set_span_attributes(
                        span,
                        {
                            "query.message_count": message_count,
                            "query.is_error": data.get("is_error", False),
                            "query.num_turns": data.get("num_turns"),
                            "query.total_cost_usd": data.get("total_cost_usd"),
                        },
                    )

                yield msg

        except Exception as e:
            record_exception(span, e)
            raise
        finally:
            end_span(span)
            await query.close()
