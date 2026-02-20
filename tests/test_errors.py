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
    """Test API error types for programmatic error handling."""

    def test_api_error_base(self):
        """Test base APIError."""
        error = APIError(
            "API error (unknown): Something went wrong",
            error_type="unknown",
            error_text="Something went wrong",
        )
        assert isinstance(error, ClaudeSDKError)
        assert error.error_type == "unknown"
        assert error.error_text == "Something went wrong"
        assert "API error" in str(error)

    def test_authentication_error(self):
        """Test AuthenticationError for 401 responses."""
        error = AuthenticationError(
            "API error (authentication_failed): Invalid API key",
            error_type="authentication_failed",
            error_text="Invalid API key",
        )
        assert isinstance(error, APIError)
        assert isinstance(error, ClaudeSDKError)
        assert error.error_type == "authentication_failed"

    def test_billing_error(self):
        """Test BillingError for billing issues."""
        error = BillingError(
            "API error (billing_error): Account suspended",
            error_type="billing_error",
            error_text="Account suspended",
        )
        assert isinstance(error, APIError)
        assert error.error_type == "billing_error"

    def test_rate_limit_error(self):
        """Test RateLimitError for 429 responses."""
        error = RateLimitError(
            "API error (rate_limit): Too many requests",
            error_type="rate_limit",
            error_text="Too many requests",
        )
        assert isinstance(error, APIError)
        assert error.error_type == "rate_limit"

    def test_invalid_request_error(self):
        """Test InvalidRequestError for 400 responses."""
        error = InvalidRequestError(
            "API error (invalid_request): Model identifier is invalid",
            error_type="invalid_request",
            error_text="Model identifier is invalid",
        )
        assert isinstance(error, APIError)
        assert error.error_type == "invalid_request"

    def test_server_error(self):
        """Test ServerError for 500/529 responses."""
        error = ServerError(
            "API error (server_error): Service temporarily unavailable",
            error_type="server_error",
            error_text="Service temporarily unavailable",
        )
        assert isinstance(error, APIError)
        assert error.error_type == "server_error"

    def test_error_hierarchy(self):
        """Test that all API errors inherit from APIError and ClaudeSDKError."""
        error_classes = [
            AuthenticationError,
            BillingError,
            RateLimitError,
            InvalidRequestError,
            ServerError,
        ]
        for cls in error_classes:
            error = cls("test", error_type="test")
            assert isinstance(error, APIError)
            assert isinstance(error, ClaudeSDKError)
            assert isinstance(error, Exception)
