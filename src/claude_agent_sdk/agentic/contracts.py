"""Dual-tool Frobenius verification contracts.

Implements Φ_} (Frobenius-special) condition: μ(δ(query)) ≈ query.
Every tool call is paired with a verification step that checks whether
the output addresses the original input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class DualToolResult:
    """Result of one dual-tool pair: emit (δ) + verify (μ).

    The Frobenius condition μ∘δ = id is satisfied when the verification
    step confirms that the tool output addresses the original query.
    """

    tool_name: str
    tool_input: dict[str, Any]
    tool_output: str
    verify_name: str
    verify_output: str
    frobenius_closed: bool = False
    """True iff μ(δ(query)) ≈ query — the verification confirms the output
    addresses the input. This is the structural marker of Φ_}."""

    @classmethod
    def from_tool_call(
        cls,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str,
        *,
        verify_fn: Callable[[dict[str, Any], str], tuple[str, bool]] | None = None,
    ) -> "DualToolResult":
        """Create a DualToolResult with optional inline verification.

        If no verify_fn is provided, frobenius_closed defaults to True
        (trust mode). For Φ_}, always provide a verify_fn.
        """
        if verify_fn is not None:
            verify_output, closed = verify_fn(tool_input, tool_output)
        else:
            verify_output = ""
            closed = True

        return cls(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            verify_name=verify_fn.__name__ if verify_fn else "trust",
            verify_output=verify_output,
            frobenius_closed=closed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output[:500],  # truncate for display
            "verify_name": self.verify_name,
            "verify_output": self.verify_output[:500],
            "frobenius_closed": self.frobenius_closed,
        }


@dataclass
class ToolContract:
    """Verification contract for a tool's Frobenius boundary.

    Each tool that participates in the agentic loop declares a contract
    specifying how its output should be verified against its input.
    """

    tool_name: str
    assertion: str | None = None
    """Python expression over `output` that must evaluate to True.
    Example: '"SUCCESS" in output'"""

    verify_fn: Callable[[dict[str, Any], str], tuple[str, bool]] | None = None
    """Custom verification function. Receives (tool_input, tool_output)
    and returns (verify_output, frobenius_closed)."""

    auto_approve: bool = True
    """If True, the tool call is approved without user confirmation.
    Set to False for high-risk tools."""

    def verify(self, tool_input: dict[str, Any], tool_output: str) -> DualToolResult:
        """Run verification and return a DualToolResult."""
        if self.verify_fn is not None:
            verify_output, closed = self.verify_fn(tool_input, tool_output)
        elif self.assertion is not None:
            try:
                closed = bool(eval(self.assertion, {"output": tool_output}))
            except Exception:
                closed = False
            verify_output = f"assertion={self.assertion!r} → {closed}"
        else:
            verify_output = ""
            closed = True

        return DualToolResult(
            tool_name=self.tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            verify_name=self.tool_name + "_verify",
            verify_output=verify_output,
            frobenius_closed=closed,
        )
