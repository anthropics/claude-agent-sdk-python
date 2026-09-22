"""Fold the per-turn results of a one-shot run into the one ``query()`` yields.

A CLI that honours the ``end_user_input`` declaration runs a string prompt the
way ``claude -p`` does: one ``result`` per turn (the prompt's, then each turn a
background subagent's completion wakes), the run ending when the CLI closes
stdout. ``query()`` yields one final result for a string prompt, so those
per-turn results are folded into one here.
"""

from dataclasses import replace
from functools import reduce
from typing import Any

from ..types import ResultMessage


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
            merged[key] = add_usage(total.get(key), value)
        return merged
    if _is_number(total) and _is_number(part):
        return total + part
    return part


def _failed(result: ResultMessage) -> bool:
    return result.subtype != "success" or result.is_error


def fold_turn_results(results: list[ResultMessage]) -> ResultMessage:
    """Fold a run's per-turn results into one.

    Text, subtype, the error fields and ``structured_output`` come from the
    first failed result when there is one, so a successful follow-up turn never
    hides an earlier error, and otherwise from the last. ``usage``,
    ``num_turns`` and ``duration_ms`` are per turn and are summed;
    ``permission_denials`` are concatenated. ``total_cost_usd``,
    ``duration_api_ms`` and ``model_usage`` are already cumulative over the
    session, so the last result's values stand. The per-turn results travel
    along as ``turn_results``. A single result is returned as it is.
    """
    if len(results) == 1:
        return results[0]

    final = results[-1]
    usages = [r.usage for r in results if isinstance(r.usage, dict)]
    return replace(
        next((r for r in results if _failed(r)), final),
        total_cost_usd=final.total_cost_usd,
        duration_api_ms=final.duration_api_ms,
        model_usage=final.model_usage,
        usage=reduce(add_usage, usages) if usages else None,
        num_turns=sum(r.num_turns for r in results),
        duration_ms=sum(r.duration_ms for r in results),
        permission_denials=[d for r in results for d in r.permission_denials or []],
        turn_results=list(results),
    )
