"""Tests for 429 rate limit retry with exponential backoff."""

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._errors import ProcessError, RateLimitError


class TestIsRateLimitError:
    """Test _is_rate_limit_error helper function."""

    def test_detects_rate_limit_in_error_message(self):
        """Error message containing rate_limit_error is detected."""
        from claude_agent_sdk._internal.client import _is_rate_limit_error

        error = Exception(
            'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"Rate limit exceeded"}}'
        )
        is_rl, retry_after = _is_rate_limit_error(error)
        assert is_rl is True
        assert retry_after is None

    def test_detects_429_in_error_message(self):
        """Error message containing 429 is detected."""
        from claude_agent_sdk._internal.client import _is_rate_limit_error

        error = Exception("Command failed with exit code 1: 429 rate limit")
        is_rl, retry_after = _is_rate_limit_error(error)
        assert is_rl is True
        assert retry_after is None

    def test_parses_retry_after_from_error_message(self):
        """retryAfter field is extracted from error message."""
        from claude_agent_sdk._internal.client import _is_rate_limit_error

        error = Exception('{"error":{"type":"rate_limit_error","retryAfter":45}}')
        is_rl, retry_after = _is_rate_limit_error(error)
        assert is_rl is True
        assert retry_after == 45.0

    def test_parses_retry_after_float(self):
        """retryAfter field handles float values."""
        from claude_agent_sdk._internal.client import _is_rate_limit_error

        error = Exception('{"error":{"type":"rate_limit_error","retryAfter":12.5}}')
        is_rl, retry_after = _is_rate_limit_error(error)
        assert is_rl is True
        assert retry_after == 12.5

    def test_detects_rate_limit_from_stderr_attribute(self):
        """Error with stderr attribute containing rate_limit_error is detected."""
        from claude_agent_sdk._internal.client import _is_rate_limit_error

        class ErrorWithStderr(Exception):
            stderr = (
                '{"type":"error","error":{"type":"rate_limit_error","retryAfter":30}}'
            )

        error = ErrorWithStderr("Process failed")
        is_rl, retry_after = _is_rate_limit_error(error)
        assert is_rl is True
        assert retry_after == 30.0

    def test_non_rate_limit_error_returns_false(self):
        """Generic errors without rate limit indicators return False."""
        from claude_agent_sdk._internal.client import _is_rate_limit_error

        error = Exception("Something went wrong")
        is_rl, retry_after = _is_rate_limit_error(error)
        assert is_rl is False
        assert retry_after is None

    def test_process_error_without_rate_limit_returns_false(self):
        """ProcessError without rate limit indicators returns False."""
        from claude_agent_sdk._internal.client import _is_rate_limit_error

        error = ProcessError("Process exited with code 1", exit_code=1)
        is_rl, retry_after = _is_rate_limit_error(error)
        assert is_rl is False
        assert retry_after is None


class TestRateLimitRetryOptions:
    """Test rate_limit_max_retries option."""

    def test_rate_limit_max_retries_option(self):
        """ClaudeAgentOptions accepts rate_limit_max_retries."""
        opts = ClaudeAgentOptions(rate_limit_max_retries=5)
        assert opts.rate_limit_max_retries == 5

    def test_rate_limit_max_retries_default(self):
        """rate_limit_max_retries defaults to 3."""
        opts = ClaudeAgentOptions()
        assert opts.rate_limit_max_retries == 3

    def test_rate_limit_max_retries_zero_disables_retry(self):
        """rate_limit_max_retries=0 disables automatic retry."""
        opts = ClaudeAgentOptions(rate_limit_max_retries=0)
        assert opts.rate_limit_max_retries == 0


class TestRateLimitError:
    """Test RateLimitError exception class."""

    def test_rate_limit_error_attributes(self):
        """RateLimitError stores retry_after and original_error."""
        original = ProcessError("429", exit_code=1)
        error = RateLimitError(
            "Rate limit exceeded", retry_after=30.0, original_error=original
        )

        assert error.retry_after == 30.0
        assert error.original_error is original
        assert "Rate limit exceeded" in str(error)

    def test_rate_limit_error_inherits_from_claude_sdk_error(self):
        """RateLimitError is a subclass of ClaudeSDKError."""
        from claude_agent_sdk._errors import ClaudeSDKError

        error = RateLimitError("test")
        assert isinstance(error, ClaudeSDKError)

    def test_rate_limit_error_without_retry_after(self):
        """RateLimitError works without retry_after."""
        error = RateLimitError("Rate limit exceeded")
        assert error.retry_after is None
        assert error.original_error is None

    def test_rate_limit_error_repr(self):
        """RateLimitError message includes original error info."""
        original = ProcessError("429", exit_code=1)
        error = RateLimitError("Rate limit exceeded", original_error=original)
        assert "Rate limit exceeded" in str(error)
