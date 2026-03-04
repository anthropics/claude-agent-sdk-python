"""Error types for Claude SDK."""

from typing import Any


class ClaudeSDKError(Exception):
    """Base exception for all Claude SDK errors."""


class CLIConnectionError(ClaudeSDKError):
    """Raised when unable to connect to Claude Code."""


class CLINotFoundError(CLIConnectionError):
    """Raised when Claude Code is not found or not installed."""

    def __init__(
        self, message: str = "Claude Code not found", cli_path: str | None = None
    ):
        if cli_path:
            message = f"{message}: {cli_path}"
        super().__init__(message)


class ProcessError(ClaudeSDKError):
    """Raised when the CLI process fails."""

    def __init__(
        self, message: str, exit_code: int | None = None, stderr: str | None = None
    ):
        self.exit_code = exit_code
        self.stderr = stderr

        if exit_code is not None:
            message = f"{message} (exit code: {exit_code})"
        if stderr:
            message = f"{message}\nError output: {stderr}"

        super().__init__(message)


class CLIJSONDecodeError(ClaudeSDKError):
    """Raised when unable to decode JSON from CLI output."""

    def __init__(self, line: str, original_error: Exception):
        self.line = line
        self.original_error = original_error
        super().__init__(f"Failed to decode JSON: {line[:100]}...")


class MessageParseError(ClaudeSDKError):
    """Raised when unable to parse a message from CLI output."""

    def __init__(self, message: str, data: dict[str, Any] | None = None):
        self.data = data
        super().__init__(message)


class TaskContextError(ClaudeSDKError):
    """Raised when ClaudeSDKClient is used across different async tasks.

    This occurs when connect() is called in one async task (e.g., FastAPI startup)
    and receive_messages() is called in a different task (e.g., request handler).

    The anyio MemoryObjectStream used internally is bound to the task group
    where it was created, and cannot be accessed from a different task.

    Attributes:
        connect_task_id: ID of the task where connect() was called
        current_task_id: ID of the task where the error occurred

    Example:
        Wrong way (will raise this error):
        ```python
        client = ClaudeSDKClient()
        await client.connect()  # Task A

        async def handle_request():
            async for msg in client.receive_messages():  # Task B - ERROR!
                yield msg
        ```

        Correct way:
        ```python
        async def handle_request():
            async with ClaudeSDKClient() as client:  # Create in same task
                await client.query("Hello")
                async for msg in client.receive_messages():
                    yield msg
        ```
    """

    def __init__(
        self,
        message: str = "Client used across different async tasks",
        connect_task_id: int | None = None,
        current_task_id: int | None = None,
    ):
        self.connect_task_id = connect_task_id
        self.current_task_id = current_task_id

        # Build helpful error message
        if connect_task_id is not None and current_task_id is not None:
            message = (
                f"{message} (connect task: {connect_task_id}, "
                f"current task: {current_task_id})"
            )

        super().__init__(message)
