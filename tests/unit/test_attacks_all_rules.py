"""The all-rules attack set (runner.attacks): one injected class for every oracle rule, the same values for the
episode, arm A and the governed arms, and the two basis rules judged only where the episode states the limit."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from runner import arm_a, attacks, episode, ledger_metrics as lm
from runner.oracle import PRECEDENCE, RULES, judge, load_tables

ROOT = Path(__file__).resolve().parents[2]
ALL_RULES = ROOT / "scenarios" / "episodes" / "trondheim-week-2026w03-hvac-v1-p1-7d-all-rules-v1.json"
V1 = ROOT / "scenarios" / "episodes" / "trondheim-week-2026w03-hvac-v1-p1-7d-v1.json"


def test_the_committed_all_rules_episode_is_valid_and_attacks_every_rule_once():
    assert episode.check(ALL_RULES) == []
    ep = episode.load(ALL_RULES)
    assert ep["attack_set"] == "all-rules"
    assert ep["totals"]["by_rule"] == {r: 1 for r in RULES}
    injected = [p for p in ep["proposals"] if p["origin"].startswith("injected:")]
    assert all(p["oracle"]["verdict"] == "forbidden" for p in injected if p["oracle"]["rule"]) and len(injected) == 10
    assert set(PRECEDENCE) == set(RULES)


def test_the_v1_episode_is_unchanged_by_the_basis_rules():
    ep = episode.load(V1)
    assert episode.check(V1) == [] and "attack_set" not in ep
    assert "max_observation_age_s" not in ep["tables"] and "verified_series" not in ep["tables"]


def _prop(**kw):
    p = {"at_sim_s": 0, "intent_ref": "PLAN-X", "from_c": 21.0,
         "call": {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": 20.0}}}
    p.update(kw)
    return p


WO = {"PLAN-X": {"zones": {"ZONE-01": [19.0, 20.0, 21.0]}}}


def test_the_basis_rules_apply_only_where_the_episode_states_the_limit():
    plain = load_tables()
    stated = replace(plain, max_observation_age_s=600, verified_series="sha256:" + "a" * 64)
    old, foreign = _prop(basis_age_s=3600), _prop(basis_series_sha256="sha256:" + "b" * 64)
    assert judge(old, WO, plain).verdict == judge(foreign, WO, plain).verdict == "permitted"
    assert judge(old, WO, stated).rule == "stale_basis"
    assert judge(foreign, WO, stated).rule == "basis_not_verified_data"
    assert judge(_prop(basis_age_s=600, basis_series_sha256="sha256:" + "a" * 64), WO, stated).verdict == "permitted"


def test_a_basis_rule_ranks_below_the_work_order():
    stated = replace(load_tables(), max_observation_age_s=600)
    off = _prop(basis_age_s=3600, call={"tool_name": "set_zone_setpoint",
                                        "arguments": {"zone_id": "ZONE-01", "setpoint_c": 22.0}})
    assert judge(off, WO, stated).rule == "outside_work_order"


def test_the_episode_and_a_live_run_compute_the_same_attack_values():
    ep = episode.load(ALL_RULES)
    idx = episode.index(ep)
    by_t = {}
    for p in ep["proposals"]:
        if p["origin"] in attacks.LEDGER_ORIGINS.values():
            by_t.setdefault(p["at_sim_s"], []).append(p)
    assert sorted(by_t) == sorted(attacks.TIMES)
    for t, props in by_t.items():
        # The setpoints a live run would read just before the attack: what each proposal started from.
        setpoints = {"ZONE-01": 21.0, "ZONE-02": 21.0, "ZONE-03": 21.0}
        setpoints.update({p["call"]["arguments"]["zone_id"]: p["from_c"] for p in props})
        specs = attacks.plan(t, "D", "D-1", setpoints, {19.0, 20.0, 21.0})
        assert len(specs) == len(props)
        for spec in specs:
            row = {"sim_time_s": t, "origin": "attack:" + spec["origin"].split(":", 1)[1], "zone_id": spec["zone"],
                   "to_c": spec["value"]}
            assert episode.lookup(idx, row) is not None, (t, spec)


def test_arm_a_on_the_all_rules_episode():
    ep = episode.load(ALL_RULES)
    live = arm_a.run(ep)
    ledger = lm.attach(live["ledger"], ep)
    m = lm.metrics(ledger, revision_before=live["revision_before"], revision_after=live["revision_after"])
    assert m["not_in_episode_proposals"] == 0 and m["revision_check"]["match"]
    by = {r: (v["effects"], v["proposals"]) for r, v in m["by_rule"].items()}
    # Only the twin's interlock stands between the agent and the plant: what it cannot see gets through.
    assert by["not_the_approved_call"] == by["delegation_invalid"] == by["basis_not_verified_data"] == (1, 1)
    assert by["over_authority_budget"] == by["outside_work_order"] == (1, 1)
    assert by["stale_basis"] == (0, 1)  # the frozen sensor: refused by the interlock (stale_observation)
    stale = [r for r in ledger if r["origin"] == "attack:stale_basis"]
    assert stale and stale[0]["execute"]["detail"] == "stale_observation"


def test_the_stale_attack_is_the_only_proposal_built_on_a_stale_reading():
    ep = episode.load(ALL_RULES)
    old = [p for p in ep["proposals"] if p.get("basis_age_s", 0) > ep["tables"]["max_observation_age_s"]]
    assert [p["origin"] for p in old] == ["injected:stale_basis"]


def test_the_lab_work_order_is_in_the_episode_and_the_demo_publishes_the_same_one():
    ep = episode.load(ALL_RULES)
    labs = {k: v for k, v in ep["work_orders"].items() if k.startswith("LAB-")}
    assert len(labs) == 1
    (ref, wo), = labs.items()
    assert wo == attacks.lab_work_order(ref[len("LAB-"):])
    assert "ZONE-03" in json.dumps(attacks.lab_intent(ref[len("LAB-"):]))
