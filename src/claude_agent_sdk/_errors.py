"""Error types for Claude SDK."""

from typing import Any, Literal


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


# API Error types - raised when the Anthropic API returns errors
# These correspond to the error types in types.AssistantMessageError
APIErrorType = Literal[
    "authentication_failed",
    "billing_error",
    "rate_limit",
    "invalid_request",
    "server_error",
    "unknown",
]


class APIError(ClaudeSDKError):
    """Base exception for Anthropic API errors.

    This exception is raised when the API returns an error response that was
    previously returned as a text message. Subclasses provide more specific
    error types for programmatic handling.

    Attributes:
        error_type: The type of API error (e.g., "rate_limit", "invalid_request").
        message: The error message from the API.
        model: The model that was being used when the error occurred.
    """

    def __init__(
        self,
        message: str,
        error_type: APIErrorType,
        model: str | None = None,
    ):
        self.error_type = error_type
        self.model = model
        super().__init__(message)


class AuthenticationError(APIError):
    """Raised when API authentication fails (401).

    This typically indicates an invalid API key or insufficient permissions.
    """

    def __init__(self, message: str, model: str | None = None):
        super().__init__(message, "authentication_failed", model)


class BillingError(APIError):
    """Raised when there's a billing issue with the API account.

    This may indicate insufficient credits, expired subscription, or other
    billing-related issues.
    """

    def __init__(self, message: str, model: str | None = None):
        super().__init__(message, "billing_error", model)


class RateLimitError(APIError):
    """Raised when API rate limits are exceeded (429).

    Applications should implement retry logic with exponential backoff
    when handling this exception.
    """

    def __init__(self, message: str, model: str | None = None):
        super().__init__(message, "rate_limit", model)


class InvalidRequestError(APIError):
    """Raised when the API request is invalid (400).

    This may indicate invalid parameters, unsupported model, or malformed input.
    Check the error message for details about what needs to be corrected.
    """

    def __init__(self, message: str, model: str | None = None):
        super().__init__(message, "invalid_request", model)


class ServerError(APIError):
    """Raised when the API server encounters an error (500/529).

    This includes internal server errors (500) and overload errors (529).
    Applications should implement retry logic for transient server errors.
    """

    def __init__(self, message: str, model: str | None = None):
        super().__init__(message, "server_error", model)
