"""Internal client implementation."""

import asyncio
import json
import logging
import os
import random
import re
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import asdict, replace
from typing import Any

from .._errors import ProcessError, RateLimitError
from ..types import (
    ClaudeAgentOptions,
    HookEvent,
    HookMatcher,
    Message,
)
from .message_parser import parse_message
from .query import Query
from .transport import Transport
from .transport.subprocess_cli import SubprocessCLITransport

logger = logging.getLogger(__name__)


def _is_rate_limit_error(error: Exception) -> tuple[bool, float | None]:
    """Detect if an error is a 429 rate limit error."""
    error_str = str(error)

    if "rate_limit_error" in error_str or "429" in error_str:
        retry_after: float | None = None
        match = re.search(r'"retryAfter"\s*:\s*(\d+(?:\.\d+)?)', error_str)
        if match:
            retry_after = float(match.group(1))
        else:
            match = re.search(
                r'Retry-After["\s:]+(\d+(?:\.\d+)?)', error_str, re.IGNORECASE
            )
            if match:
                retry_after = float(match.group(1))
        return True, retry_after

    if hasattr(error, "stderr") and error.stderr:
        stderr_str = str(error.stderr)
        if "rate_limit_error" in stderr_str or "429" in stderr_str:
            match = re.search(r'"retryAfter"\s*:\s*(\d+(?:\.\d+)?)', stderr_str)
            if match:
                return True, float(match.group(1))
            return True, None

    return False, None


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
        """Process a query through transport and Query with automatic 429 retry."""
        configured_options = options
        if options.can_use_tool:
            if isinstance(prompt, str):
                raise ValueError(
                    "can_use_tool callback requires streaming mode. "
                    "Please provide prompt as an AsyncIterable instead of a string."
                )

            if options.permission_prompt_tool_name:
                raise ValueError(
                    "can_use_tool cannot be used with permission_prompt_tool_name. "
                    "Please use one or the other."
                )

            configured_options = replace(options, permission_prompt_tool_name="stdio")

        max_retries = configured_options.rate_limit_max_retries
        attempt = 0

        while True:
            is_retry = attempt > 0
            chosen_transport: Transport
            query: Query | None = None

            try:
                if transport is not None and not is_retry:
                    chosen_transport = transport
                else:
                    if transport is not None and is_retry:
                        await transport.close()
                    chosen_transport = SubprocessCLITransport(
                        prompt=prompt,
                        options=configured_options,
                    )
                    await chosen_transport.connect()

                sdk_mcp_servers = {}
                if configured_options.mcp_servers and isinstance(
                    configured_options.mcp_servers, dict
                ):
                    for name, config in configured_options.mcp_servers.items():
                        if isinstance(config, dict) and config.get("type") == "sdk":
                            sdk_mcp_servers[name] = config["instance"]  # type: ignore[typeddict-item]

                exclude_dynamic_sections: bool | None = None
                sp = configured_options.system_prompt
                if isinstance(sp, dict) and sp.get("type") == "preset":
                    eds = sp.get("exclude_dynamic_sections")
                    if isinstance(eds, bool):
                        exclude_dynamic_sections = eds

                agents_dict = None
                if configured_options.agents:
                    agents_dict = {
                        name: {
                            k: v for k, v in asdict(agent_def).items() if v is not None
                        }
                        for name, agent_def in configured_options.agents.items()
                    }

                initialize_timeout_ms = int(
                    os.environ.get("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "60000")
                )
                initialize_timeout = max(initialize_timeout_ms / 1000.0, 60.0)

                query = Query(
                    transport=chosen_transport,
                    is_streaming_mode=True,
                    can_use_tool=configured_options.can_use_tool,
                    hooks=self._convert_hooks_to_internal_format(
                        configured_options.hooks
                    )
                    if configured_options.hooks
                    else None,
                    sdk_mcp_servers=sdk_mcp_servers,
                    initialize_timeout=initialize_timeout,
                    agents=agents_dict,
                    exclude_dynamic_sections=exclude_dynamic_sections,
                )

                await query.start()
                await query.initialize()

                if isinstance(prompt, str):
                    user_message = {
                        "type": "user",
                        "session_id": "",
                        "message": {"role": "user", "content": prompt},
                        "parent_tool_use_id": None,
                    }
                    await chosen_transport.write(json.dumps(user_message) + "\n")
                    query.spawn_task(query.wait_for_result_and_end_input())
                elif isinstance(prompt, AsyncIterable):
                    query.spawn_task(query.stream_input(prompt))

                async for data in query.receive_messages():
                    message = parse_message(data)
                    if message is not None:
                        yield message

                return

            except ProcessError as e:
                is_rl, retry_after = _is_rate_limit_error(e)

                if is_rl and attempt < max_retries:
                    attempt += 1
                    if retry_after is None:
                        base_delay = min(2.0 * (2 ** (attempt - 1)), 60.0)
                        delay = base_delay + random.uniform(0, 1)
                    else:
                        delay = retry_after

                    logger.warning(
                        "Rate limit hit (attempt %d/%d). Retrying in %.1fs.",
                        attempt,
                        max_retries,
                        delay,
                    )

                    if query is not None:
                        await query.close()
                    elif chosen_transport is not None:
                        await chosen_transport.close()

                    await asyncio.sleep(delay)
                    continue

                raise RateLimitError(
                    str(e),
                    retry_after=retry_after,
                    original_error=e,
                ) from e

            finally:
                if query is not None:
                    await query.close()
