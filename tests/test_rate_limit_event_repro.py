"""Test that rate_limit_event messages are parsed into typed RateLimitEvent.

CLI v2.1.45+ emits `rate_limit_event` messages when rate limit status changes
for claude.ai subscription users. The Python SDK's message parser originally
had no handler for this message type and crashed with MessageParseError. It was
then patched to silently return None (#598). This test verifies the full fix:
the parser now returns a typed RateLimitEvent so callers can act on warnings.

See: https://github.com/anthropics/claude-agent-sdk-python/issues/583
"""

from claude_agent_sdk import RateLimitEvent, RateLimitInfo
from claude_agent_sdk._internal.message_parser import parse_message


class TestRateLimitEventHandling:
    """Verify rate_limit_event is parsed into a typed message."""

    def test_rate_limit_event_parsed_as_typed_message(self):
        """allowed_warning status should be parsed into a RateLimitEvent."""
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed_warning",
                "resetsAt": 1700000000,
                "rateLimitType": "five_hour",
                "utilization": 0.85,
                "isUsingOverage": False,
            },
            "uuid": "550e8400-e29b-41d4-a716-446655440000",
            "session_id": "test-session-id",
        }

        result = parse_message(data)
        assert isinstance(result, RateLimitEvent)
        assert result.uuid == "550e8400-e29b-41d4-a716-446655440000"
        assert result.session_id == "test-session-id"

        info = result.rate_limit_info
        assert isinstance(info, RateLimitInfo)
        assert info.status == "allowed_warning"
        assert info.resets_at == 1700000000
        assert info.rate_limit_type == "five_hour"
        assert info.utilization == 0.85
        # Unmodeled field preserved in raw
        assert info.raw["isUsingOverage"] is False

    def test_rate_limit_event_rejected_parsed(self):
        """Hard rate limit (status=rejected) with overage info."""
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1700003600,
                "rateLimitType": "seven_day",
                "isUsingOverage": False,
                "overageStatus": "rejected",
                "overageDisabledReason": "out_of_credits",
            },
            "uuid": "660e8400-e29b-41d4-a716-446655440001",
            "session_id": "test-session-id",
        }

        result = parse_message(data)
        assert isinstance(result, RateLimitEvent)
        assert result.rate_limit_info.status == "rejected"
        assert result.rate_limit_info.overage_status == "rejected"
        assert result.rate_limit_info.overage_disabled_reason == "out_of_credits"

    def test_rate_limit_event_minimal_fields(self):
        """Only status is required; optional fields default to None."""
        data = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed"},
            "uuid": "770e8400-e29b-41d4-a716-446655440002",
            "session_id": "test-session-id",
        }

        result = parse_message(data)
        assert isinstance(result, RateLimitEvent)
        assert result.rate_limit_info.status == "allowed"
        assert result.rate_limit_info.resets_at is None
        assert result.rate_limit_info.rate_limit_type is None

    def test_unknown_message_type_returns_none(self):
        """Truly unknown message types still return None for forward compat."""
        data = {
            "type": "some_future_event_type",
            "uuid": "880e8400-e29b-41d4-a716-446655440003",
            "session_id": "test-session-id",
        }

        result = parse_message(data)
        assert result is None

    def test_known_message_types_still_parsed(self):
        """Known message types should still be parsed normally."""
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hello"}],
                "model": "claude-sonnet-4-6-20250929",
            },
        }

        result = parse_message(data)
        assert result is not None
        assert result.content[0].text == "hello"
