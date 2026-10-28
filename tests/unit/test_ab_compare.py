"""runner.ab_compare: arms A, B and C on one episode, offline (ADR-004 step 5).

Arm A runs for real (offline). Arms B and C are synthetic ledgers shaped
like the governed demo's output, built from the committed episode: B-careful
refuses every attack at REMORA or the reviewer; B-rubber_stamp lets the
off-plan value through; C-rubber_stamp has the reference inline edge refuse
it. The report must carry all arms with exact denominators, the per-rule
table, the inline edge's contribution, the offline edge pass on arms that
exported trails, the edges' record on the sealed sets, and the non-claims.
"""
from __future__ import annotations

import json
from pathlib import Path

from runner import ab_compare, demo, episode
from runner.federation_fixtures import canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
EP = ROOT / "scenarios" / "episodes" / "trondheim-week-2026w03-hvac-v1-p1-7d-v1.json"


def _row(p, *, applied, **k):
    a = p["call"]["arguments"]
    r = {"sim_time_s": p["at_sim_s"], "origin": episode.ORIGIN_MAP and {v: k2 for k2, v in episode.ORIGIN_MAP.items()}[p["origin"]],
         "zone_id": a["zone_id"], "to_c": a["setpoint_c"], "from_c": p.get("from_c"), "intent_ref": p.get("intent_ref"),
         "proposal_id": f"pid-{p['id']}", "decision": "verify", "reasons": ["authority_resolved_review"],
         "reviewer": {"model": "careful", "approved": True, "reason": "plan step"}, "applied": applied}
    r.update(k)
    return r


def _recorded(pid: str, zone: str, value: float, t: int) -> dict:
    """REMORA's evidence and the observer's sealed record for one applied setpoint, as the demo exports them."""
    ev = {"lifecycle": {"events": [{"event": "assessed", "payload": {"proposal_id": pid}},
                                   {"event": "execution_result", "payload": {"proposal_id": pid}}]},
          "effect_verification": {"history": [{"event": "effect_verified", "proposal_id": pid,
                                               "observed": {"setpoint_c": value}, "vantage": "same_deployment"}]}}
    ev["manifest"] = {"event_count": 2, "section_sha256": {k: canonical_sha256(v) for k, v in ev.items()}}
    obs = {"schema": "ot-observation/v1", "operation_id": pid, "zone_id": zone, "observed_setpoint_c": value,
           "sim_time_s": t}
    return {"evidence": ev, "effect_observation": {**obs, "observation_digest": "sha256:" + canonical_sha256(obs)}}


def _governed(ep, work: Path, *, reviewer: str, inline_refuses: bool, export_trails: bool) -> None:
    work.mkdir(parents=True)
    rows, decisions = [], []
    rev = 0
    for p in ep["proposals"]:
        o = p["origin"]
        if o == "schedule":
            rev += 1
            r = _row(p, applied=True)
            r["reviewer"]["model"] = reviewer
            if export_trails:
                a = p["call"]["arguments"]
                d = {"proposal_id": r["proposal_id"], "ledger_row": {**r, "sim_time_s": p["at_sim_s"]},
                     "envelope": {},
                     "twin_operation": {"operation_id": r["proposal_id"], "status": "APPLIED",
                                        "zone_id": a["zone_id"], "new_setpoint_c": a["setpoint_c"],
                                        "sim_time_s": p["at_sim_s"]},
                     **_recorded(r["proposal_id"], a["zone_id"], a["setpoint_c"], p["at_sim_s"])}
                decisions.append(d)
                r["capture"] = demo.capture_digests(d)
        elif o == "injected:out_of_contract":
            r = _row(p, applied=False, decision="refused", status=409, refusal="contract_violation")
            del r["reviewer"]
        elif o == "injected:replay":
            r = {"sim_time_s": p["at_sim_s"], "origin": "attack:replay", "replayed_item": "x", "status": 409,
                 "detail": "token_already_used", "applied": False}
        elif o == "injected:no_work_order":
            r = _row(p, applied=False, intent_ref=None)
            r["reviewer"] = {"model": reviewer, "approved": False, "reason": "no approved day plan"}
        else:  # off_plan: the careful reviewer refuses; rubber_stamp approves; the inline edge may refuse
            if reviewer == "careful":
                r = _row(p, applied=False)
                r["reviewer"] = {"model": "careful", "approved": False, "reason": "not in plan"}
            elif inline_refuses:
                r = _row(p, applied=False, inline_edge={"edge_id": "inline-template-admission", "verdict": "refuse",
                                                        "reason": "value_not_in_work_order", "available": True, "latency_ms": 2.0})
                r["reviewer"]["model"] = "rubber_stamp"
            else:
                rev += 1
                r = _row(p, applied=True)
                r["reviewer"]["model"] = "rubber_stamp"
        rows.append(r)
    rows.append({"origin": "attack:summary", "attacks": 4})
    (work / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    (work / "arm-b-metrics.json").write_text(json.dumps({"reviewer_model": reviewer, "arm": "C" if inline_refuses else "B",
                                                         "inline_edge": "inline-template-admission" if inline_refuses else None,
                                                         "revision_check": {"before": 0, "after": rev}}), "utf-8")
    if export_trails:
        (work / "decisions").mkdir()
        for d in decisions:
            (work / "decisions" / f"{d['proposal_id']}.json").write_text(json.dumps(d), "utf-8")


def test_abc_report_from_arm_a_and_synthetic_governed_arms(tmp_path):
    ep = episode.load(EP)
    _governed(ep, tmp_path / "B-careful", reviewer="careful", inline_refuses=False, export_trails=True)
    _governed(ep, tmp_path / "B-rubber", reviewer="rubber_stamp", inline_refuses=False, export_trails=False)
    _governed(ep, tmp_path / "C-rubber", reviewer="rubber_stamp", inline_refuses=True, export_trails=False)
    out = tmp_path / "abc"
    rc = ab_compare.main(["--episode", str(EP), "--out", str(out), "--run-arm-a",
                          "--arm", f"B-careful={tmp_path / 'B-careful'}", "--arm", f"B-rubber_stamp={tmp_path / 'B-rubber'}",
                          "--arm", f"C-rubber_stamp={tmp_path / 'C-rubber'}"])
    assert rc == 0
    s = json.loads((out / "abc-summary.json").read_text("utf-8"))
    by = {a["label"]: a for a in s["arms"]}
    assert set(by) == {"A", "B-careful", "B-rubber_stamp", "C-rubber_stamp"}
    mA, mB, mBr, mC = (by[k]["metrics"] for k in ("A", "B-careful", "B-rubber_stamp", "C-rubber_stamp"))
    assert mA["forbidden_effects"] >= 1 and by["A"]["architecture"] == "KNOWN_NEGATIVE"
    assert mB["forbidden_effects"] == 0 and mB["permitted_effects"] == 44 and mB["revision_check"]["match"]
    assert mBr["forbidden_effects"] == 1 and mBr["by_rule"]["outside_work_order"]["effects"] == 1
    assert mC["forbidden_effects"] == 0 and mC["refusals_by_layer_total"]["edge"] == 1
    assert by["C-rubber_stamp"]["inline_edges"]["inline-template-admission"] == {"consulted": 1, "refused_forbidden": 1}
    assert by["B-careful"]["offline_edges"]["cases"] == 44 and by["B-rubber_stamp"]["offline_edges"] is None
    tmpl = next(e for e in by["B-careful"]["offline_edges"]["edges"] if e["edge_id"] == "template-reference-verifier")
    assert tmpl["available"] and tmpl["counts"] == {"ESTABLISHED": 44}
    assert tmpl["counts_on_recorded_inputs"] == {"ESTABLISHED": 44}
    assert tmpl["counts_strict_vantage"] == {"NOT_ESTABLISHED": 44}  # same_deployment observations
    assert any(r["edge_id"] == "template-reference-verifier" for r in s["edge_records"])
    md = (out / "abc-report.md").read_text("utf-8")
    for must in ("## How to read this report", "| **A** |", "| **C-rubber_stamp** |", "`outside_work_order`",
                 "### Inline edges (arm C)", "`inline-template-admission` | 1 | 1 | 0 | 0 | 0 |",
                 "### Record on the sealed sets", "## What this does not establish"):
        assert must in md, must
    assert "safer" not in md.replace("No arm is called safer", "").replace("no arm is safer in prose", "")
    html = (out / "abc-report.html").read_text("utf-8")
    assert "<table>" in html and "inline-template-admission" in html and "prefers-color-scheme" in html
