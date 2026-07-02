"""Tests for Claude SDK error handling."""

from claude_agent_sdk import (
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    RateLimitError,
)
from claude_agent_sdk._internal.query import _is_rate_limit_error


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

    def test_rate_limit_error_is_subclass(self):
        """Test RateLimitError is a subclass of ClaudeSDKError."""
        error = RateLimitError()
        assert isinstance(error, ClaudeSDKError)
        assert isinstance(error, Exception)

    def test_rate_limit_error_default_message(self):
        """Test RateLimitError default message."""
        error = RateLimitError()
        assert "Rate limit exceeded" in str(error)
        assert error.retry_after is None

    def test_rate_limit_error_custom_message(self):
        """Test RateLimitError with custom message."""
        error = RateLimitError("Custom rate limit message")
        assert "Custom rate limit message" in str(error)

    def test_rate_limit_error_retry_after(self):
        """Test RateLimitError with retry_after stores value and appends to message."""
        error = RateLimitError(retry_after=60)
        assert error.retry_after == 60
        assert "retry after 60s" in str(error)

    def test_rate_limit_error_retry_after_with_message(self):
        """Test RateLimitError with both message and retry_after."""
        error = RateLimitError("Too many requests", retry_after=30)
        assert error.retry_after == 30
        assert "Too many requests" in str(error)
        assert "retry after 30s" in str(error)


class TestIsRateLimitError:
    """Test the _is_rate_limit_error helper."""

    def test_detects_429(self):
        assert _is_rate_limit_error("HTTP error 429") is True

    def test_detects_rate_limit(self):
        assert _is_rate_limit_error("rate limit exceeded") is True

    def test_detects_rate_limit_mixed_case(self):
        assert _is_rate_limit_error("Rate Limit Exceeded") is True

    def test_detects_too_many_requests(self):
        assert _is_rate_limit_error("Too Many Requests") is True

    def test_detects_too_many_requests_lowercase(self):
        assert _is_rate_limit_error("too many requests") is True

    def test_returns_false_for_other_errors(self):
        assert _is_rate_limit_error("connection refused") is False

    def test_returns_false_for_empty_string(self):
        assert _is_rate_limit_error("") is False

    def test_returns_false_for_generic_error(self):
        assert _is_rate_limit_error("Unknown error occurred") is False
