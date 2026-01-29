"""Tests for Claude SDK error handling."""

from claude_agent_sdk import (
    APIError,
    AuthenticationError,
    BillingError,
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    InvalidRequestError,
    ProcessError,
    RateLimitError,
    ServerError,
)


class TestErrorTypes:
    """Test error types and their properties."""

    def test_base_error(self):
        """Test base ClaudeSDKError."""
        error = ClaudeSDKError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert isinstance(error, Exception)

    def test_cli_not_found_error(self):
        """Test CLINotFoundError."""
        error = CLINotFoundError("Claude Code not found")
        assert isinstance(error, ClaudeSDKError)
        assert "Claude Code not found" in str(error)

    def test_connection_error(self):
        """Test CLIConnectionError."""
        error = CLIConnectionError("Failed to connect to CLI")
        assert isinstance(error, ClaudeSDKError)
        assert "Failed to connect to CLI" in str(error)

    def test_process_error(self):
        """Test ProcessError with exit code and stderr."""
        error = ProcessError("Process failed", exit_code=1, stderr="Command not found")
        assert error.exit_code == 1
        assert error.stderr == "Command not found"
        assert "Process failed" in str(error)
        assert "exit code: 1" in str(error)
        assert "Command not found" in str(error)

    def test_json_decode_error(self):
        """Test CLIJSONDecodeError."""
        import json

        try:
            json.loads("{invalid json}")
        except json.JSONDecodeError as e:
            error = CLIJSONDecodeError("{invalid json}", e)
            assert error.line == "{invalid json}"
            assert error.original_error == e
            assert "Failed to decode JSON" in str(error)


class TestAPIErrors:
    """Test API error types for programmatic error handling (issue #472)."""

    def test_api_error_base(self):
        """Test base APIError."""
        error = APIError("API error occurred", "unknown", "claude-sonnet-4-5")
        assert isinstance(error, ClaudeSDKError)
        assert error.error_type == "unknown"
        assert error.model == "claude-sonnet-4-5"
        assert "API error occurred" in str(error)

    def test_authentication_error(self):
        """Test AuthenticationError for 401 responses."""
        error = AuthenticationError("Invalid API key", "claude-sonnet-4-5")
        assert isinstance(error, APIError)
        assert isinstance(error, ClaudeSDKError)
        assert error.error_type == "authentication_failed"
        assert error.model == "claude-sonnet-4-5"
        assert "Invalid API key" in str(error)

    def test_billing_error(self):
        """Test BillingError for billing issues."""
        error = BillingError("Insufficient credits", "claude-opus-4-5")
        assert isinstance(error, APIError)
        assert error.error_type == "billing_error"
        assert error.model == "claude-opus-4-5"
        assert "Insufficient credits" in str(error)

    def test_rate_limit_error(self):
        """Test RateLimitError for 429 responses."""
        error = RateLimitError("Rate limit exceeded", "claude-sonnet-4-5")
        assert isinstance(error, APIError)
        assert error.error_type == "rate_limit"
        assert "Rate limit exceeded" in str(error)

    def test_invalid_request_error(self):
        """Test InvalidRequestError for 400 responses."""
        error = InvalidRequestError(
            "The provided model identifier is invalid", "invalid-model"
        )
        assert isinstance(error, APIError)
        assert error.error_type == "invalid_request"
        assert "model identifier is invalid" in str(error)

    def test_server_error(self):
        """Test ServerError for 500/529 responses."""
        error = ServerError("API Error: Repeated 529 Overloaded errors")
        assert isinstance(error, APIError)
        assert error.error_type == "server_error"
        assert error.model is None  # Model is optional
        assert "529 Overloaded" in str(error)

    def test_api_errors_are_catchable_by_base_class(self):
        """Test that all API errors can be caught by APIError or ClaudeSDKError."""
        errors = [
            AuthenticationError("auth failed"),
            BillingError("billing issue"),
            RateLimitError("rate limited"),
            InvalidRequestError("bad request"),
            ServerError("server error"),
        ]

        for error in errors:
            # Should be catchable by APIError
            try:
                raise error
            except APIError as e:
                assert e.error_type is not None

            # Should also be catchable by ClaudeSDKError
            try:
                raise error
            except ClaudeSDKError:
                pass  # Successfully caught
