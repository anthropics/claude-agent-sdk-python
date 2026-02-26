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


class SessionStorageError(ClaudeSDKError):
    """Raised when session storage operations fail.

    This error is raised for cloud storage failures such as upload/download
    errors, permission issues, or network problems.

    Attributes:
        session_id: The session ID involved in the failed operation.
        operation: The operation that failed (upload, download, delete, etc.).
        original_error: The underlying exception that caused this error.

    Example:
        >>> try:
        ...     await storage.upload_transcript("session-123", "/tmp/transcript.jsonl")
        ... except SessionStorageError as e:
        ...     print(f"Failed to upload session {e.session_id}: {e}")
        ...     if e.original_error:
        ...         print(f"Caused by: {e.original_error}")
    """

    def __init__(
        self,
        message: str,
        session_id: str | None = None,
        operation: str | None = None,
        original_error: Exception | None = None,
    ):
        self.session_id = session_id
        self.operation = operation
        self.original_error = original_error

        parts = [message]
        if session_id:
            parts.append(f"session: {session_id}")
        if operation:
            parts.append(f"operation: {operation}")
        if original_error:
            parts.append(f"caused by: {original_error}")

        super().__init__(" | ".join(parts))
