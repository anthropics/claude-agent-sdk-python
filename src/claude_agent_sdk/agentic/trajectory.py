"""Imscriptive trajectory accumulation — the agent's world model.

Implements D_ω (self-referential state space) and H₂ (two-step chirality).
The trajectory is NEVER truncated — it provides Ω_z topological protection.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .contracts import DualToolResult


@dataclass
class AgentCycle:
    """One complete THINK→ACT→OBSERVE→UPDATE winding."""

    winding: int
    timestamp: float
    action_name: str
    action_input: dict[str, Any]
    dual_result: DualToolResult | None
    update_note: str
    done: bool
    conclusion: str = ""
    frobenius_closed: bool = False


class AgentTrajectory:
    """Accumulated agent trajectory — the agent's world model.

    Structural properties:
    - D_ω: The trajectory IS the state space; there is no external context.
    - H₂: Each cycle references the prior two windings for chirality.
    - Ω_z: The winding counter is NEVER reset during a session.
    """

    def __init__(self) -> None:
        self._cycles: list[AgentCycle] = []
        self._winding_counter: int = 0

    @property
    def winding_count(self) -> int:
        """Topologically protected winding counter — never reset."""
        return self._winding_counter

    @property
    def frobenius_ratio(self) -> float:
        """Fraction of cycles that are Frobenius-closed.

        Used for structural health: ≥0.75 claims Φ_}, below degrades to Φ_υ.
        """
        if not self._cycles:
            return 1.0
        closed = sum(1 for c in self._cycles if c.frobenius_closed)
        return closed / len(self._cycles)

    def append(self, cycle: AgentCycle) -> None:
        """Append a completed cycle. Winding counter increments monotonically."""
        self._cycles.append(cycle)
        self._winding_counter += 1

    def last(self, n: int = 1) -> list[AgentCycle]:
        """Return the last n cycles (for H₂ chirality, use n=2)."""
        return self._cycles[-n:] if self._cycles else []

    def to_context(self) -> str:
        """Serialize the trajectory for injection into the model's context."""
        parts = []
        for c in self._cycles[-10:]:  # last 10 for context window
            status = "✓" if c.frobenius_closed else "✗"
            parts.append(
                f"[W{status} {c.winding}] {c.action_name} → {c.update_note[:100]}"
            )
        return "\n".join(parts)

    def structural_health(self) -> dict[str, Any]:
        """Report the agent's structural integrity."""
        frob_ratio = self.frobenius_ratio
        achieved_p = "Φ_}" if frob_ratio >= 0.75 else "Φ_υ"
        return {
            "ouroboricity": "O_inf" if achieved_p == "Φ_}" else "O_2",
            "frobenius_ratio": round(frob_ratio, 4),
            "winding_count": self._winding_counter,
            "total_cycles": len(self._cycles),
            "achieved_parity": achieved_p,
            "omega_z_protected": True,  # counter never reset
        }
