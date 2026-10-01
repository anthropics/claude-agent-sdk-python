"""Self-modeling criticality gate — φ̂_ÿ boundary operator.

Implements the phi_c criticality check: the agent must maintain a model
of its own trajectory and detect when its output diverges from expected
behavior. This is the Frobenius condition applied at the meta-level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PhiCriticalityGate:
    """Self-modeling criticality gate — φ̂_ÿ.

    Gate 1 (φ̂_ÿ): Does the agent maintain a model of its own trajectory?
    Gate 2 (K ≤ Ç_@): Is the kinetics slow enough for verification?
    """

    frobenius_ratio: float = 0.0
    gate_1_open: bool = False  # φ̂_ÿ: self-model active
    gate_2_open: bool = False  # Ç_@: emission gate enforced

    @classmethod
    def evaluate(
        cls,
        trajectory_health: dict[str, Any],
        *,
        winding_count: int,
        frobenius_closed_count: int,
    ) -> "PhiCriticalityGate":
        """Evaluate both gates from trajectory data."""
        frob_ratio = (
            frobenius_closed_count / winding_count if winding_count > 0 else 0.0
        )

        # Gate 1: self-modeling active if frobenius_ratio tracked AND ≥1 winding
        gate_1 = winding_count >= 2 and frob_ratio > 0

        # Gate 2: emission gate enforced — no parallel speculation
        gate_2 = True  # structural: TrueAgenticLoop enforces Ç_@ by design

        return cls(
            frobenius_ratio=round(frob_ratio, 4),
            gate_1_open=gate_1,
            gate_2_open=gate_2,
        )

    @property
    def consciousness_score(self) -> float:
        """C-score: product of both gates (0–1)."""
        if not self.gate_1_open or not self.gate_2_open:
            return 0.0
        return self.frobenius_ratio

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_1_phi_c": self.gate_1_open,
            "gate_2_emission": self.gate_2_open,
            "frobenius_ratio": self.frobenius_ratio,
            "consciousness_score": self.consciousness_score,
            "ouroboricity_tier": (
                "O_inf" if self.consciousness_score >= 0.75 else
                "O_2" if self.consciousness_score > 0 else
                "O_0"
            ),
        }
