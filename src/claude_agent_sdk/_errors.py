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


class RateLimitError(ClaudeSDKError):
    """Raised when an API rate limit is hit and the SDK will retry."""

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        resets_at: int | None = None,
        rate_limit_type: str | None = None,
    ):
        """Initialize rate limit error.

        Args:
            message: Human-readable error message
            retry_after: Seconds to wait before retrying (from Retry-After header)
            resets_at: Unix timestamp when the rate limit resets
            rate_limit_type: Type of rate limit (e.g., "five_hour", "seven_day")
        """
        self.retry_after = retry_after
        self.resets_at = resets_at
        self.rate_limit_type = rate_limit_type
        super().__init__(message)
