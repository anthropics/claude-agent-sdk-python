"""Tests for structured output normalization.

Tests the fixes for:
- Issue #502: Model wraps data in 'output'/'response'/'json' field
- Issue #510: Model serializes arrays as JSON strings
"""

import pytest

from claude_agent_sdk._internal.message_parser import (
    _normalize_structured_output,
    _parse_stringified_json,
    parse_message,
)
from claude_agent_sdk.types import ResultMessage


class TestNormalizeStructuredOutput:
    """Tests for _normalize_structured_output function."""

    def test_returns_none_for_none_input(self):
        """Test that None input returns None."""
        assert _normalize_structured_output(None) is None

    def test_unwraps_output_wrapper(self):
        """Test unwrapping {'output': {...}} wrapper (Issue #502)."""
        wrapped = {"output": {"actions": [1, 2, 3]}}
        result = _normalize_structured_output(wrapped)
        assert result == {"actions": [1, 2, 3]}

    def test_unwraps_response_wrapper(self):
        """Test unwrapping {'response': {...}} wrapper (Issue #502)."""
        wrapped = {"response": {"actions": [1, 2, 3]}}
        result = _normalize_structured_output(wrapped)
        assert result == {"actions": [1, 2, 3]}

    def test_unwraps_json_wrapper(self):
        """Test unwrapping {'json': {...}} wrapper (Issue #502)."""
        wrapped = {"json": {"actions": [1, 2, 3]}}
        result = _normalize_structured_output(wrapped)
        assert result == {"actions": [1, 2, 3]}

    def test_unwraps_data_wrapper(self):
        """Test unwrapping {'data': {...}} wrapper."""
        wrapped = {"data": {"actions": [1, 2, 3]}}
        result = _normalize_structured_output(wrapped)
        assert result == {"actions": [1, 2, 3]}

    def test_unwraps_result_wrapper(self):
        """Test unwrapping {'result': {...}} wrapper."""
        wrapped = {"result": {"actions": [1, 2, 3]}}
        result = _normalize_structured_output(wrapped)
        assert result == {"actions": [1, 2, 3]}

    def test_case_insensitive_wrapper_keys(self):
        """Test that wrapper key detection is case-insensitive."""
        wrapped = {"OUTPUT": {"actions": [1, 2, 3]}}
        result = _normalize_structured_output(wrapped)
        assert result == {"actions": [1, 2, 3]}

    def test_does_not_unwrap_multi_key_dict(self):
        """Test that dicts with multiple keys are not unwrapped."""
        not_wrapped = {"output": {"a": 1}, "other": {"b": 2}}
        result = _normalize_structured_output(not_wrapped)
        assert result == {"output": {"a": 1}, "other": {"b": 2}}

    def test_does_not_unwrap_non_wrapper_single_key(self):
        """Test that single-key dicts with non-wrapper keys are not unwrapped."""
        not_wrapped = {"actions": [1, 2, 3]}
        result = _normalize_structured_output(not_wrapped)
        assert result == {"actions": [1, 2, 3]}

    def test_preserves_valid_structured_output(self):
        """Test that already-valid structured output is preserved."""
        valid = {"actions": [{"type": "click", "target": "button"}]}
        result = _normalize_structured_output(valid)
        assert result == valid


class TestParseStringifiedJson:
    """Tests for _parse_stringified_json function."""

    def test_parses_stringified_array(self):
        """Test parsing stringified JSON array (Issue #510)."""
        stringified = '[{"field": "value"}, {"field": "other"}]'
        result = _parse_stringified_json(stringified)
        assert result == [{"field": "value"}, {"field": "other"}]

    def test_parses_stringified_object(self):
        """Test parsing stringified JSON object."""
        stringified = '{"key": "value", "number": 42}'
        result = _parse_stringified_json(stringified)
        assert result == {"key": "value", "number": 42}

    def test_parses_nested_stringified_json(self):
        """Test parsing nested stringified JSON in a dict value (Issue #510)."""
        data = {
            "name": "Section",
            "items": '[{"field": {"text": "hello"}}]',
        }
        result = _parse_stringified_json(data)
        assert result == {
            "name": "Section",
            "items": [{"field": {"text": "hello"}}],
        }

    def test_parses_deeply_nested_stringified_json(self):
        """Test parsing deeply nested stringified JSON."""
        data = {
            "level1": {
                "level2": '{"level3": [1, 2, 3]}',
            }
        }
        result = _parse_stringified_json(data)
        assert result == {
            "level1": {
                "level2": {"level3": [1, 2, 3]},
            }
        }

    def test_preserves_regular_strings(self):
        """Test that regular strings are preserved."""
        assert _parse_stringified_json("hello world") == "hello world"
        assert _parse_stringified_json("not json") == "not json"

    def test_preserves_invalid_json_strings(self):
        """Test that invalid JSON-looking strings are preserved."""
        invalid = "[this is not valid json]"
        assert _parse_stringified_json(invalid) == invalid

    def test_handles_empty_array_string(self):
        """Test parsing empty array string."""
        assert _parse_stringified_json("[]") == []

    def test_handles_empty_object_string(self):
        """Test parsing empty object string."""
        assert _parse_stringified_json("{}") == {}

    def test_handles_whitespace_around_json(self):
        """Test parsing JSON with surrounding whitespace."""
        result = _parse_stringified_json('  [1, 2, 3]  ')
        assert result == [1, 2, 3]


class TestCombinedNormalization:
    """Tests for combined wrapper unwrapping and JSON parsing."""

    def test_unwraps_and_parses_combined(self):
        """Test unwrapping wrapper AND parsing stringified JSON inside."""
        data = {
            "output": {
                "name": "Section",
                "items": '[{"field": "value"}]',
            }
        }
        result = _normalize_structured_output(data)
        assert result == {
            "name": "Section",
            "items": [{"field": "value"}],
        }

    def test_complex_real_world_case(self):
        """Test a complex real-world case from Issue #510."""
        # This simulates what the model sometimes returns
        data = {
            "output": {
                "name": "Example Section",
                "items": '[{"field": {"text": "Sample", "field_id": "F1"}, "metadata": [{"id": "M1"}]}]',
            }
        }
        result = _normalize_structured_output(data)
        assert result == {
            "name": "Example Section",
            "items": [
                {
                    "field": {"text": "Sample", "field_id": "F1"},
                    "metadata": [{"id": "M1"}],
                }
            ],
        }


class TestResultMessageParsing:
    """Tests for ResultMessage parsing with structured output normalization."""

    def test_parse_result_message_with_wrapped_output(self):
        """Test that ResultMessage parsing normalizes wrapped structured_output."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 800,
            "is_error": False,
            "num_turns": 3,
            "session_id": "test-session",
            "structured_output": {"output": {"actions": [1, 2, 3]}},
        }
        result = parse_message(data)
        assert isinstance(result, ResultMessage)
        assert result.structured_output == {"actions": [1, 2, 3]}

    def test_parse_result_message_with_stringified_json(self):
        """Test that ResultMessage parsing handles stringified JSON in structured_output."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 800,
            "is_error": False,
            "num_turns": 3,
            "session_id": "test-session",
            "structured_output": {
                "items": '[{"field": "value"}]',
            },
        }
        result = parse_message(data)
        assert isinstance(result, ResultMessage)
        assert result.structured_output == {"items": [{"field": "value"}]}

    def test_parse_result_message_preserves_valid_output(self):
        """Test that valid structured_output is preserved unchanged."""
        expected_output = {"actions": [{"type": "click"}]}
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 800,
            "is_error": False,
            "num_turns": 3,
            "session_id": "test-session",
            "structured_output": expected_output,
        }
        result = parse_message(data)
        assert isinstance(result, ResultMessage)
        assert result.structured_output == expected_output

    def test_parse_result_message_with_none_output(self):
        """Test that None structured_output remains None."""
        data = {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1000,
            "duration_api_ms": 800,
            "is_error": False,
            "num_turns": 3,
            "session_id": "test-session",
            "structured_output": None,
        }
        result = parse_message(data)
        assert isinstance(result, ResultMessage)
        assert result.structured_output is None
