"""Claude SDK Client for interacting with Claude Code."""

import os
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from ._errors import CLIConnectionError
from .types import ClaudeCodeOptions, Message, ResultMessage


class ClaudeSDKClient:
    """
    Client for bidirectional, interactive conversations with Claude Code.

    This client provides full control over the conversation flow with support
    for streaming, interrupts, and dynamic message sending. For simple one-shot
    queries, consider using the query() function instead.

    Key features:
    - **Bidirectional**: Send and receive messages at any time
    - **Stateful**: Maintains conversation context across messages
    - **Interactive**: Send follow-ups based on responses
    - **Control flow**: Support for interrupts and session management

    When to use ClaudeSDKClient:
    - Building chat interfaces or conversational UIs
    - Interactive debugging or exploration sessions
    - Multi-turn conversations with context
    - When you need to react to Claude's responses
    - Real-time applications with user input
    - When you need interrupt capabilities

    When to use query() instead:
    - Simple one-off questions
    - Batch processing of prompts
    - Fire-and-forget automation scripts
    - When all inputs are known upfront
    - Stateless operations

    Example - Interactive conversation:
        ```python
        # Automatically connects with empty stream for interactive use
        async with ClaudeSDKClient() as client:
            # Send initial message
            await client.send_message("Let's solve a math problem step by step")

            # Receive and process response
            async for message in client.receive_messages():
                if "ready" in str(message.content).lower():
                    break

            # Send follow-up based on response
            await client.send_message("What's 15% of 80?")

            # Continue conversation...
        # Automatically disconnects
        ```

    Example - With interrupt:
        ```python
        async with ClaudeSDKClient() as client:
            # Start a long task
            await client.send_message("Count to 1000")

            # Interrupt after 2 seconds
            await asyncio.sleep(2)
            await client.interrupt()

            # Send new instruction
            await client.send_message("Never mind, what's 2+2?")
        ```

    Example - Manual connection:
        ```python
        client = ClaudeSDKClient()

        # Connect with initial message stream
        async def message_stream():
            yield {"type": "user", "message": {"role": "user", "content": "Hello"}}

        await client.connect(message_stream())

        # Send additional messages dynamically
        await client.send_message("What's the weather?")

        async for message in client.receive_messages():
            print(message)

        await client.disconnect()
        ```
    """

    def __init__(self, options: ClaudeCodeOptions | None = None):
        """Initialize Claude SDK client."""
        if options is None:
            options = ClaudeCodeOptions()
        self.options = options
        self._transport: Any | None = None
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py-client"

    async def connect(self, prompt: str | AsyncIterable[dict[str, Any]] | None = None) -> None:
        """Connect to Claude with a prompt or message stream."""
        from ._internal.transport.subprocess_cli import SubprocessCLITransport

        # Auto-connect with empty async iterable if no prompt is provided
        async def _empty_stream() -> AsyncIterator[dict[str, Any]]:
            # Never yields, but indicates that this function is an iterator and
            # keeps the connection open.
            # This yield is never reached but makes this an async generator
            return
            yield {}  # type: ignore[unreachable]

        self._transport = SubprocessCLITransport(
            prompt=_empty_stream() if prompt is None else prompt,
            options=self.options,
        )
        await self._transport.connect()

    async def receive_messages(self) -> AsyncIterator[Message]:
        """Receive all messages from Claude."""
        if not self._transport:
            raise CLIConnectionError("Not connected. Call connect() first.")

        from ._internal.message_parser import parse_message

        async for data in self._transport.receive_messages():
            message = parse_message(data)
            if message:
                yield message

    async def send_message(self, content: str, session_id: str = "default") -> None:
        """Send a new message in streaming mode."""
        if not self._transport:
            raise CLIConnectionError("Not connected. Call connect() first.")

        message = {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": session_id,
        }

        await self._transport.send_request([message], {"session_id": session_id})

    async def interrupt(self) -> None:
        """Send interrupt signal (only works with streaming mode)."""
        if not self._transport:
            raise CLIConnectionError("Not connected. Call connect() first.")
        await self._transport.interrupt()

    async def receive_response(self) -> AsyncIterator[Message]:
        """
        Receive messages from Claude until a ResultMessage is received.

        This is an async iterator that yields all messages including the final ResultMessage.
        It's a convenience method over receive_messages() that automatically stops iteration
        after receiving a ResultMessage.

        Yields:
            Message: Each message received (UserMessage, AssistantMessage, SystemMessage, ResultMessage)

        Example:
            ```python
            async with ClaudeSDKClient() as client:
                # Send message and process response
                await client.send_message("What's the capital of France?")

                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                print(f"Claude: {block.text}")
                    elif isinstance(msg, ResultMessage):
                        print(f"Cost: ${msg.total_cost_usd:.4f}")
            ```

        Note:
            The iterator will automatically stop after yielding a ResultMessage.
            If you need to collect all messages into a list, use:
            ```python
            messages = [msg async for msg in client.receive_response()]
            ```
        """
        async for message in self.receive_messages():
            yield message
            if isinstance(message, ResultMessage):
                return

    async def disconnect(self) -> None:
        """Disconnect from Claude."""
        if self._transport:
            await self._transport.disconnect()
            self._transport = None

    async def __aenter__(self) -> "ClaudeSDKClient":
        """Enter async context - automatically connects with empty stream for interactive use."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Exit async context - always disconnects."""
        await self.disconnect()
        return False
