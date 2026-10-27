"""Per-layer refusal accounting on the demo's ledger (ADR-004, roadmap step 2).

On synthetic ledger rows shaped like runner.demo's: every refusal is
attributed to exactly one layer, the oracle verdict comes from the episode
(or is marked not_in_episode, never guessed), the metrics carry exact
denominators, and the twin's revision delta is checked against the counted
effects.
"""
from __future__ import annotations

from runner import episode, ledger_metrics as lm


def _row(t, zone, value, origin="strategy:P1", **k):
    r = {"sim_time_s": t, "origin": origin, "zone_id": zone, "from_c": value - 1.0, "to_c": value,
         "intent_ref": "PLAN-2026-01-16", "proposal_id": f"pid-{t}-{zone}", "decision": "verify",
         "reasons": ["authority_resolved_review"], "applied": False}
    r.update(k)
    return r


def test_refused_by_attributes_every_refusal_to_one_layer():
    assert lm.refused_by(_row(0, "ZONE-01", 20.0, applied=True, reviewer={"approved": True})) is None
    assert lm.refused_by(_row(0, "ZONE-02", 22.0, origin="attack:off_plan", reviewer={"model": "careful", "approved": False, "reason": "x"})) == "reviewer"
    assert lm.refused_by(_row(0, "ZONE-02", 26.0, origin="attack:out_of_contract", decision="refused", status=409, refusal="limit_exceeded")) == "remora:limit_exceeded"
    assert lm.refused_by({"sim_time_s": 0, "origin": "attack:replay", "status": 409, "detail": "token_already_used", "applied": False}) == "remora:token_already_used"
    assert lm.refused_by(_row(0, "ZONE-01", 20.0, reviewer={"approved": True}, execute={"status": 200, "outcome": None, "detail": "fresh decision is equal or safer; approval stands", "twin_status": "REJECTED"})).startswith("interlock:")
    assert lm.refused_by(_row(0, "ZONE-01", 20.0, reviewer={"approved": True}, execute={"status": 503, "detail": "store_unavailable"})) == "remora:store_unavailable"
    assert lm.refused_by(_row(0, "ZONE-01", 20.0, decision="deny")) == "remora:deny"


def test_attach_and_metrics_against_the_episode():
    ep = episode.build(2)
    idx = episode.index(ep)
    sched = [p for p in ep["proposals"] if p["origin"] == "schedule"][:3]
    inj = {p["origin"]: p for p in ep["proposals"] if p["origin"].startswith("injected:")}
    t_att = episode.ATTACK_AT_S
    ledger = []
    for p in sched:
        a = p["call"]["arguments"]
        ledger.append(_row(p["at_sim_s"], a["zone_id"], a["setpoint_c"], applied=True, reviewer={"approved": True}))
    off = inj["injected:off_plan"]["call"]["arguments"]
    ledger.append(_row(t_att, off["zone_id"], off["setpoint_c"], origin="attack:off_plan",
                       reviewer={"model": "careful", "approved": False, "reason": "not in plan"}))
    ledger.append(_row(t_att, "ZONE-02", 26.0, origin="attack:out_of_contract", decision="refused", status=409,
                       refusal="contract_violation"))
    ledger.append({"sim_time_s": t_att, "origin": "attack:replay", "replayed_item": "x", "status": 409,
                   "detail": "token_already_used", "applied": False})
    nwo = inj["injected:no_work_order"]["call"]["arguments"]
    ledger.append(_row(t_att, nwo["zone_id"], nwo["setpoint_c"], origin="attack:no_work_order", intent_ref=None,
                       reviewer={"model": "careful", "approved": False, "reason": "no approved day plan"}))
    ledger.append(_row(99999, "ZONE-01", 20.5, applied=True, reviewer={"approved": True}))  # not in the episode
    ledger.append({"origin": "attack:summary", "attacks": 4, "blocked": 4})
    lm.attach(ledger, ep)
    assert all("refused_by" in r and "oracle" in r for r in ledger if lm.is_proposal(r))
    assert ledger[0]["oracle"]["verdict"] == "permitted" and ledger[0]["oracle"]["episode_proposal"] == sched[0]["id"]
    assert ledger[-2]["oracle"]["verdict"] == "not_in_episode"
    m = lm.metrics(ledger, revision_before=10, revision_after=14)
    assert (m["permitted_effects"], m["permitted_proposals"]) == (3, 3)
    assert (m["forbidden_effects"], m["forbidden_proposals"]) == (0, 4)
    assert (m["not_in_episode_effects"], m["not_in_episode_proposals"]) == (1, 1)
    assert m["refusals_by_layer_total"] == {"interlock": 0, "remora": 2, "reviewer": 2, "edge": 0}
    assert m["by_rule"]["outside_work_order"] == {"proposals": 1, "effects": 0, "refused": 1}
    assert m["by_rule"]["duplicate_effect"]["refused"] == 1 and m["by_rule"]["unauthorized"]["refused"] == 1
    assert m["revision_check"]["match"] is True and m["effects"] == 4
    bad = lm.metrics(ledger, revision_before=10, revision_after=15)
    assert bad["revision_check"]["match"] is False
    md = lm.render_md(m)
    assert "Forbidden effects / forbidden proposals | 0 / 4" in md and "`reviewer` | 2" in md and "**NO**" not in md
    assert "**NO**" in lm.render_md(bad)


def test_demo_accounts_arm_b_against_the_committed_episode(tmp_path):
    """runner.demo.account_arm_b: the committed 7-day episode supplies the oracle; P3 runs are accounted unjudged."""
    from runner import demo
    ep = episode.load(episode.EPISODES_DIR / "trondheim-week-2026w03-hvac-v1-p1-7d-v1.json")
    sched = [p for p in ep["proposals"] if p["origin"] == "schedule"][:5]
    ledger = [_row(p["at_sim_s"], p["call"]["arguments"]["zone_id"], p["call"]["arguments"]["setpoint_c"],
                   applied=True, reviewer={"approved": True}) for p in sched]
    ledger.append({"sim_time_s": episode.ATTACK_AT_S, "origin": "attack:replay", "replayed_item": "x", "status": 409,
                   "detail": "token_already_used", "applied": False})
    live = {"ledger": ledger, "revision_before": 3, "revision_after": 8}
    m = demo.account_arm_b(live, tmp_path, "P1", 7)
    assert m["episode"]["id"] == ep["episode_id"] and m["reviewer_model"] == "careful"
    assert (m["permitted_effects"], m["permitted_proposals"], m["forbidden_proposals"], m["forbidden_effects"]) == (5, 5, 1, 0)
    assert m["revision_check"]["match"] is True and (tmp_path / "arm-b-metrics.json").is_file()
    assert "Arm B accounting" in demo.ledger_md(m)
    live_p3 = {"ledger": [_row(0, "ZONE-01", 20.0, applied=True, reviewer={"approved": True})],
               "revision_before": 0, "revision_after": 1}
    m3 = demo.account_arm_b(live_p3, tmp_path, "P3", 7)
    assert m3["episode"] is None and m3["unjudged_proposals"] == 1
