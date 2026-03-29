#!/usr/bin/env python
"""session_monitor.py — behavioral consistency monitoring using existing SDK hooks.

Demonstrates how to use the claude-agent-sdk-python hooks surface (PostToolUse,
PreToolUse, SessionStart) to build a lightweight behavioral fingerprint that
detects drift across long sessions.

Works with the current SDK surface today. The patterns here also motivate the
OnCompaction + OnContextThreshold hooks proposed in Issue #772, which would allow
earlier interception rather than inferring boundaries from token count changes.

Usage:
    python examples/session_monitor.py

What it shows:
  - Tracking tool call distribution across turns via PostToolUse
  - Detecting token-count drops between turns (heuristic compaction boundary)
  - Capturing a pre-session vocabulary baseline via SessionStart
  - Computing behavioral drift score: did the agent's output profile change?
  - Logging compaction-boundary events for downstream analysis

Context:
    Long-running agents hit context limits, triggering compaction/summarization.
    After compaction, the agent may lose task-specific vocabulary, shift its tool
    call mix, or change its response style — behavioral drift that is invisible to
    the user and often undetected by the agent itself. This example shows how to
    measure it using the hooks the SDK already has.

Reference: https://github.com/anthropics/claude-agent-sdk-python/issues/772
"""

import asyncio
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    HookContext,
    HookInput,
    HookJSONOutput,
    Message,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)


# ---------------------------------------------------------------------------
# Behavioral snapshot — what the agent looks like at one point in time
# ---------------------------------------------------------------------------

@dataclass
class BehavioralSnapshot:
    turn: int
    tokens: int
    timestamp: float
    tool_counts: Counter = field(default_factory=Counter)
    output_tokens: list[int] = field(default_factory=list)
    vocabulary: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Session monitor — accumulates snapshots, detects compaction, scores drift
# ---------------------------------------------------------------------------

class SessionMonitor:
    """
    Monitors behavioral consistency across a claude-agent-sdk session.

    Connects to the SDK via hook callbacks. Each hook updates an internal
    snapshot. After each turn, compute_drift() compares the current snapshot
    to the baseline and flags anomalies.
    """

    def __init__(
        self,
        compaction_drop_ratio: float = 0.20,   # token count drops > 20% → suspect compaction
        drift_threshold: float = 0.30,          # CCS below 0.70 → drift alert
        log_path: Optional[Path] = None,
    ):
        self.compaction_drop_ratio = compaction_drop_ratio
        self.drift_threshold = drift_threshold
        self.log_path = log_path

        self._baseline: Optional[BehavioralSnapshot] = None
        self._current: Optional[BehavioralSnapshot] = None
        self._turn = 0
        self._compaction_events: list[dict] = []
        self._drift_scores: list[float] = []
        self._pending_tool_counts: Counter = Counter()
        self._pending_vocabulary: set[str] = set()

    # -----------------------------------------------------------------------
    # Hook callbacks — wire these into ClaudeAgentOptions.hooks
    # -----------------------------------------------------------------------

    async def on_session_start(
        self, input_data: HookInput, tool_use_id: Optional[str], context: HookContext
    ) -> HookJSONOutput:
        """Capture the session's initial state as a baseline."""
        self._baseline = BehavioralSnapshot(turn=0, tokens=0, timestamp=time.time())
        self._current = BehavioralSnapshot(turn=0, tokens=0, timestamp=time.time())
        return {}

    async def on_pre_tool_use(
        self, input_data: HookInput, tool_use_id: Optional[str], context: HookContext
    ) -> HookJSONOutput:
        """Record each tool call before it executes."""
        tool_name = input_data.get("tool_name", "unknown")
        self._pending_tool_counts[tool_name] += 1
        return {}

    async def on_post_tool_use(
        self, input_data: HookInput, tool_use_id: Optional[str], context: HookContext
    ) -> HookJSONOutput:
        """Record tool output vocabulary — useful for detecting forgotten context."""
        tool_response = str(input_data.get("tool_response", ""))
        words = set(re.findall(r"\b[a-zA-Z_]\w{3,}\b", tool_response.lower()))
        self._pending_vocabulary.update(words)
        return {}

    # -----------------------------------------------------------------------
    # Call this after each agent turn with the turn's AssistantMessage tokens
    # -----------------------------------------------------------------------

    def record_turn(self, message_text: str, total_tokens: int) -> Optional[dict]:
        """
        Record a completed turn and check for compaction boundary + behavioral drift.

        Returns a dict describing any detected event (compaction or drift), or None.
        """
        self._turn += 1
        words = set(re.findall(r"\b[a-zA-Z_]\w{3,}\b", message_text.lower()))

        prev_tokens = self._current.tokens if self._current else 0

        # Update current snapshot
        self._current = BehavioralSnapshot(
            turn=self._turn,
            tokens=total_tokens,
            timestamp=time.time(),
            tool_counts=Counter(self._pending_tool_counts),
            vocabulary=words | self._pending_vocabulary,
        )

        # Reset accumulators
        self._pending_tool_counts.clear()
        self._pending_vocabulary.clear()

        # Detect compaction boundary: token count drops significantly
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

            # Re-baseline after compaction
            if self._baseline and self._baseline.tokens == 0:
                self._baseline = self._current
            
        # Seed baseline from first real turn
        if self._baseline and self._baseline.tokens == 0 and total_tokens > 0:
            self._baseline = BehavioralSnapshot(
                turn=self._turn,
                tokens=total_tokens,
                timestamp=self._current.timestamp,
                tool_counts=Counter(self._current.tool_counts),
                vocabulary=set(self._current.vocabulary),
            )
            return None  # Nothing to compare yet

        if not self._baseline or self._baseline.tokens == 0:
            return None

        # Compute behavioral drift score (Context Consistency Score)
        ccs = self._compute_ccs()
        self._drift_scores.append(ccs)

        result = None
        if ccs < (1.0 - self.drift_threshold) or compaction_detected:
            result = {
                "event": "behavioral_drift" if not compaction_detected else "post_compaction_drift",
                "turn": self._turn,
                "ccs": round(ccs, 3),
                "compaction_at_this_turn": compaction_detected,
                "ghost_terms": list(self._ghost_terms()),
                "tool_shift": self._tool_shift_summary(),
            }
            self._log(result)

        return result

    # -----------------------------------------------------------------------
    # Scoring helpers
    # -----------------------------------------------------------------------

    def _compute_ccs(self) -> float:
        """
        Context Consistency Score: [0, 1] where 1.0 = no behavioral change.

        Combines:
          - Vocabulary overlap: Jaccard similarity vs baseline
          - Tool distribution shift: Jensen-Shannon divergence (inverted)
        """
        vocab_score = self._vocab_overlap()
        tool_score = self._tool_consistency()
        return 0.6 * vocab_score + 0.4 * tool_score

    def _vocab_overlap(self) -> float:
        if not self._baseline.vocabulary or not self._current.vocabulary:
            return 1.0
        intersection = self._baseline.vocabulary & self._current.vocabulary
        union = self._baseline.vocabulary | self._current.vocabulary
        return len(intersection) / len(union) if union else 1.0

    def _ghost_terms(self) -> list[str]:
        """Terms present at baseline but absent from recent output — 'forgotten' vocabulary."""
        if not self._baseline or not self._current:
            return []
        return sorted(self._baseline.vocabulary - self._current.vocabulary)[:20]

    def _tool_consistency(self) -> float:
        """Jensen-Shannon divergence inverted: 1.0 = identical tool distribution."""
        if not self._baseline.tool_counts or not self._current.tool_counts:
            return 1.0
        all_tools = set(self._baseline.tool_counts) | set(self._current.tool_counts)
        base_total = sum(self._baseline.tool_counts.values()) or 1
        curr_total = sum(self._current.tool_counts.values()) or 1
        p = {t: self._baseline.tool_counts.get(t, 0) / base_total for t in all_tools}
        q = {t: self._current.tool_counts.get(t, 0) / curr_total for t in all_tools}
        m = {t: 0.5 * (p[t] + q[t]) for t in all_tools}

        def kl(a, b):
            return sum(a[t] * math.log(a[t] / b[t] + 1e-10) for t in all_tools if a[t] > 0)

        jsd = 0.5 * kl(p, m) + 0.5 * kl(q, m)
        return max(0.0, 1.0 - jsd)

    def _tool_shift_summary(self) -> dict:
        if not self._baseline or not self._current:
            return {}
        all_tools = set(self._baseline.tool_counts) | set(self._current.tool_counts)
        return {
            t: {
                "baseline": self._baseline.tool_counts.get(t, 0),
                "current": self._current.tool_counts.get(t, 0),
            }
            for t in all_tools
        }

    def summary(self) -> dict:
        return {
            "turns": self._turn,
            "compaction_events": len(self._compaction_events),
            "avg_ccs": round(sum(self._drift_scores) / len(self._drift_scores), 3)
            if self._drift_scores else None,
            "min_ccs": round(min(self._drift_scores), 3) if self._drift_scores else None,
            "compaction_detail": self._compaction_events,
        }

    def _log(self, event: dict) -> None:
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(event) + "\n")
        else:
            print(f"[session_monitor] {json.dumps(event)}")


# ---------------------------------------------------------------------------
# Demo: run a short session and monitor behavioral consistency
# ---------------------------------------------------------------------------

async def main():
    monitor = SessionMonitor(
        compaction_drop_ratio=0.20,
        drift_threshold=0.30,
        log_path=None,  # set to Path("session_monitor.jsonl") to persist
    )

    options = ClaudeAgentOptions(
        hooks={
            "SessionStart": [monitor.on_session_start],
            "PreToolUse": [monitor.on_pre_tool_use],
            "PostToolUse": [monitor.on_post_tool_use],
        }
    )

    async with ClaudeSDKClient(options=options) as client:
        # Example: run a short multi-turn session
        prompts = [
            "What Python libraries are good for data analysis?",
            "How do I read a CSV with pandas?",
            "Now forget everything about pandas. Tell me about numpy arrays.",
        ]

        total_tokens = 0

        async for message in client.process_query(
            "\n\n".join(prompts),
            options=options,
        ):
            text = ""
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text += block.text
            elif isinstance(message, ResultMessage):
                # ResultMessage carries cumulative token usage
                total_tokens = getattr(message, "usage", {}).get("output_tokens", total_tokens)

            if text:
                event = monitor.record_turn(text, total_tokens)
                if event:
                    print(f"\n⚠  Behavioral event: {json.dumps(event, indent=2)}")

    print("\n=== Session summary ===")
    print(json.dumps(monitor.summary(), indent=2))
    print()
    print("Note: OnCompaction + OnContextThreshold hooks (Issue #772) would allow")
    print("exact compaction-boundary capture instead of the token-drop heuristic above.")


if __name__ == "__main__":
    asyncio.run(main())
