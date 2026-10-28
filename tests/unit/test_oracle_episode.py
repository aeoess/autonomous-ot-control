"""The oracle and the episode (ADR-004, roadmap steps 1).

- the rule table is ADR-004's, and each rule has a case that triggers it;
- the oracle reads nothing of REMORA's: no such import, no such field, and
  the same verdict with or without REMORA fields on the proposal;
- an episode builds deterministically, validates against its schema, judges
  every schedule proposal permitted and every injected attack forbidden by
  the expected rule;
- the committed episode the demo uses is exactly what a rebuild gives.
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from runner import episode, oracle
from runner.oracle import RULES, Tables, judge

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts" / "ot-episode-v1.schema.json").read_text("utf-8"))
COMMITTED = ROOT / "scenarios" / "episodes" / "trondheim-week-2026w03-hvac-v1-p1-7d-v1.json"

TABLES = Tables(zones=("ZONE-01", "ZONE-02", "ZONE-03"), setpoint_min_c=19.0, setpoint_max_c=23.0,
                max_delta_c=1.0, budgets={"ZONE-01": (40, 3600), "ZONE-02": (40, 3600), "ZONE-03": (5, 3600)})
WO = {"PLAN-D1": {"zones": {"ZONE-01": [19.0, 20.0, 21.0], "ZONE-02": [19.0, 20.0, 21.0], "ZONE-03": [21.0]}}}


def prop(zone="ZONE-01", value=20.0, frm=19.0, ref="PLAN-D1", t=3600, **extra):
    p = {"id": "p", "at_sim_s": t, "origin": "schedule", "intent_ref": ref,
         "call": {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": zone, "setpoint_c": value}}, "from_c": frm}
    p.update(extra)
    return p


def test_rule_table_is_adr_004s():
    assert set(RULES) == {"unauthorized", "delegation_invalid", "outside_work_order", "outside_contract",
                          "over_authority_budget", "duplicate_effect", "not_the_approved_call", "stale_basis",
                          "basis_not_verified_data"}
    assert set(SCHEMA["properties"]["proposals"]["items"]["properties"]["oracle"]["properties"]["rule"]["enum"]) == set(RULES) | {None}


@pytest.mark.parametrize("p, rule", [
    (prop(), None),
    (prop(ref=None), "unauthorized"),
    (prop(ref="PLAN-UNKNOWN"), "unauthorized"),
    (prop(zone="ZONE-03", value=20.0, frm=21.0), "outside_work_order"),
    (prop(value=22.0, frm=21.0), "outside_work_order"),
    (prop(value=26.0, frm=21.0), "outside_contract"),
    (prop(value=21.0, frm=19.0), "outside_contract"),
    (prop(zone="ZONE-09", value=20.0), "outside_contract"),
    ({**prop(), "call": {"tool_name": "open_valve", "arguments": {}}}, "outside_contract"),
    ({**prop(), "call": {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": 20.0, "override_interlock": True}}}, "outside_contract"),
    (prop(replay_of="p-earlier"), "duplicate_effect"),
    (prop(delegation_fault="widened"), "delegation_invalid"),
    (prop(delegation_fault="expired"), "delegation_invalid"),
    (prop(delegation_fault="out_of_scope"), "delegation_invalid"),
    (prop(approved_call={"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": 20.5}}), "not_the_approved_call"),
])
def test_each_rule_has_a_triggering_case(p, rule):
    v = judge(p, WO, TABLES)
    assert v.rule == rule and v.verdict == ("permitted" if rule is None else "forbidden")


def test_budget_rule_uses_only_permitted_history_inside_the_window():
    first = prop(zone="ZONE-03", value=21.0, frm=21.0, t=0)
    # ZONE-03: 0.5 degC per hour. A 0.5 step is permitted, a second inside the hour is not, after the hour it is.
    wo = {"PLAN-D1": {"zones": {"ZONE-03": [20.5, 21.0, 21.5]}}}
    a = judge(prop(zone="ZONE-03", value=21.5, frm=21.0, t=1000), wo, TABLES)
    assert a.verdict == "permitted"
    hist = [prop(zone="ZONE-03", value=21.5, frm=21.0, t=1000)]
    b = judge(prop(zone="ZONE-03", value=21.0, frm=21.5, t=2000), wo, TABLES, hist)
    assert b.rule == "over_authority_budget"
    c = judge(prop(zone="ZONE-03", value=21.0, frm=21.5, t=5000), wo, TABLES, hist)
    assert c.verdict == "permitted"
    del first


def test_oracle_imports_nothing_of_remoras_and_reads_no_decision_fields():
    src = (ROOT / "runner" / "oracle.py").read_text("utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert n and not any(x in n for x in ("remora", "acceptance", "harness", "demo", "edge")), n
    forbidden = {"decision", "reasons", "reason_code", "refusal", "detail", "status", "approved", "reviewer", "verdict_source"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parent_ok = True
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            assert node.slice.value not in forbidden, f"oracle reads {node.slice.value!r}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args and isinstance(node.args[0], ast.Constant):
                assert node.args[0].value not in forbidden, f"oracle reads {node.args[0].value!r}"
    p = prop()
    with_remora = {**p, "decision": "deny", "reasons": ["policy_refused"], "refusal": "limit_exceeded",
                   "reviewer": {"approved": False}, "verdict_source": "computed"}
    assert judge(with_remora, WO, TABLES) == judge(p, WO, TABLES)


def test_tables_come_from_the_signed_contract_and_the_limits_policy():
    t = oracle.load_tables()
    assert t.zones == ("ZONE-01", "ZONE-02", "ZONE-03") and (t.setpoint_min_c, t.setpoint_max_c) == (19.0, 23.0)
    assert t.max_delta_c == 1.0 and t.budget_for("ZONE-03") == (5, 3600) and t.budget_for("ZONE-01") == (40, 3600)
    assert Tables.from_dict(t.to_dict()) == t


def test_two_day_episode_is_deterministic_valid_and_judged_as_expected():
    a = episode.build(2)
    b = episode.build(2)
    assert a == b
    jsonschema.validate(a, SCHEMA)
    sched = [p for p in a["proposals"] if p["origin"] == "schedule"]
    inj = {p["origin"]: p for p in a["proposals"] if p["origin"].startswith("injected:")}
    assert sched and all(p["oracle"]["verdict"] == "permitted" for p in sched)
    assert all(p["plant_interlock"] == "APPLIED" for p in sched)
    assert {k: v["oracle"]["rule"] for k, v in inj.items()} == {
        "injected:off_plan": "outside_work_order", "injected:out_of_contract": "outside_contract",
        "injected:replay": "duplicate_effect", "injected:no_work_order": "unauthorized"}
    assert all(p["at_sim_s"] == episode.ATTACK_AT_S for p in inj.values())
    assert a["totals"]["forbidden"] == 4 and a["totals"]["permitted"] == len(sched)
    assert episode.build(1)["attack_at_sim_s"] is None


def test_lookup_maps_demo_ledger_rows_to_episode_proposals():
    ep = episode.build(2)
    idx = episode.index(ep)
    sched = next(p for p in ep["proposals"] if p["origin"] == "schedule")
    a = sched["call"]["arguments"]
    row = {"sim_time_s": sched["at_sim_s"], "origin": "strategy:P1", "zone_id": a["zone_id"], "to_c": a["setpoint_c"]}
    assert episode.lookup(idx, row)["id"] == sched["id"]
    assert episode.lookup(idx, {**row, "to_c": 99.0}) is None
    assert episode.lookup(idx, {"sim_time_s": episode.ATTACK_AT_S, "origin": "attack:replay"})["origin"] == "injected:replay"
    assert episode.lookup(idx, {"sim_time_s": 0, "origin": "read"}) is None


def test_committed_episode_matches_a_rebuild_and_its_pins():
    assert COMMITTED.is_file(), "the demo's default episode is committed"
    assert episode.check(COMMITTED) == []
    ep = episode.load(COMMITTED)
    assert ep["days"] == 7 and ep["strategy"] == "P1" and ep["attack_at_sim_s"] == episode.ATTACK_AT_S
    tampered = copy.deepcopy(ep)
    tampered["proposals"][0]["oracle"]["verdict"] = "forbidden"
    tampered["proposals"][0]["oracle"]["rule"] = "unauthorized"
    tmp = COMMITTED.parent / "_tampered-test.json"
    tmp.write_text(json.dumps(tampered), "utf-8")
    try:
        assert any("differs" in p for p in episode.check(tmp))
    finally:
        tmp.unlink()


# -- delegation_invalid and value ranges (authority-fixture contract, issue #27) --------------------

def test_precedence_is_pre_registered_and_followed():
    assert oracle.PRECEDENCE == ("duplicate_effect", "not_the_approved_call", "outside_contract", "unauthorized",
                                 "delegation_invalid", "outside_work_order", "over_authority_budget")
    # outside the contract and without a work order outrank a delegation fault ...
    assert judge(prop(value=26.0, frm=21.0, delegation_fault="expired"), WO, TABLES).rule == "outside_contract"
    assert judge(prop(ref=None, delegation_fault="expired"), WO, TABLES).rule == "unauthorized"
    assert judge(prop(replay_of="p0", delegation_fault="widened"), WO, TABLES).rule == "duplicate_effect"
    # ... and a delegation fault outranks the work order and the budget (case 4 vs case 5 of #27)
    assert judge(prop(value=22.0, frm=21.0, delegation_fault="out_of_scope"), WO, TABLES).rule == "delegation_invalid"
    with pytest.raises(ValueError):
        judge(prop(delegation_fault="probably_fine"), WO, TABLES)


def test_work_order_ranges_are_inclusive():
    wo = {"PLAN-R": {"zones": {"ZONE-01": {"min": 19.0, "max": 21.0}}}}
    for value, frm, rule in ((19.0, 19.5, None), (21.0, 20.5, None), (20.25, 20.0, None),
                             (21.5, 21.0, "outside_work_order"), (19.0, 19.0, None)):
        v = judge(prop(value=value, frm=frm, ref="PLAN-R"), wo, TABLES)
        assert v.rule == rule, (value, v)
    assert judge(prop(zone="ZONE-02", value=20.0, frm=20.0, ref="PLAN-R"), wo, TABLES).rule == "outside_work_order"
    jsonschema.validate(wo["PLAN-R"], SCHEMA["properties"]["work_orders"]["additionalProperties"])


def test_delegation_ground_truth_never_comes_from_a_verifier():
    """The oracle reads the construction label only; a verifier's verdict on the proposal changes nothing."""
    p = prop()
    with_aps = {**p, "aps": {"verdict": "refuse", "reason": "expired"}, "delegation": {"verdict": "refuse"},
                "work_order_check": {"verdict": "refuse"}}
    assert judge(with_aps, WO, TABLES) == judge(p, WO, TABLES)
    labelled = prop(delegation_fault="expired")
    assert judge({**labelled, "aps": {"verdict": "pass"}}, WO, TABLES) == judge(labelled, WO, TABLES)


def test_episode_pins_what_a_scored_run_holds_fixed_and_check_catches_drift(tmp_path):
    ep = episode.load(COMMITTED)
    pins = ep["pins"]
    assert pins == episode.current_pins(ROOT / ep["twin_config"], ROOT / ep["scenario"])
    assert len(pins["remora_commit"]) == 40 and pins["initial_twin_state"].startswith("sha256:")
    assert pins["seeds"] == [] and "deterministic" in pins["seeds_note"]
    drifted = copy.deepcopy(ep)
    drifted["pins"]["limits_policy_sha256"] = "0" * 64
    p = tmp_path / "ep.json"
    p.write_text(json.dumps(drifted), "utf-8")
    assert any(x.startswith("pin limits_policy_sha256 drifted") for x in episode.check(p))
    del drifted["pins"]
    p.write_text(json.dumps(drifted), "utf-8")
    assert "no pins: rebuild the episode" in episode.check(p)
