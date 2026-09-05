"""Query function for single-call interactions with Claude Code."""

from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from ._internal.client import InternalClient
from ._internal.transport import Transport
from .types import ClaudeAgentOptions, Message


async def query(
    *,
    prompt: str | AsyncIterable[dict[str, Any]],
    options: ClaudeAgentOptions | None = None,
    transport: Transport | None = None,
) -> AsyncIterator[Message]:
    """
    Query Claude Code for a one-shot or single-call streaming interaction.

    This function is ideal for simple, stateless queries where you don't need
    explicit connection or conversation management. For interactive, stateful
    conversations with follow-up calls, use ClaudeSDKClient instead.

    Key differences from ClaudeSDKClient:
    - **Single-call lifecycle**: An async prompt iterable is consumed in the
      background while responses are received, so input and output may interleave
    - **Invocation-scoped**: Each query() call owns one connection lifecycle
    - **Simple**: No explicit connection management
    - **No client controls**: Cannot call interrupt() or query() again on a client

    Custom SDK MCP tools and hooks are supported through ClaudeAgentOptions.
    A can_use_tool callback requires an AsyncIterable prompt so the control
    protocol can exchange messages over the streaming connection.
    The supplied prompt iterable is the only input channel for that call;
    query() does not expose a separate send method after the call starts.

    When to use query():
    - Simple one-off questions ("What is 2+2?")
    - Batch processing of independent prompts
    - Code generation or analysis tasks
    - Automated scripts and CI/CD pipelines
    - When the input producer can be supplied for the lifetime of the call

    When to use ClaudeSDKClient:
    - Interactive conversations with follow-ups
    - Chat applications or REPL-like interfaces
    - When you need to send messages based on responses
    - When you need interrupt capabilities
    - Long-running sessions with state

    Args:
        prompt: The prompt to send to Claude. Can be a string for single-shot queries
                or an AsyncIterable[dict] for input streamed during the same call.
                Responses may arrive before that iterable is exhausted.
                In streaming mode, each dict should have the structure:
                {
                    "type": "user",
                    "message": {"role": "user", "content": "..."},
                    "parent_tool_use_id": None,
                    "session_id": "..."
                }
        options: Optional configuration (defaults to ClaudeAgentOptions() if None).
                 Set options.permission_mode to control tool execution:
                 - 'default': CLI prompts for dangerous tools
                 - 'acceptEdits': Auto-accept file edits
                 - 'plan': Plan-only mode (no tool execution)
                 - 'bypassPermissions': Allow all tools (use with caution)
                 - 'dontAsk': Deny anything not pre-approved by allow rules
                 - 'auto': A model classifier approves or denies each tool call
                 Set options.cwd for working directory.
        transport: Optional transport implementation. If provided, this will be used
                  instead of the default transport selection based on options.
                  The transport will be automatically configured with the prompt and options.

    Yields:
        Messages from the conversation

    Example - Simple query:
        ```python
        # One-off question
        async for message in query(prompt="What is the capital of France?"):
            print(message)
        ```

    Example - With options:
        ```python
        # Code generation with specific settings
        async for message in query(
            prompt="Create a Python web server",
            options=ClaudeAgentOptions(
                system_prompt="You are an expert Python developer",
                cwd="/home/user/project"
            )
        ):
            print(message)
        ```

    Example - Streaming input and output:
        ```python
        async def prompts():
            yield {"type": "user", "message": {"role": "user", "content": "Hello"}}
            yield {"type": "user", "message": {"role": "user", "content": "How are you?"}}

        # Responses may arrive while prompts() is still producing messages.
        async for message in query(prompt=prompts()):
            print(message)
        ```

    Example - With custom transport:
        ```python
        from claude_agent_sdk import query, Transport

        class MyCustomTransport(Transport):
            # Implement custom transport logic
            pass

        transport = MyCustomTransport()
        async for message in query(
            prompt="Hello",
            transport=transport
        ):
            print(message)
        ```

    """
    if options is None:
        options = ClaudeAgentOptions()

    client = InternalClient()

    async for message in client.process_query(
        prompt=prompt, options=options, transport=transport
    ):
        yield message
