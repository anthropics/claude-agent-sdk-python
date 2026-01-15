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


class APIError(ClaudeSDKError):
    """Base exception for API errors from the Anthropic API.

    Raised when the API returns an error (400, 401, 429, 529, etc.) instead of
    being silently returned as a text message.
    """

    def __init__(
        self,
        message: str,
        error_type: str | None = None,
        error_text: str | None = None,
    ):
        self.error_type = error_type
        self.error_text = error_text
        super().__init__(message)


class AuthenticationError(APIError):
    """Raised when API authentication fails (401)."""


class BillingError(APIError):
    """Raised when there's a billing issue with the API account."""


class RateLimitError(APIError):
    """Raised when API rate limits are exceeded (429)."""


class InvalidRequestError(APIError):
    """Raised when the API request is invalid (400)."""


class ServerError(APIError):
    """Raised when the API server encounters an error (500, 529)."""
