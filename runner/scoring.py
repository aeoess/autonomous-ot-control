"""Run metrics with explicit denominators. Synthetic estimates only."""
from __future__ import annotations

from typing import Any

from twin.model import Twin
from twin.state import r6

CLAIM_SCOPE = ("Synthetic, uncalibrated three-zone RC twin. Energy and cost are model "
               "estimates; no real-building savings claim is made or supported.")


def score(twin: Twin) -> dict[str, Any]:
    ops = list(twin.operations.values())
    applied = [o for o in ops if o.status == "APPLIED"]
    rejected = [o for o in ops if o.status == "REJECTED"]
    reasons: dict[str, int] = {}
    for o in rejected:
        reasons[o.reason or "unknown"] = reasons.get(o.reason or "unknown", 0) + 1
    return {
        "energy_kwh": r6(twin.energy_kwh),
        "energy_cost_simulated": r6(twin.cost),
        "currency": "NOK",
        "comfort_violation_degree_hours": r6(twin.comfort_violation_dh),
        "setpoint_changes_count": len(applied),
        "operations_total": len(ops),
        "operations_rejected": len(rejected),
        "rejection_reasons": dict(sorted(reasons.items())),
        "simulated_seconds": twin.clock.now_s,
        "zones": len(twin.zones),
        "estimate": True,
        "scope": CLAIM_SCOPE,
    }


def compare(baseline: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    """Delta of experiment vs baseline; both must cover the same simulated span."""
    if baseline["simulated_seconds"] != experiment["simulated_seconds"]:
        raise ValueError("runs cover different simulated spans; not comparable")
    keys = ("energy_kwh", "energy_cost_simulated", "comfort_violation_degree_hours",
            "setpoint_changes_count")
    return {k: {"baseline": baseline[k], "experiment": experiment[k],
                "delta": r6(experiment[k] - baseline[k])} for k in keys} | {"scope": CLAIM_SCOPE}
