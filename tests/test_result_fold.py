"""Tests for folding a run's per-turn ``result`` frames into one."""

import pytest

from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk._internal.result_fold import (
    add_usage,
    fold_turn_results,
    is_turn_content,
)
from claude_agent_sdk.types import ResultMessage


def _result(**overrides):
    frame = {
        "type": "result",
        "subtype": "success",
        "duration_ms": 100,
        "duration_api_ms": 80,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s1",
        "result": "ok",
        "total_cost_usd": 0.01,
        "permission_denials": [],
    }
    frame.update(overrides)
    return frame


class TestFoldTurnResults:
    def test_single_result_is_returned_as_it_is(self):
        only = _result()
        assert fold_turn_results([only]) is only

    def test_no_results_is_an_error(self):
        with pytest.raises(ValueError):
            fold_turn_results([])

    def test_text_comes_from_the_last_result_when_every_turn_succeeded(self):
        folded = fold_turn_results([_result(result="a"), _result(result="b")])
        assert folded["result"] == "b"
        assert folded["subtype"] == "success"

    def test_first_failed_turn_supplies_text_subtype_and_error_fields(self):
        failed = _result(
            subtype="error_during_execution",
            is_error=True,
            errors=["boom"],
            result=None,
            api_error_status=529,
        )
        folded = fold_turn_results([_result(result="a"), failed, _result(result="c")])
        assert folded["subtype"] == "error_during_execution"
        assert folded["is_error"] is True
        assert folded["errors"] == ["boom"]
        assert folded["api_error_status"] == 529
        assert folded["result"] is None

    def test_api_failure_reported_as_success_subtype_still_counts_as_failed(self):
        failed = _result(is_error=True, result="API Error: overloaded")
        folded = fold_turn_results([failed, _result(result="later")])
        assert folded["is_error"] is True
        assert folded["result"] == "API Error: overloaded"

    def test_structured_output_follows_the_base_result(self):
        folded = fold_turn_results(
            [
                _result(structured_output={"turn": 1}),
                _result(structured_output={"turn": 2}),
            ]
        )
        assert folded["structured_output"] == {"turn": 2}

    def test_session_cumulative_fields_take_the_last_value(self):
        folded = fold_turn_results(
            [
                _result(
                    total_cost_usd=0.01,
                    duration_api_ms=80,
                    modelUsage={"m": {"inputTokens": 1}},
                    result_index=0,
                ),
                _result(
                    total_cost_usd=0.03,
                    duration_api_ms=200,
                    modelUsage={"m": {"inputTokens": 4}},
                    result_index=1,
                ),
            ]
        )
        assert folded["total_cost_usd"] == 0.03
        assert folded["duration_api_ms"] == 200
        assert folded["modelUsage"] == {"m": {"inputTokens": 4}}
        assert folded["result_index"] == 1

    def test_a_cumulative_field_the_last_result_lacks_is_not_carried_over(self):
        base = _result(subtype="error_max_turns", is_error=True, result_index=0)
        folded = fold_turn_results([base, _result()])
        assert "result_index" not in folded

    def test_per_turn_counters_are_summed(self):
        folded = fold_turn_results(
            [
                _result(num_turns=2, duration_ms=100),
                _result(num_turns=3, duration_ms=250),
            ]
        )
        assert folded["num_turns"] == 5
        assert folded["duration_ms"] == 350

    def test_usage_is_summed_recursively(self):
        folded = fold_turn_results(
            [
                _result(
                    usage={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "service_tier": "standard",
                        "server_tool_use": {"web_search_requests": 1},
                    }
                ),
                _result(
                    usage={
                        "input_tokens": 7,
                        "output_tokens": 3,
                        "service_tier": "priority",
                        "server_tool_use": {"web_search_requests": 2},
                    }
                ),
            ]
        )
        assert folded["usage"] == {
            "input_tokens": 17,
            "output_tokens": 8,
            "service_tier": "priority",
            "server_tool_use": {"web_search_requests": 3},
        }

    def test_usage_is_left_alone_when_no_turn_reported_any(self):
        folded = fold_turn_results([_result(), _result()])
        assert "usage" not in folded

    def test_a_turn_without_usage_does_not_hide_the_others(self):
        folded = fold_turn_results([_result(usage={"input_tokens": 4}), _result()])
        assert folded["usage"] == {"input_tokens": 4}

    def test_permission_denials_are_concatenated(self):
        folded = fold_turn_results(
            [
                _result(permission_denials=[{"tool_name": "A"}]),
                _result(permission_denials=[]),
                _result(permission_denials=[{"tool_name": "B"}]),
            ]
        )
        assert folded["permission_denials"] == [{"tool_name": "A"}, {"tool_name": "B"}]

    def test_turn_results_carry_every_turn_in_order(self):
        first, second = _result(result="a"), _result(result="b")
        folded = fold_turn_results([first, second])
        assert folded["turn_results"] == [first, second]

    def test_inputs_are_not_mutated(self):
        first, second = _result(result="a"), _result(result="b")
        fold_turn_results([first, second])
        assert "turn_results" not in first and "turn_results" not in second


class TestAddUsage:
    def test_numbers_add(self):
        assert add_usage({"a": 1, "b": 2.5}, {"a": 4, "b": 0.5}) == {"a": 5, "b": 3.0}

    def test_keys_present_on_one_side_are_kept(self):
        assert add_usage({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_labels_take_the_later_value(self):
        assert add_usage({"tier": "x"}, {"tier": "y"}) == {"tier": "y"}

    def test_booleans_are_not_added(self):
        assert add_usage({"flag": True}, {"flag": False}) == {"flag": False}


class TestIsTurnContent:
    @pytest.mark.parametrize(
        "type_", ["assistant", "user", "stream_event", "tool_progress"]
    )
    def test_turn_output(self, type_):
        assert is_turn_content({"type": type_})

    @pytest.mark.parametrize(
        "message",
        [
            {"type": "system", "subtype": "task_notification"},
            {"type": "system", "subtype": "session_state_changed"},
            {"type": "rate_limit_event"},
            {"type": "prompt_suggestion"},
            {},
        ],
    )
    def test_between_turns_notices(self, message):
        assert not is_turn_content(message)


class TestFoldedResultParsing:
    def test_turn_results_parse_into_result_messages(self):
        first, second = _result(result="a"), _result(result="b")
        folded = parse_message(fold_turn_results([first, second]))

        assert isinstance(folded, ResultMessage)
        assert folded.num_turns == 2
        assert folded.turn_results is not None
        assert all(isinstance(t, ResultMessage) for t in folded.turn_results)
        assert [t.result for t in folded.turn_results] == ["a", "b"]
        assert all(t.turn_results is None for t in folded.turn_results)

    def test_a_plain_result_has_no_turn_results(self):
        assert parse_message(_result()).turn_results is None
