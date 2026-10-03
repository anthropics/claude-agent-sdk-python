"""Query function for one-shot interactions with Claude Code."""

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
    Query Claude Code for one-shot or unidirectional streaming interactions.

    This function is ideal for simple, stateless queries where you don't need
    bidirectional communication or conversation management. For interactive,
    stateful conversations, use ClaudeSDKClient instead.

    Key differences from ClaudeSDKClient:
    - **Unidirectional**: Send all messages upfront, receive all responses
    - **Stateless**: Each query is independent, no conversation state
    - **Simple**: Fire-and-forget style, no connection management
    - **No interrupts**: Cannot interrupt or send follow-up messages

    When to use query():
    - Simple one-off questions ("What is 2+2?")
    - Batch processing of independent prompts
    - Code generation or analysis tasks
    - Automated scripts and CI/CD pipelines
    - When you know all inputs upfront

    When to use ClaudeSDKClient:
    - Interactive conversations with follow-ups
    - Chat applications or REPL-like interfaces
    - When you need to send messages based on responses
    - When you need interrupt capabilities
    - Long-running sessions with state

    Args:
        prompt: The prompt to send to Claude. Can be a string for single-shot queries
                or an AsyncIterable[dict] for streaming mode with continuous interaction.
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
        transport: Optional transport implementation. If provided, it is used
                  instead of the default subprocess transport: the SDK calls its
                  ``connect()``, delivers the prompt over ``write()``, and sends
                  hooks, agents, and the other initialize-request settings through
                  the control protocol. Command-line options in ``options`` (model,
                  cwd, permission mode, tools, and the other fields the subprocess
                  transport turns into CLI flags) are not applied to a custom
                  transport, and store-backed session resume is skipped.

    Yields:
        Messages from the conversation

    Examples:
        Simple query:
        ```python
        # One-off question
        async for message in query(prompt="What is the capital of France?"):
            print(message)
        ```

        With options:
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

        Streaming mode (still unidirectional):
        ```python
        async def prompts():
            yield {"type": "user", "message": {"role": "user", "content": "Hello"}}
            yield {"type": "user", "message": {"role": "user", "content": "How are you?"}}

        # All prompts are sent, then all responses received
        async for message in query(prompt=prompts()):
            print(message)
        ```

        With custom transport:
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
