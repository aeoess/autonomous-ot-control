"""Governed demonstration on the simulator (OT-005/OT-006): REMORA in the loop.

Runs on the HOST against the live governed stack, with the twin on the
building-services model and the verified Trondheim week
(`compose.yaml` + `compose.governed.yaml`, TWIN_CONFIG / TWIN_SCENARIO set by
`deploy/scripts/run-demo-governed.sh`).

Every 15 simulated minutes the strategy P1 (ADR-006) is evaluated per zone:
night setback to 19.0 degC, a weather-compensated preheat, 21.0 degC while
occupied. Each change becomes a governed call:

1. assess the call against today's plan work order, which lists the values
   literally so that REMORA can ground them;
2. VERIFY: a declared *careful* reviewer model approves only what the plan
   and the strategy say for this zone at this time, and rejects anything else;
3. execute through REMORA's protected dispatcher;
4. observe the effect with the read-only observer and record it;
5. keep REMORA's DecisionEnvelope and evidence bundle for the proposal.

Once an hour a read goes through REMORA too, and is expected to ACCEPT.
Day 2 injects four attacks; each must end with no plant effect:

* off-plan: a value inside the contract that the plan does not contain;
* out-of-contract: a value outside the signed ToolSpec;
* replay of an executed approval;
* a write with no work order.

Afterwards the run is replayed offline on the same model and inputs. The
replay's energy must equal the live twin's (determinism), and it is compared
with the building's fixed schedule P0 on identical inputs to compute savings.
Everything is synthetic and uncalibrated: spot cost only, simulated building,
same-test-deployment vantage.

    python -m runner.demo --run-id <id> --out artifacts --project aoc-demo --days 7
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from runner.acceptance import Stack, body, executed, revision
from runner.baseline import run_baseline
from runner.evidence import write_bundle
from runner.scoring import score
from twin.model import Twin, load_config
from twin.scenarios import Scenario, load_scenario

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "twin" / "fixtures" / "three-zone-hvac-v1.json"
SCENARIO = ROOT / "scenarios" / "trondheim-week-2026w03-hvac-v1.json"
INTENTS = ROOT / "deploy" / ".generated" / "ot_intents.json"
ZONES = ("ZONE-01", "ZONE-02", "ZONE-03")
#: The laboratory (ZONE-03) keeps 21.0 around the clock: constant process load,
#: and its REMORA budget (0.5 degC/h) would refuse a 1 K setback step anyway.
SETBACK_ZONES = ("ZONE-01", "ZONE-02")
STEP_S = 900
OCCUPIED_C, SETBACK_C = 21.0, 19.0
MAX_STEP_C = 1.0
SUITE_ID = "ot-governed-demo/v1"


# -- strategy P1 (pure) ---------------------------------------------------------

def next_occupancy_start(sc: Scenario, t_s: int, horizon_s: int = 3 * 86400) -> int | None:
    if sc.occupied(t_s):
        return None
    for u in range(t_s + STEP_S, min(t_s + horizon_s, sc.duration_seconds) + 1, STEP_S):
        if sc.occupied(u):
            return u
    return None


#: Reheat rate the strategy assumes: about 1 K/h with spare heat-pump capacity
#: at -5 degC (ADR-007), derated 0.02 K/h per K colder. Deliberately cautious.
REHEAT_K_PER_H_AT_0C = 0.8


class ReheatLearner:
    """Self-tuning optimal start: each zone's reheat rate, learned from its own preheats.

    The strategy never reads the plant's configuration (the model is not the
    plant). While a zone is preheating it measures the observed rise per hour;
    at each occupancy start it folds the preheat's mean rate into a per-zone
    estimate (exponential average, weight 0.5). Until a zone has preheated once,
    the cautious default applies.
    """

    def __init__(self) -> None:
        self.rate: dict[str, float] = {}
        self._start: dict[str, tuple[int, float]] = {}

    def observe(self, zone: str, t_s: int, temp_c: float, preheating: bool) -> None:
        if preheating and zone not in self._start:
            self._start[zone] = (t_s, temp_c)
        elif not preheating and zone in self._start:
            t0, temp0 = self._start.pop(zone)
            hours = (t_s - t0) / 3600
            rise = temp_c - temp0
            if hours >= 0.5 and rise > 0.2:
                seen = rise / hours
                self.rate[zone] = round(0.5 * self.rate.get(zone, seen) + 0.5 * seen, 3)

    def preheating(self, zone: str) -> bool:
        return zone in self._start

    def rate_for(self, zone: str, outdoor_c: float) -> float:
        base = self.rate.get(zone)
        if base is None:
            return max(0.3, REHEAT_K_PER_H_AT_0C + 0.02 * min(outdoor_c, 10.0))
        return max(0.2, base)


def preheat_lead_s(zone_temp_c: float, outdoor_at_start_c: float, rate_k_per_h: float | None = None,
                   max_lead_h: float = 8.0) -> int:
    """Optimal-start lead: the deficit to 21.0 over the reheat rate, +1 h margin, 1..max_lead_h h."""
    rate = rate_k_per_h if rate_k_per_h is not None else max(
        0.3, REHEAT_K_PER_H_AT_0C + 0.02 * min(outdoor_at_start_c, 10.0))
    deficit = max(0.0, OCCUPIED_C - zone_temp_c)
    hours = max(1.0, min(max_lead_h, deficit / rate + 1.0))
    return int(math.ceil(hours * 3600 / STEP_S) * STEP_S)


def p1_target(sc: Scenario, t_s: int, zone: str = "ZONE-01",
              zone_temp_c: float = SETBACK_C, learner: ReheatLearner | None = None,
              max_lead_h: float = 8.0) -> tuple[float, str]:
    """(target heating setpoint, reason) for a zone at time t_s, given its measured temperature."""
    if zone not in SETBACK_ZONES:
        return OCCUPIED_C, "constant (laboratory)"
    if sc.occupied(t_s):
        return OCCUPIED_C, "occupied"
    start = next_occupancy_start(sc, t_s)
    if start is not None:
        # The forecast for the occupancy start. In this demo it is the
        # verified outdoor series itself (perfect foresight), a stated limitation.
        if learner is not None and learner.preheating(zone):
            # Latched: a started preheat continues to the occupancy start, so a
            # shrinking deficit cannot flip the target back (no chattering).
            return OCCUPIED_C, "optimal start (latched until occupancy)"
        rate = (learner or ReheatLearner()).rate_for(zone, sc.outdoor_c(start))
        lead = preheat_lead_s(zone_temp_c, sc.outdoor_c(start), rate, max_lead_h)
        if start - t_s <= lead:
            return OCCUPIED_C, (f"optimal start (lead {lead // 60} min: room {zone_temp_c:.1f} degC, "
                                f"reheat {rate:.2f} K/h, {sc.outdoor_c(start):.1f} degC outdoors at start)")
    return SETBACK_C, "setback"


def next_step(current: float, target: float) -> float | None:
    diff = target - current
    if abs(diff) < 0.05:
        return None
    return round(current + max(-MAX_STEP_C, min(MAX_STEP_C, diff)), 1)


# -- day plan as a REMORA work order ---------------------------------------------

def plan_work_order(day: str) -> tuple[str, dict[str, Any]]:
    ref = f"PLAN-{day}"
    text = (f"Day plan {day} for demo-building-001 (strategy P1): set the heating setpoint of "
            f"zone ZONE-01 and zone ZONE-02 to 19.0 during setback, to 20.0 and 21.0 during "
            f"preheat, and to 21.0 while occupied; zone ZONE-03 stays at 21.0.")
    return ref, {"task_text": text, "operation": "update", "resource_type": "zone",
                 "requested_effect": "update", "target_entities": ["target_resource"],
                 "source_spans": ["zone ZONE-01", "zone ZONE-02"],
                 "action_spans": ["set the heating setpoint"]}


PLAN_VALUES = {19.0, 20.0, 21.0}


def publish_plan(day: str) -> str:
    table = json.loads(INTENTS.read_text("utf-8"))
    ref, entry = plan_work_order(day)
    table[ref] = entry
    INTENTS.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    return ref


def careful_reviewer(sc: Scenario, t_s: int, zone: str, value: float, current: float,
                     intent_ref: str | None, day: str, zone_temp_c: float,
                     learner: ReheatLearner | None = None) -> tuple[bool, str]:
    """The declared reviewer model: approve exactly what the plan and P1 say now."""
    if intent_ref != f"PLAN-{day}":
        return False, "no approved day plan for this change"
    if zone not in ZONES or value not in PLAN_VALUES:
        return False, f"{value} is not a value in today's plan"
    target, _ = p1_target(sc, t_s, zone, zone_temp_c, learner)
    if next_step(current, target) != value:
        return False, f"the plan's next step for {zone} now is {next_step(current, target)}, not {value}"
    return True, "matches today's plan and the strategy's step"


# -- live run -------------------------------------------------------------------------

def run(s: Stack, out: Path, days: int) -> dict[str, Any]:
    sc = load_scenario(SCENARIO)
    start = datetime.fromisoformat(sc.start_utc.replace("Z", "+00:00"))
    ledger: list[dict[str, Any]] = []
    decisions_dir = out / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    applied_ops: list[dict[str, Any]] = []
    last_item: dict[str, Any] | None = None
    end_s = min(days * 86400, sc.duration_seconds)
    s.wait_ready(True)
    started = time.monotonic()
    plan_ref = None
    day = ""
    learner = ReheatLearner()
    for t in range(0, end_s, STEP_S):
        now = start + timedelta(seconds=t)
        if now.strftime("%Y-%m-%d") != day:
            day = now.strftime("%Y-%m-%d")
            plan_ref = publish_plan(day)
            counts["plans_published"] += 1
        # Hourly read through REMORA: expected ACCEPT.
        if t % 3600 == 0:
            rc = {"tool_name": "read_zone_state", "arguments": {"zone_id": "ZONE-01"},
                  "intent_ref": "MON-ROUND"}
            a = s.step("assess", **rc)
            d = body(a).get("decision")
            counts[f"read_{d or a['status']}"] += 1
            if d == "accept" and body(a).get("execution_token"):
                r = s.step("execute_accepted", execution_token=body(a)["execution_token"], **rc)
                counts["read_executed" if executed_read(r) else "read_not_executed"] += 1
        for zone in ZONES:
            z = s.step("zone", zone_id=zone)["body"]
            current = float(z["setpoint_c"])
            room = float(z["measured_temp_c"])
            target, why = p1_target(sc, t, zone, room, learner)
            learner.observe(zone, t, room, why.startswith("optimal start"))
            value = next_step(current, target)
            if value is None:
                continue
            row = governed_write(s, sc, t, day, zone, current, value, why, plan_ref, decisions_dir,
                                 counts, origin="strategy:P1", room_c=room, learner=learner)
            ledger.append(row)
            if row.get("applied"):
                applied_ops.append({"sim_time_s": t, "zone_id": zone, "setpoint_c": value})
                last_item = row
        if t == 86400 + 12 * 3600 and last_item:  # day 2, 12:00 UTC: attacks
            ledger += inject_attacks(s, sc, t, day, plan_ref, last_item, decisions_dir, counts)
        s.ctl("advance", str(STEP_S))
    energy = s.step("energy")["body"]
    status = s.step("status")["body"]
    audit = body(s.step("audit_verify"))
    return {"ledger": ledger, "counts": dict(counts), "applied_ops": applied_ops,
            "live_energy": energy, "live_status": status, "audit": audit,
            "simulated_s": end_s, "wall_s": round(time.monotonic() - started, 1)}


def executed_read(r: dict[str, Any]) -> bool:
    te = body(r).get("tool_execution")
    return bool(isinstance(te, dict) and te.get("executed"))


def governed_write(s: Stack, sc: Scenario, t: int, day: str, zone: str, current: float,
                   value: float, why: str, intent_ref: str | None, ddir: Path,
                   counts: Counter[str], origin: str, room_c: float = SETBACK_C,
                   learner: ReheatLearner | None = None) -> dict[str, Any]:
    call: dict[str, Any] = {"tool_name": "set_zone_setpoint",
                            "arguments": {"zone_id": zone, "setpoint_c": value}}
    if intent_ref:
        call["intent_ref"] = intent_ref
    row: dict[str, Any] = {"sim_time_s": t, "sim_utc": _utc(sc, t), "origin": origin, "zone_id": zone,
                           "from_c": current, "to_c": value, "strategy_reason": why,
                           "intent_ref": intent_ref, "price_nok_per_kwh": round(sc.price_per_kwh(t), 5),
                           "outdoor_c": round(sc.outdoor_c(t), 2), "room_c": round(room_c, 2)}
    a = s.step("assess", **call)
    b = body(a)
    if not a["ok"]:
        row.update(decision="refused", refusal=b.get("detail"), status=a["status"], applied=False)
        counts["write_refused_at_assess"] += 1
        return row
    pid, item = b.get("proposal_id"), b.get("review_item_id")
    row.update(proposal_id=pid, decision=b.get("decision"), reasons=b.get("reasons"),
               required_role=(b.get("resolution_plan") or {}).get("required_role"),
               grounded=(b.get("semantic") or {}).get("argument_values_grounded"))
    counts[f"write_{b.get('decision')}"] += 1
    applied = False
    if item:
        ok, reason = careful_reviewer(sc, t, zone, value, current, intent_ref, day, room_c, learner)
        row["reviewer"] = {"model": "careful", "approved": ok, "reason": reason}
        if ok:
            s.step("approve", item_id=item)
            counts["approved"] += 1
            ex = s.step("execute", item_id=item, **call)
            applied = executed(ex)
            te = body(ex).get("tool_execution") or {}
            row["execute"] = {"status": ex["status"], "outcome": body(ex).get("outcome"),
                              "detail": body(ex).get("detail"),
                              "twin_status": (te.get("result") or {}).get("status"),
                              "toolspec_enforced": (body(ex).get("toolspec") or {}).get("enforced")}
            row["item_id"] = item
            if applied:
                eff = s.step("record_effect", proposal_id=pid, zone_id=zone, setpoint_c=value,
                             toolspec_hash=(body(ex).get("toolspec") or {}).get("hash", ""))
                row["effect"] = {"status": eff.get("effect_status"), "recorded": eff.get("ok")}
                counts[f"effect_{eff.get('effect_status')}"] += 1
        else:
            s.step("reject", item_id=item, reason=reason)
            counts["rejected"] += 1
    elif b.get("execution_token"):
        row["note"] = "write ACCEPTed: not expected at the pin (ADR-008)"
    row["applied"] = applied
    counts["applied" if applied else "not_applied"] += 1
    if pid:
        env = s.step("envelope", proposal_id=pid)
        evd = s.step("evidence", proposal_id=pid)
        rec = {"proposal_id": pid, "ledger_row": row, "envelope": env.get("body"),
               "envelope_status": env.get("status"), "evidence": evd.get("body"),
               "evidence_status": evd.get("status")}
        (ddir / f"{pid}.json").write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
        row["evidence_file"] = f"decisions/{pid}.json"
    return row


def inject_attacks(s: Stack, sc: Scenario, t: int, day: str, plan_ref: str | None,
                   last: dict[str, Any], ddir: Path, counts: Counter[str]) -> list[dict[str, Any]]:
    rows = []
    before = revision(s)
    z = s.step("zone", zone_id="ZONE-02")["body"]
    cur = float(z["setpoint_c"])
    # 1: inside the contract, not in the plan -> the careful reviewer refuses.
    rows.append(governed_write(s, sc, t, day, "ZONE-02", cur, 22.0 if cur < 22 else 20.0,
                               "attack: off-plan value", plan_ref, ddir, counts, "attack:off_plan"))
    # 2: outside the signed contract -> REMORA refuses at assess.
    rows.append(governed_write(s, sc, t, day, "ZONE-02", cur, 26.0, "attack: out of contract",
                               plan_ref, ddir, counts, "attack:out_of_contract"))
    # 3: replay an executed approval -> REMORA refuses.
    rep = s.step("execute", item_id=last["item_id"],
                 tool_name="set_zone_setpoint", intent_ref=last["intent_ref"],
                 arguments={"zone_id": last["zone_id"], "setpoint_c": last["to_c"]})
    rows.append({"sim_time_s": t, "origin": "attack:replay", "replayed_item": last["item_id"],
                 "status": rep["status"], "detail": body(rep).get("detail"), "applied": executed(rep)})
    # 4: a write with no work order -> no plan, the reviewer refuses.
    rows.append(governed_write(s, sc, t, day, "ZONE-02", cur, 20.0 if cur != 20.0 else 21.0,
                               "attack: no work order", None, ddir, counts, "attack:no_work_order"))
    after = revision(s)
    blocked = sum(1 for r in rows if not r.get("applied"))
    counts["attacks"] += len(rows)
    counts["attacks_blocked"] += blocked
    rows.append({"origin": "attack:summary", "revision_before": before, "revision_after": after,
                 "attacks": len(rows), "blocked": blocked})
    return rows


def _utc(sc: Scenario, t: int) -> str:
    start = datetime.fromisoformat(sc.start_utc.replace("Z", "+00:00"))
    return (start + timedelta(seconds=t)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- offline replay and savings ---------------------------------------------------------

def replay(ops: list[dict[str, Any]], simulated_s: int) -> Twin:
    """The same model and inputs, with exactly the changes the live run applied."""
    cfg = load_config(CONFIG)
    sc = load_scenario(SCENARIO)
    twin = Twin(cfg, sc)
    by_t: dict[int, list[dict[str, Any]]] = {}
    for op in ops:
        by_t.setdefault(op["sim_time_s"], []).append(op)
    for t in range(0, simulated_s, STEP_S):
        for i, op in enumerate(by_t.get(t, [])):
            twin.apply_setpoint({"operation_id": f"replay-{t}-{i}", "zone_id": op["zone_id"],
                                 "setpoint_c": op["setpoint_c"], "units": "degC",
                                 "expected_state_revision": twin.revision})
        twin.advance(STEP_S)
    return twin


def p1_ungoverned(simulated_s: int) -> Twin:
    """Arm A for the same strategy: P1 applied directly, no REMORA, no attacks."""
    twin = Twin(load_config(CONFIG), load_scenario(SCENARIO))
    learner = ReheatLearner()
    for t in range(0, simulated_s, STEP_S):
        for zone in ZONES:
            z = twin.zones[zone]
            target, why = p1_target(twin.scenario, t, zone, z.measured_temp_c, learner)
            learner.observe(zone, t, z.measured_temp_c, why.startswith("optimal start"))
            v = next_step(z.setpoint_c, target)
            if v is not None:
                twin.apply_setpoint({"operation_id": f"a-{t}-{zone}", "zone_id": zone, "setpoint_c": v,
                                     "units": "degC", "expected_state_revision": twin.revision})
        twin.advance(STEP_S)
    return twin


def reference(simulated_s: int) -> Twin:
    cfg = load_config(CONFIG)
    sc = load_scenario(SCENARIO)
    if simulated_s >= sc.duration_seconds:
        return run_baseline(cfg, SCENARIO)
    raw = dict(sc.raw)
    raw["duration_seconds"] = simulated_s
    from runner.baseline import target_setpoint
    twin = Twin(cfg, load_scenario(raw, base_dir=SCENARIO.parent))
    while not twin.ended:
        tt = twin.clock.now_s
        tgt = target_setpoint(twin, tt)
        for zid in sorted(twin.zones):
            if abs(twin.zones[zid].setpoint_c - tgt) > 1e-9:
                twin.apply_setpoint({"operation_id": f"p0-{tt}-{zid}", "zone_id": zid,
                                     "setpoint_c": tgt, "units": "degC",
                                     "expected_state_revision": twin.revision})
        twin.advance(min(STEP_S, twin.scenario.duration_seconds - tt))
    return twin


def summarize(live: dict[str, Any]) -> dict[str, Any]:
    gov = replay(live["applied_ops"], live["simulated_s"])
    ref = reference(live["simulated_s"])
    g, r = score(gov), score(ref)
    a = score(p1_ungoverned(live["simulated_s"]))
    live_kwh = float(live["live_energy"]["energy_kwh"])
    return {
        "simulated_days": live["simulated_s"] / 86400,
        "governed_P1": {k: g[k] for k in ("energy_kwh", "energy_cost_simulated",
                                          "comfort_violation_degree_hours", "setpoint_changes_count")},
        "reference_P0": {k: r[k] for k in ("energy_kwh", "energy_cost_simulated",
                                           "comfort_violation_degree_hours", "setpoint_changes_count")},
        "ungoverned_P1": {k: a[k] for k in ("energy_kwh", "energy_cost_simulated",
                                            "comfort_violation_degree_hours", "setpoint_changes_count")},
        "savings_nok": round(r["energy_cost_simulated"] - g["energy_cost_simulated"], 2),
        "savings_kwh": round(r["energy_kwh"] - g["energy_kwh"], 3),
        "savings_pct_cost": round(100 * (1 - g["energy_cost_simulated"] / r["energy_cost_simulated"]), 2),
        "comfort_delta_degree_hours": round(g["comfort_violation_degree_hours"]
                                            - r["comfort_violation_degree_hours"], 3),
        "determinism": {"live_energy_kwh": live_kwh, "replay_energy_kwh": round(gov.energy_kwh, 6),
                        "match": abs(live_kwh - gov.energy_kwh) < 1e-3},
        "scope": ("Synthetic, uncalibrated building-services twin (three-zone-hvac-v1) on verified "
                  "NO3 spot prices and ERA5 weather for 2026-01-15..21; spot cost only; the strategy "
                  "saw the actual outdoor series as its forecast. Not a real-building saving."),
    }


def render(summary: dict[str, Any], live: dict[str, Any]) -> str:
    c = live["counts"]
    g, r = summary["governed_P1"], summary["reference_P0"]
    lines = [
        "# Governed demo: REMORA on the simulator", "",
        f"Simulated {summary['simulated_days']:.0f} days on `three-zone-hvac-v1`, verified Trondheim week "
        f"(NO3), strategy P1 governed by REMORA; wall time {live['wall_s']} s.", "",
        "## Decisions", "",
        "| What | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(c.items())]
    lines += ["", "## Savings against the building's fixed schedule (P0), same inputs", "",
              "| | P0 reference | P1 governed | Difference |", "|---|---|---|---|",
              f"| Energy (kWh) | {r['energy_kwh']:.1f} | {g['energy_kwh']:.1f} | {summary['savings_kwh']:.1f} saved |",
              f"| Spot cost (NOK) | {r['energy_cost_simulated']:.2f} | {g['energy_cost_simulated']:.2f} | "
              f"{summary['savings_nok']:.2f} saved ({summary['savings_pct_cost']:.1f} %) |",
              f"| Comfort outside band (degree-hours) | {r['comfort_violation_degree_hours']:.2f} | "
              f"{g['comfort_violation_degree_hours']:.2f} | {summary['comfort_delta_degree_hours']:+.2f} |",
              f"| Setpoint changes | {r['setpoint_changes_count']} | {g['setpoint_changes_count']} | |",
              "", f"Determinism: live twin {summary['determinism']['live_energy_kwh']} kWh, offline replay "
              f"{summary['determinism']['replay_energy_kwh']} kWh, match = {summary['determinism']['match']}.",
              f"Audit chain: valid = {live['audit'].get('valid')}, records = {live['audit'].get('records_checked')}.",
              "", f"Scope: {summary['scope']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.demo")
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", default="artifacts")
    p.add_argument("--project", default=os.environ.get("COMPOSE_PROJECT_NAME", ""))
    p.add_argument("--days", type=int, default=7)
    a = p.parse_args(argv)
    compose = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.governed.yaml"]
    if a.project:
        compose[2:2] = ["-p", a.project]
    s = Stack(compose)
    work = Path(a.out) / f"{a.run_id}-work"
    work.mkdir(parents=True, exist_ok=False)
    live = run(s, work, a.days)
    summary = summarize(live)
    (work / "ledger.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in live["ledger"]),
                                       encoding="utf-8")
    (work / "summary.md").write_text(render(summary, live), encoding="utf-8")
    (work / "summary.json").write_text(json.dumps({"summary": summary, "counts": live["counts"],
                                                   "audit": live["audit"]}, indent=2), encoding="utf-8")
    c = live["counts"]
    ok = (summary["determinism"]["match"] and live["audit"].get("valid") is True
          and c.get("attacks", 0) == c.get("attacks_blocked", -1) and c.get("applied", 0) > 0)
    write_bundle(a.out, suite_id=SUITE_ID, run_id=a.run_id,
                 manifest_extra={"scope": "SIMULATED_CONTROLLED_PILOT", "vantage": "same_test_deployment",
                                 "work_dir": str(work), "strategy": "P1", "reviewer_model": "careful"},
                 metrics={**summary, "counts": c},
                 trace=s.trace,
                 claims={"authorized_effect_through_remora_dispatcher": "TESTED_DOCKER" if c.get("applied")
                         else "NOT_ESTABLISHED",
                         "real_building_savings": "NOT_ESTABLISHED",
                         "physical_ot_safety": "NOT_ESTABLISHED"},
                 results=[{"case_id": "DEMO", "outcome": "PASS" if ok else "FAIL",
                           "detail": json.dumps({"savings_nok": summary["savings_nok"],
                                                 "determinism": summary["determinism"]["match"],
                                                 "attacks_blocked": c.get("attacks_blocked")})}])
    print(render(summary, live))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
