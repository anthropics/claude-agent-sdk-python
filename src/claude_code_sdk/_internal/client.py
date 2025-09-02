"""Internal client implementation."""

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from ..types import (
    ClaudeCodeOptions,
    Message,
)
from .message_parser import parse_message
from .query import Query
from .transport import Transport
from .transport.subprocess_cli import SubprocessCLITransport


class InternalClient:
    """Internal client implementation."""

    def __init__(self) -> None:
        """Initialize the internal client."""

    async def process_query(
        self,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: ClaudeCodeOptions,
        transport: Transport | None = None,
    ) -> AsyncIterator[Message]:
        """Process a query through transport and Query."""

        # Use provided transport or create subprocess transport
        if transport is not None:
            chosen_transport = transport
        else:
            chosen_transport = SubprocessCLITransport(prompt=prompt, options=options)

        # Connect transport
        await chosen_transport.connect()

        # Create Query to handle control protocol
        is_streaming = not isinstance(prompt, str)
        query = Query(
            transport=chosen_transport,
            is_streaming_mode=is_streaming,
            can_use_tool=None,  # TODO: Add support for can_use_tool callback
            hooks=None,  # TODO: Add support for hooks
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
            async for data in query.receive_messages():
                yield parse_message(data)

        finally:
            await query.close()
