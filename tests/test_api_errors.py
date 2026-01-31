"""Tests for API error handling.

Tests the fixes for:
- Issue #472: API errors returned as text instead of raised as exceptions
- Issue #505: AssistantMessage error field not being properly populated
"""

import pytest

from claude_agent_sdk._errors import (
    APIError,
    AuthenticationError,
    BillingError,
    InvalidRequestError,
    RateLimitError,
    ServerError,
    get_api_error_class,
)
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import AssistantMessage


class TestAPIErrorClasses:
    """Tests for API error exception classes."""

    def test_api_error_base_class(self):
        """Test APIError base class attributes."""
        error = APIError("Test error", error_type="unknown", model="claude-sonnet-4-5")
        assert str(error) == "Test error"
        assert error.error_type == "unknown"
        assert error.model == "claude-sonnet-4-5"

    def test_authentication_error(self):
        """Test AuthenticationError has correct error_type."""
        error = AuthenticationError("Invalid API key")
        assert error.error_type == "authentication_failed"
        assert str(error) == "Invalid API key"

    def test_billing_error(self):
        """Test BillingError has correct error_type."""
        error = BillingError("Insufficient credits")
        assert error.error_type == "billing_error"

    def test_rate_limit_error(self):
        """Test RateLimitError has correct error_type."""
        error = RateLimitError("Too many requests")
        assert error.error_type == "rate_limit"

    def test_invalid_request_error(self):
        """Test InvalidRequestError has correct error_type."""
        error = InvalidRequestError("Invalid model identifier")
        assert error.error_type == "invalid_request"

    def test_server_error(self):
        """Test ServerError has correct error_type."""
        error = ServerError("Internal server error")
        assert error.error_type == "server_error"

    def test_error_inheritance(self):
        """Test that all API errors inherit from APIError."""
        assert issubclass(AuthenticationError, APIError)
        assert issubclass(BillingError, APIError)
        assert issubclass(RateLimitError, APIError)
        assert issubclass(InvalidRequestError, APIError)
        assert issubclass(ServerError, APIError)

    def test_error_with_model(self):
        """Test that model is stored on error."""
        error = RateLimitError("Rate limited", model="claude-opus-4-5")
        assert error.model == "claude-opus-4-5"


class TestGetAPIErrorClass:
    """Tests for get_api_error_class function."""

    def test_authentication_failed_maps_to_authentication_error(self):
        """Test authentication_failed maps to AuthenticationError."""
        assert get_api_error_class("authentication_failed") == AuthenticationError

    def test_billing_error_maps_to_billing_error(self):
        """Test billing_error maps to BillingError."""
        assert get_api_error_class("billing_error") == BillingError

    def test_rate_limit_maps_to_rate_limit_error(self):
        """Test rate_limit maps to RateLimitError."""
        assert get_api_error_class("rate_limit") == RateLimitError

    def test_invalid_request_maps_to_invalid_request_error(self):
        """Test invalid_request maps to InvalidRequestError."""
        assert get_api_error_class("invalid_request") == InvalidRequestError

    def test_server_error_maps_to_server_error(self):
        """Test server_error maps to ServerError."""
        assert get_api_error_class("server_error") == ServerError

    def test_unknown_error_maps_to_api_error(self):
        """Test unknown error types map to base APIError."""
        assert get_api_error_class("unknown") == APIError
        assert get_api_error_class("some_new_error") == APIError


class TestMessageParserAPIErrors:
    """Tests for API error handling in message parser."""

    def test_raises_authentication_error(self):
        """Test that authentication_failed error raises AuthenticationError."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Invalid API key · Fix external API key"}
                ],
                "model": "claude-sonnet-4-5",
            },
            "error": "authentication_failed",
        }
        with pytest.raises(AuthenticationError) as exc_info:
            parse_message(data)

        assert "Invalid API key" in str(exc_info.value)
        assert exc_info.value.error_type == "authentication_failed"
        assert exc_info.value.model == "claude-sonnet-4-5"

    def test_raises_billing_error(self):
        """Test that billing_error raises BillingError."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Your credit balance is too low"}
                ],
                "model": "claude-sonnet-4-5",
            },
            "error": "billing_error",
        }
        with pytest.raises(BillingError) as exc_info:
            parse_message(data)

        assert "credit balance" in str(exc_info.value)
        assert exc_info.value.error_type == "billing_error"

    def test_raises_rate_limit_error(self):
        """Test that rate_limit error raises RateLimitError."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Rate limit exceeded, please retry"}
                ],
                "model": "claude-opus-4-5",
            },
            "error": "rate_limit",
        }
        with pytest.raises(RateLimitError) as exc_info:
            parse_message(data)

        assert "Rate limit" in str(exc_info.value)
        assert exc_info.value.error_type == "rate_limit"
        assert exc_info.value.model == "claude-opus-4-5"

    def test_raises_invalid_request_error(self):
        """Test that invalid_request error raises InvalidRequestError."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "API Error: 400 The provided model identifier is invalid."}
                ],
                "model": "<synthetic>",
            },
            "error": "invalid_request",
        }
        with pytest.raises(InvalidRequestError) as exc_info:
            parse_message(data)

        assert "model identifier is invalid" in str(exc_info.value)
        assert exc_info.value.error_type == "invalid_request"

    def test_raises_server_error(self):
        """Test that server_error raises ServerError."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "API Error: Repeated 529 Overloaded errors"}
                ],
                "model": "claude-sonnet-4-5",
            },
            "error": "server_error",
        }
        with pytest.raises(ServerError) as exc_info:
            parse_message(data)

        assert "529" in str(exc_info.value) or "Overloaded" in str(exc_info.value)
        assert exc_info.value.error_type == "server_error"

    def test_raises_unknown_error_as_api_error(self):
        """Test that unknown error type raises base APIError."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Some unknown error occurred"}
                ],
                "model": "claude-sonnet-4-5",
            },
            "error": "unknown",
        }
        with pytest.raises(APIError) as exc_info:
            parse_message(data)

        # Should be base APIError, not a subclass
        assert type(exc_info.value) == APIError
        assert exc_info.value.error_type == "unknown"

    def test_no_error_returns_assistant_message(self):
        """Test that messages without error field return AssistantMessage normally."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello! How can I help you?"}
                ],
                "model": "claude-sonnet-4-5",
            },
        }
        result = parse_message(data)

        assert isinstance(result, AssistantMessage)
        assert result.content[0].text == "Hello! How can I help you?"
        assert result.error is None

    def test_error_field_at_top_level_not_in_message(self):
        """Test that error field is read from top level (fix for #505).

        The CLI returns error at the top level of the JSON, not nested
        inside the message object.
        """
        # Error at top level (correct location per CLI output)
        data_correct = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Auth failed"}],
                "model": "claude-sonnet-4-5",
            },
            "error": "authentication_failed",  # Top level - correct
        }
        with pytest.raises(AuthenticationError):
            parse_message(data_correct)

        # Error nested in message (incorrect, but some might expect this)
        data_nested = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello"}],
                "model": "claude-sonnet-4-5",
                "error": "authentication_failed",  # Nested - should be ignored
            },
        }
        # Should NOT raise, because error is in wrong location
        result = parse_message(data_nested)
        assert isinstance(result, AssistantMessage)

    def test_error_with_empty_content(self):
        """Test handling error with empty content array."""
        data = {
            "type": "assistant",
            "message": {
                "content": [],
                "model": "claude-sonnet-4-5",
            },
            "error": "rate_limit",
        }
        with pytest.raises(RateLimitError) as exc_info:
            parse_message(data)

        # Should use default message when no text content
        assert exc_info.value.error_type == "rate_limit"

    def test_error_with_non_text_content(self):
        """Test handling error with only non-text content blocks."""
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "Read",
                        "input": {"path": "/test"},
                    }
                ],
                "model": "claude-sonnet-4-5",
            },
            "error": "invalid_request",
        }
        with pytest.raises(InvalidRequestError) as exc_info:
            parse_message(data)

        # Should use default message
        assert exc_info.value.error_type == "invalid_request"

    def test_all_api_errors_catchable_as_api_error(self):
        """Test that all specific errors can be caught as APIError."""
        error_types = [
            ("authentication_failed", AuthenticationError),
            ("billing_error", BillingError),
            ("rate_limit", RateLimitError),
            ("invalid_request", InvalidRequestError),
            ("server_error", ServerError),
        ]

        for error_type, expected_class in error_types:
            data = {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": f"Error: {error_type}"}],
                    "model": "claude-sonnet-4-5",
                },
                "error": error_type,
            }

            # Can catch as specific class
            with pytest.raises(expected_class):
                parse_message(data)

            # Can also catch as base APIError
            with pytest.raises(APIError):
                parse_message(data)


class TestAPIErrorIntegration:
    """Integration tests for API error handling patterns."""

    def test_retry_pattern_for_rate_limit(self):
        """Test that RateLimitError enables retry pattern."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Rate limit exceeded"}],
                "model": "claude-sonnet-4-5",
            },
            "error": "rate_limit",
        }

        retries = 0
        max_retries = 3

        for _ in range(max_retries):
            try:
                parse_message(data)
            except RateLimitError:
                retries += 1
                continue

        assert retries == max_retries

    def test_catch_all_api_errors(self):
        """Test catching all API errors with single except clause."""
        error_types = [
            "authentication_failed",
            "billing_error",
            "rate_limit",
            "invalid_request",
            "server_error",
            "unknown",
        ]

        for error_type in error_types:
            data = {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Error"}],
                    "model": "claude-sonnet-4-5",
                },
                "error": error_type,
            }

            caught = False
            try:
                parse_message(data)
            except APIError as e:
                caught = True
                assert e.error_type == error_type

            assert caught, f"Failed to catch {error_type} as APIError"
