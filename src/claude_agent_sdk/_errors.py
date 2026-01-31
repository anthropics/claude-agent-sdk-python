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


# API Error types
# These correspond to AssistantMessageError in types.py:
# "authentication_failed", "billing_error", "rate_limit",
# "invalid_request", "server_error", "unknown"

APIErrorType = Literal[
    "authentication_failed",
    "billing_error",
    "rate_limit",
    "invalid_request",
    "server_error",
    "unknown",
]


class APIError(ClaudeSDKError):
    """Base exception for API errors returned by the Anthropic API.

    This exception is raised when the Claude CLI returns an assistant message
    with an error field, indicating an API-level failure such as authentication
    errors, rate limits, or invalid requests.

    Catching this exception allows programmatic handling of API errors instead
    of having them appear as text messages in the response stream.

    Attributes:
        error_type: The type of API error (e.g., "rate_limit", "authentication_failed")
        message: Human-readable error message extracted from the response
        model: The model that was being used when the error occurred
    """

    def __init__(
        self,
        message: str,
        error_type: APIErrorType = "unknown",
        model: str | None = None,
    ):
        self.error_type = error_type
        self.model = model
        super().__init__(message)


class AuthenticationError(APIError):
    """Raised when API authentication fails (invalid or expired API key).

    This typically indicates:
    - Invalid API key
    - Expired API key
    - API key without required permissions

    Example:
        try:
            async for msg in query("Hello"):
                print(msg)
        except AuthenticationError as e:
            print(f"Auth failed: {e}")
            # Prompt user to check API key
    """

    def __init__(self, message: str = "Authentication failed", model: str | None = None):
        super().__init__(message, error_type="authentication_failed", model=model)


class BillingError(APIError):
    """Raised when there's a billing issue with the API account.

    This typically indicates:
    - Insufficient credits
    - Payment method issues
    - Account suspension due to billing

    Example:
        try:
            async for msg in query("Hello"):
                print(msg)
        except BillingError as e:
            print(f"Billing issue: {e}")
            # Prompt user to check account balance
    """

    def __init__(
        self, message: str = "Billing error", model: str | None = None
    ):
        super().__init__(message, error_type="billing_error", model=model)


class RateLimitError(APIError):
    """Raised when API rate limits are exceeded.

    This typically indicates:
    - Too many requests per minute
    - Token usage limits exceeded
    - Concurrent request limits exceeded

    Applications should implement retry logic with exponential backoff
    when catching this exception.

    Example:
        import asyncio

        async def query_with_retry(prompt, max_retries=3):
            for attempt in range(max_retries):
                try:
                    async for msg in query(prompt):
                        yield msg
                    return
                except RateLimitError:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        raise
    """

    def __init__(self, message: str = "Rate limit exceeded", model: str | None = None):
        super().__init__(message, error_type="rate_limit", model=model)


class InvalidRequestError(APIError):
    """Raised when the API request is invalid.

    This typically indicates:
    - Invalid model identifier
    - Malformed request parameters
    - Input exceeds model limits
    - Invalid tool configurations

    Example:
        try:
            async for msg in query("Hello", options=ClaudeAgentOptions(model="invalid")):
                print(msg)
        except InvalidRequestError as e:
            print(f"Invalid request: {e}")
    """

    def __init__(self, message: str = "Invalid request", model: str | None = None):
        super().__init__(message, error_type="invalid_request", model=model)


class ServerError(APIError):
    """Raised when the API server encounters an internal error.

    This typically indicates:
    - Server-side issues (5xx errors)
    - API overload (529)
    - Temporary service disruption

    Applications should implement retry logic when catching this exception,
    as server errors are often transient.

    Example:
        try:
            async for msg in query("Hello"):
                print(msg)
        except ServerError as e:
            print(f"Server error (retrying...): {e}")
    """

    def __init__(self, message: str = "Server error", model: str | None = None):
        super().__init__(message, error_type="server_error", model=model)


def get_api_error_class(error_type: str) -> type[APIError]:
    """Get the appropriate APIError subclass for an error type.

    Args:
        error_type: The error type string from AssistantMessage.error

    Returns:
        The appropriate APIError subclass, or APIError for unknown types
    """
    error_map: dict[str, type[APIError]] = {
        "authentication_failed": AuthenticationError,
        "billing_error": BillingError,
        "rate_limit": RateLimitError,
        "invalid_request": InvalidRequestError,
        "server_error": ServerError,
    }
    return error_map.get(error_type, APIError)
