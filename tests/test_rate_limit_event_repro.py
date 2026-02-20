"""Repro test: rate_limit_event message type crashes the Python Agent SDK.

CLI v2.1.45+ emits `rate_limit_event` messages when rate limit status changes
for claude.ai subscription users. The Python SDK's message parser had no handler
for this message type, causing a MessageParseError crash.

Fix: the parser now returns None for unknown message types, and the caller
filters them out. This makes the SDK forward-compatible with new CLI message types.

See: https://github.com/anthropics/claude-agent-sdk-python/issues/583
"""

from claude_agent_sdk._internal.message_parser import parse_message


class TestRateLimitEventHandling:
    """Verify rate_limit_event and unknown message types don't crash."""

    def test_rate_limit_event_returns_none(self):
        """rate_limit_event should be silently skipped, not crash."""
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
        assert result is None

    def test_rate_limit_event_rejected_returns_none(self):
        """Hard rate limit (status=rejected) should also be skipped."""
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
        assert result is None

    def test_unknown_message_type_returns_none(self):
        """Any unknown message type should return None for forward compatibility."""
        data = {
            "type": "some_future_event_type",
            "uuid": "770e8400-e29b-41d4-a716-446655440002",
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
