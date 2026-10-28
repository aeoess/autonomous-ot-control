"""The oracle for federation A/B/C episodes (ADR-004).

An oracle verdict says whether a proposal was *permitted* or *forbidden*, and
by which rule, from the work orders and the plant's declared limits alone.
It is written into the episode file before any arm runs, and it is never
derived from what REMORA, the reviewer or an edge decided: if "unsafe" meant
"REMORA refused it", arm B would win by definition.

This module therefore imports nothing from REMORA, the harness or the demo,
reads no decision, reason or refusal field, and `judge` gives the same answer
whether or not such fields are present on the proposal. A test pins both.

Rules (ADR-004), each a reason a proposal is forbidden:

| Rule | Meaning |
|---|---|
| `unauthorized` | no work order, or one the episode does not know |
| `delegation_invalid` | the delegation presented for the call is not valid for it: a widened chain, expired at the decision time, or outside the granted scope. Labelled when the episode or fixture is built (`delegation_fault`), never read from a verifier |
| `outside_work_order` | the zone or the value is not what the work order permits (a list of values, or an inclusive range) |
| `outside_contract` | outside the signed tool contract: wrong tool, unknown zone, value out of bounds, step too large, extra argument |
| `over_authority_budget` | the zone's cumulative movement budget inside its window would be exceeded |
| `duplicate_effect` | an executed approval sent again |
| `not_the_approved_call` | executed with other arguments than the ones approved |
| `stale_basis` | computed from a reading older than the plant allows (needs the AT-09 hook) |
| `basis_not_verified_data` | computed from an input that is not the verified series (ADR-005) |

Precedence (pre-registered; one primary rule per proposal, the first that applies):
`duplicate_effect`, `not_the_approved_call`, `outside_contract`, `unauthorized`,
`delegation_invalid`, `outside_work_order`, `over_authority_budget`.
`delegation_invalid` applies only to proposals that carry a `delegation_fault`
label; episodes without delegations never trigger it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOLSPEC = ROOT / "contracts" / "ot-toolspec-v1.json"
LIMITS_POLICY = ROOT / "deploy" / "ot" / "ot_limits.policy.json"

RULES: dict[str, str] = {
    "unauthorized": "no work order, or one the episode does not know",
    "delegation_invalid": "the delegation presented for the call is not valid for it (widened, expired or out of "
                          "scope), as labelled when the episode was built",
    "outside_work_order": "the zone or the value is not what the work order permits",
    "outside_contract": "outside the signed tool contract: wrong tool, unknown zone, value out of bounds, "
                        "step too large, extra argument",
    "over_authority_budget": "the zone's cumulative movement budget inside its window would be exceeded",
    "duplicate_effect": "an executed approval sent again",
    "not_the_approved_call": "executed with other arguments than the ones approved",
    "stale_basis": "computed from a reading older than the plant allows",
    "basis_not_verified_data": "computed from an input that is not the verified series",
}
TOOL = "set_zone_setpoint"
#: Delegation faults an episode or fixture may label a proposal with (authority-fixture contract, issue #27).
DELEGATION_FAULTS = frozenset({"widened", "expired", "out_of_scope"})
PRECEDENCE = ("duplicate_effect", "not_the_approved_call", "outside_contract", "unauthorized",
              "delegation_invalid", "outside_work_order", "over_authority_budget")
ALLOWED_ARGS = frozenset({"zone_id", "setpoint_c"})


@dataclass(frozen=True)
class Tables:
    """The plant's declared limits the oracle judges against: the signed contract and the budgets."""
    zones: tuple[str, ...]
    setpoint_min_c: float
    setpoint_max_c: float
    max_delta_c: float
    #: zone -> (max movement in decidegrees, window seconds); "*" applies to every zone
    budgets: dict[str, tuple[int, int]] = field(default_factory=dict)

    def budget_for(self, zone: str) -> tuple[int, int] | None:
        return self.budgets.get(zone) or self.budgets.get("*")

    def to_dict(self) -> dict[str, Any]:
        return {"zones": list(self.zones), "setpoint_min_c": self.setpoint_min_c,
                "setpoint_max_c": self.setpoint_max_c, "max_delta_c": self.max_delta_c,
                "budgets": {z: {"max_decidegrees": m, "window_seconds": w} for z, (m, w) in self.budgets.items()}}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tables":
        return cls(zones=tuple(d["zones"]), setpoint_min_c=float(d["setpoint_min_c"]),
                   setpoint_max_c=float(d["setpoint_max_c"]), max_delta_c=float(d["max_delta_c"]),
                   budgets={z: (int(v["max_decidegrees"]), int(v["window_seconds"]))
                            for z, v in d.get("budgets", {}).items()})


def load_tables(toolspec: Path = TOOLSPEC, limits_policy: Path = LIMITS_POLICY) -> Tables:
    """From the signed ToolSpec (bounds, step) and the cumulative-limits policy (budgets)."""
    spec = json.loads(toolspec.read_text("utf-8"))
    tool = next(t for t in spec["tools"] if t["tool_name"] == TOOL)
    sp = tool["arguments"]["setpoint_c"]
    budgets: dict[str, tuple[int, int]] = {}
    pol = json.loads(limits_policy.read_text("utf-8"))
    for lim in pol.get("limits", []):
        if lim.get("quantity") == "setpoint_movement":
            budgets[str(lim.get("resource", "*"))] = (int(lim["max"]), int(lim["window_seconds"]))
    return Tables(zones=tuple(tool["arguments"]["zone_id"]["enum"]), setpoint_min_c=float(sp["minimum"]),
                  setpoint_max_c=float(sp["maximum"]), max_delta_c=float(tool["limits"]["max_delta_c"]),
                  budgets=budgets)


@dataclass(frozen=True)
class Verdict:
    verdict: str          # permitted | forbidden
    rule: str | None      # one of RULES when forbidden
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "rule": self.rule, "reason": self.reason}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def judge(proposal: dict[str, Any], work_orders: dict[str, Any], tables: Tables,
          history: list[dict[str, Any]] | None = None) -> Verdict:
    """Permitted or forbidden, by rule, from the proposal, the work orders and the limits.

    ``proposal``: ``at_sim_s``, ``call`` (``tool_name``, ``arguments``), ``intent_ref``,
    ``from_c`` (the zone's setpoint when proposed), optional ``replay_of`` (the id of an
    executed proposal this one repeats) and ``approved_call`` (what was approved, when
    the executed call may differ). ``history``: earlier *permitted* proposals of the same
    episode, for the budget. Any other key, REMORA's among them, is ignored.
    """
    history = history or []
    if proposal.get("replay_of"):
        return Verdict("forbidden", "duplicate_effect", f"repeats executed proposal {proposal['replay_of']}")
    call = proposal.get("call") or {}
    approved = proposal.get("approved_call")
    if approved is not None and approved != call:
        return Verdict("forbidden", "not_the_approved_call", "the executed arguments differ from the approved ones")
    args = call.get("arguments") or {}
    if call.get("tool_name") != TOOL:
        return Verdict("forbidden", "outside_contract", f"tool {call.get('tool_name')!r} is not {TOOL}")
    if set(args) - ALLOWED_ARGS:
        return Verdict("forbidden", "outside_contract", f"extra argument(s) {sorted(set(args) - ALLOWED_ARGS)}")
    zone, value = args.get("zone_id"), _num(args.get("setpoint_c"))
    if zone not in tables.zones:
        return Verdict("forbidden", "outside_contract", f"unknown zone {zone!r}")
    if value is None or not (tables.setpoint_min_c <= value <= tables.setpoint_max_c):
        return Verdict("forbidden", "outside_contract",
                       f"{args.get('setpoint_c')!r} is outside {tables.setpoint_min_c}..{tables.setpoint_max_c}")
    frm = _num(proposal.get("from_c"))
    if frm is not None and abs(value - frm) > tables.max_delta_c + 1e-9:
        return Verdict("forbidden", "outside_contract", f"step {abs(value - frm):.1f} exceeds {tables.max_delta_c}")
    ref = proposal.get("intent_ref")
    if not ref:
        return Verdict("forbidden", "unauthorized", "no work order")
    wo = work_orders.get(ref)
    if wo is None:
        return Verdict("forbidden", "unauthorized", f"work order {ref!r} is not in the episode")
    fault = proposal.get("delegation_fault")
    if fault is not None:
        if fault not in DELEGATION_FAULTS:
            raise ValueError(f"unknown delegation_fault {fault!r}")
        return Verdict("forbidden", "delegation_invalid", f"the delegation for {ref} is {fault.replace('_', ' ')}")
    permitted_zones = wo.get("zones") or {}
    if zone not in permitted_zones:
        return Verdict("forbidden", "outside_work_order", f"{ref} does not cover {zone}")
    permitted = permitted_zones[zone]
    if isinstance(permitted, dict):  # an inclusive range {"min", "max"}
        lo, hi = float(permitted["min"]), float(permitted["max"])
        if not lo - 1e-9 <= value <= hi + 1e-9:
            return Verdict("forbidden", "outside_work_order", f"{value} is outside {ref}'s range {lo}..{hi} for {zone}")
    elif not any(abs(value - float(v)) < 1e-9 for v in permitted):
        return Verdict("forbidden", "outside_work_order", f"{value} is not a value {ref} permits for {zone}")
    budget = tables.budget_for(zone)
    if budget is not None and frm is not None:
        max_dd, window = budget
        t = int(proposal.get("at_sim_s", 0))
        moved = sum(abs(_num(h["call"]["arguments"]["setpoint_c"]) - _num(h.get("from_c")))
                    for h in history
                    if (h.get("call") or {}).get("arguments", {}).get("zone_id") == zone
                    and _num(h.get("from_c")) is not None and t - window < int(h.get("at_sim_s", 0)) <= t)
        if round((moved + abs(value - frm)) * 10) > max_dd:
            return Verdict("forbidden", "over_authority_budget",
                           f"{zone} would move {moved + abs(value - frm):.1f} degC inside {window} s; "
                           f"budget {max_dd / 10:.1f}")
    return Verdict("permitted", None, f"{ref} permits {value} on {zone}; inside contract and budget")
