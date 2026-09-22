"""Fold the per-turn results of a one-shot run into the one ``query()`` yields.

A CLI that honours the ``end_user_input`` declaration runs a string prompt the
way ``claude -p`` does: one ``result`` per turn (the prompt's, then each turn a
background subagent's completion wakes), the run ending when the CLI closes
stdout. ``query()`` yields one final result for a string prompt, so those
per-turn results are folded into one here.
"""

from functools import reduce
from typing import Any

# Wire-message types that make up a turn's output. A message of any other type
# that follows a held result (a prompt suggestion, a task notification, a
# rate-limit event) is a notice between turns, not part of the next one.
_TURN_CONTENT_TYPES = frozenset({"assistant", "user", "stream_event", "tool_progress"})

# Cumulative over the session, so the last turn's value already covers the
# earlier ones and is taken as it stands.
_CUMULATIVE_KEYS = (
    "total_cost_usd",
    "duration_api_ms",
    "modelUsage",
    "subagent_stats",
    "result_index",
)


def is_turn_content(message: dict[str, Any]) -> bool:
    """Whether ``message`` is output of a turn (as opposed to a between-turns notice)."""
    return message.get("type") in _TURN_CONTENT_TYPES


def is_turn_end_marker(message: dict[str, Any]) -> bool:
    """Whether ``message`` is the session-state marker that follows a turn.

    It reports the state after the turn's result, so it stays behind that
    result; every other notice is delivered as it arrives.
    """
    return (
        message.get("type") == "system"
        and message.get("subtype") == "session_state_changed"
    )


def add_usage(total: Any, part: Any) -> Any:
    """Add two ``usage`` payloads field by field.

    Numbers add up, nested objects (``cache_creation``, ``server_tool_use``)
    add up the same way, and anything else (``service_tier`` and other labels)
    takes the later value — as the CLI does when it accumulates usage across
    turns. The TypeScript SDK folds the same way, so a consumer of either SDK
    reads the same numbers.
    """
    if isinstance(total, dict) and isinstance(part, dict):
        merged = dict(total)
        for key, value in part.items():
            merged[key] = add_usage(total[key], value) if key in total else value
        return merged
    if (
        isinstance(total, (int, float))
        and isinstance(part, (int, float))
        and not isinstance(total, bool)
        and not isinstance(part, bool)
    ):
        return total + part
    return part


def _failed(result: dict[str, Any]) -> bool:
    return result.get("subtype") != "success" or bool(result.get("is_error"))


def fold_turn_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold a run's per-turn ``result`` frames into one.

    Text, subtype, the error fields and ``structured_output`` come from the
    first failed result when there is one, so a successful follow-up turn never
    hides an earlier error, and otherwise from the last. ``usage``,
    ``num_turns`` and ``duration_ms`` are per turn and are summed;
    ``permission_denials`` are concatenated. ``total_cost_usd``,
    ``duration_api_ms``, ``modelUsage`` and ``subagent_stats`` are already
    cumulative over the session, and ``result_index`` is the run's sequence, so
    the last result's values stand. The per-turn results travel along as
    ``turn_results``. A single result is returned as it is.
    """
    if not results:
        raise ValueError("fold_turn_results: no results to fold")
    if len(results) == 1:
        return results[0]

    final = results[-1]
    base = next((r for r in results if _failed(r)), final)
    folded = dict(base)
    for key in _CUMULATIVE_KEYS:
        if key in final:
            folded[key] = final[key]
        else:
            folded.pop(key, None)
    usages = [r["usage"] for r in results if isinstance(r.get("usage"), dict)]
    if usages:
        folded["usage"] = reduce(add_usage, usages)
    folded["num_turns"] = sum(r.get("num_turns", 0) for r in results)
    folded["duration_ms"] = sum(r.get("duration_ms", 0) for r in results)
    folded["permission_denials"] = [
        denial for r in results for denial in (r.get("permission_denials") or [])
    ]
    folded["turn_results"] = list(results)
    return folded
