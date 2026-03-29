#!/usr/bin/env python
"""session_monitor.py — behavioral consistency monitoring for long SDK sessions.

This example stays on the public SDK surface:

- `HookMatcher`-based `PreToolUse` / `PostToolUse` callbacks
- `ClaudeSDKClient.query()` + `receive_response()` for turns
- `ClaudeSDKClient.get_context_usage()` for context-window telemetry

Together, those are enough to build a lightweight monitor for long-running
sessions where context compaction or summarization may silently change the
agent's behavior.

Reference: https://github.com/anthropics/claude-agent-sdk-python/issues/772
"""

import asyncio
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
from claude_agent_sdk.types import (
    AssistantMessage,
    HookContext,
    HookJSONOutput,
    PostToolUseHookInput,
    PreToolUseHookInput,
    ResultMessage,
    TextBlock,
)


@dataclass
class BehavioralSnapshot:
    """What the agent looks like at one point in the session."""

    turn: int
    tokens: int
    timestamp: float
    tool_counts: Counter = field(default_factory=Counter)
    vocabulary: set[str] = field(default_factory=set)


class SessionMonitor:
    """Track vocabulary and tool-use drift across a Claude SDK session."""

    def __init__(
        self,
        compaction_drop_ratio: float = 0.20,
        drift_threshold: float = 0.30,
        log_path: Optional[Path] = None,
    ) -> None:
        self.compaction_drop_ratio = compaction_drop_ratio
        self.drift_threshold = drift_threshold
        self.log_path = log_path

        self._baseline: Optional[BehavioralSnapshot] = None
        self._current: Optional[BehavioralSnapshot] = None
        self._turn = 0
        self._compaction_events: list[dict[str, Any]] = []
        self._drift_scores: list[float] = []
        self._pending_tool_counts: Counter = Counter()
        self._pending_vocabulary: set[str] = set()

    async def on_pre_tool_use(
        self,
        input_data: PreToolUseHookInput,
        tool_use_id: Optional[str],
        context: HookContext,
    ) -> HookJSONOutput:
        """Record each tool call before execution."""

        del tool_use_id, context
        self._pending_tool_counts[input_data["tool_name"]] += 1
        return {}

    async def on_post_tool_use(
        self,
        input_data: PostToolUseHookInput,
        tool_use_id: Optional[str],
        context: HookContext,
    ) -> HookJSONOutput:
        """Capture vocabulary emitted by tool results."""

        del tool_use_id, context
        tool_response = str(input_data.get("tool_response", ""))
        words = set(re.findall(r"\b[a-zA-Z_]\w{3,}\b", tool_response.lower()))
        self._pending_vocabulary.update(words)
        return {}

    def record_turn(self, message_text: str, total_tokens: int) -> Optional[dict[str, Any]]:
        """Record a completed turn and return any detected event."""

        self._turn += 1
        words = set(re.findall(r"\b[a-zA-Z_]\w{3,}\b", message_text.lower()))
        prev_tokens = self._current.tokens if self._current else 0

        self._current = BehavioralSnapshot(
            turn=self._turn,
            tokens=total_tokens,
            timestamp=time.time(),
            tool_counts=Counter(self._pending_tool_counts),
            vocabulary=words | self._pending_vocabulary,
        )

        self._pending_tool_counts.clear()
        self._pending_vocabulary.clear()

        if self._baseline is None and total_tokens > 0:
            self._baseline = BehavioralSnapshot(
                turn=self._turn,
                tokens=total_tokens,
                timestamp=self._current.timestamp,
                tool_counts=Counter(self._current.tool_counts),
                vocabulary=set(self._current.vocabulary),
            )
            return None

        if self._baseline is None:
            return None

        compaction_detected = False
        if prev_tokens > 0 and total_tokens < prev_tokens * (1 - self.compaction_drop_ratio):
            compaction_detected = True
            event = {
                "event": "compaction_suspected",
                "turn": self._turn,
                "tokens_before": prev_tokens,
                "tokens_after": total_tokens,
                "drop_ratio": round(1.0 - total_tokens / prev_tokens, 3),
                "timestamp": self._current.timestamp,
            }
            self._compaction_events.append(event)
            self._log(event)

        ccs = self._compute_ccs()
        self._drift_scores.append(ccs)

        if ccs < (1.0 - self.drift_threshold) or compaction_detected:
            event = {
                "event": "post_compaction_drift" if compaction_detected else "behavioral_drift",
                "turn": self._turn,
                "ccs": round(ccs, 3),
                "compaction_at_this_turn": compaction_detected,
                "ghost_terms": self._ghost_terms(),
                "tool_shift": self._tool_shift_summary(),
            }
            self._log(event)
            return event

        return None

    def _compute_ccs(self) -> float:
        """Context Consistency Score: 1.0 means no behavioral change."""

        return 0.6 * self._vocab_overlap() + 0.4 * self._tool_consistency()

    def _vocab_overlap(self) -> float:
        if not self._baseline or not self._baseline.vocabulary or not self._current:
            return 1.0
        if not self._current.vocabulary:
            return 1.0
        intersection = self._baseline.vocabulary & self._current.vocabulary
        union = self._baseline.vocabulary | self._current.vocabulary
        return len(intersection) / len(union) if union else 1.0

    def _ghost_terms(self) -> list[str]:
        if not self._baseline or not self._current:
            return []
        return sorted(self._baseline.vocabulary - self._current.vocabulary)[:20]

    def _tool_consistency(self) -> float:
        if not self._baseline or not self._current:
            return 1.0
        if not self._baseline.tool_counts or not self._current.tool_counts:
            return 1.0

        all_tools = set(self._baseline.tool_counts) | set(self._current.tool_counts)
        baseline_total = sum(self._baseline.tool_counts.values()) or 1
        current_total = sum(self._current.tool_counts.values()) or 1
        baseline_distribution = {
            tool: self._baseline.tool_counts.get(tool, 0) / baseline_total
            for tool in all_tools
        }
        current_distribution = {
            tool: self._current.tool_counts.get(tool, 0) / current_total
            for tool in all_tools
        }
        midpoint = {
            tool: 0.5 * (baseline_distribution[tool] + current_distribution[tool])
            for tool in all_tools
        }

        def kl_divergence(lhs: dict[str, float], rhs: dict[str, float]) -> float:
            return sum(
                lhs[tool] * math.log(lhs[tool] / rhs[tool] + 1e-10)
                for tool in all_tools
                if lhs[tool] > 0
            )

        jsd = 0.5 * kl_divergence(baseline_distribution, midpoint) + 0.5 * kl_divergence(
            current_distribution, midpoint
        )
        return max(0.0, 1.0 - jsd)

    def _tool_shift_summary(self) -> dict[str, dict[str, int]]:
        if not self._baseline or not self._current:
            return {}
        all_tools = set(self._baseline.tool_counts) | set(self._current.tool_counts)
        return {
            tool: {
                "baseline": self._baseline.tool_counts.get(tool, 0),
                "current": self._current.tool_counts.get(tool, 0),
            }
            for tool in all_tools
        }

    def summary(self) -> dict[str, Any]:
        return {
            "turns": self._turn,
            "compaction_events": len(self._compaction_events),
            "avg_ccs": round(sum(self._drift_scores) / len(self._drift_scores), 3)
            if self._drift_scores
            else None,
            "min_ccs": round(min(self._drift_scores), 3) if self._drift_scores else None,
            "compaction_detail": self._compaction_events,
        }

    def _log(self, event: dict[str, Any]) -> None:
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
        else:
            print(f"[session_monitor] {json.dumps(event)}")


async def run_monitored_turn(
    client: ClaudeSDKClient,
    monitor: SessionMonitor,
    prompt: str,
) -> Optional[dict[str, Any]]:
    """Run one SDK turn, then score it using public message + usage APIs."""

    await client.query(prompt)

    text_parts: list[str] = []
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage) and message.is_error:
            raise RuntimeError(message.result or "Claude SDK turn failed")

    usage = await client.get_context_usage()
    total_tokens = int(usage.get("totalTokens", 0))
    return monitor.record_turn(" ".join(text_parts), total_tokens)


async def main() -> None:
    monitor = SessionMonitor(
        compaction_drop_ratio=0.20,
        drift_threshold=0.30,
        log_path=None,
    )

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[monitor.on_pre_tool_use]),
            ],
            "PostToolUse": [
                HookMatcher(matcher="Bash", hooks=[monitor.on_post_tool_use]),
            ],
        },
    )

    prompts = [
        "Use Bash to print 'jwt bcrypt redis', then explain how those terms fit together in a web auth stack.",
        "Use Bash to print 'id,name\\n1,Ada', then explain how pandas would load this CSV.",
        "Use Bash to print '[0 1 2]', then explain numpy arrays in one short paragraph.",
    ]

    async with ClaudeSDKClient(options=options) as client:
        for prompt in prompts:
            event = await run_monitored_turn(client, monitor, prompt)
            if event:
                print(f"\n[session_monitor] Behavioral event: {json.dumps(event, indent=2)}")

    print("\n=== Session summary ===")
    print(json.dumps(monitor.summary(), indent=2))
    print()
    print("Note: native OnCompaction / OnContextThreshold hooks would still be better.")
    print("This sample shows the closest monitor you can build today with public hooks")
    print("plus get_context_usage() as the compaction-boundary heuristic.")


if __name__ == "__main__":
    asyncio.run(main())
