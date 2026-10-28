"""runner.edge_pass: offline edges beside a governed run (ADR-004).

On a synthetic work directory shaped like the demo's export (`decisions/`
trails with REMORA's evidence, the twin's operation record and the
observer's sealed record; `ledger.jsonl` with the capture digests):
- every applied setpoint trail becomes a case; refused proposals and other
  tools are counted and left out;
- the reference edge establishes a consistent trail, contradicts one whose
  twin record disagrees with the ledger, and cannot establish one with no
  exported twin record;
- the pass never makes up what an edge checks: the observation is REMORA's
  recorded readback, the dispatch count is REMORA's lifecycle, the digest
  is the one captured at export (or REMORA's export manifest); a missing
  record is NOT_ESTABLISHED, a changed or self-contradicting one CONTRADICTED;
- an edge's ESTABLISHED counts only on recorded inputs, and strict vantage
  also needs an independent observation;
- an edge that cannot run is reported unavailable, and the pass still
  completes on the edges that could;
- the pass fails only when no edge ran at all.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from runner import demo, edge_pass
from runner.federation_fixtures import canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_template"


def _evidence(pid: str, observed: float | None, *, executions: int = 1) -> dict:
    events = [{"event": "assessed", "payload": {"proposal_id": pid}},
              *({"event": "execution_result", "payload": {"proposal_id": pid}} for _ in range(executions))]
    ev: dict = {"lifecycle": {"events": events},
                "effect_verification": {"history": [] if observed is None else [
                    {"event": "effect_verified", "proposal_id": pid, "observed": {"setpoint_c": observed},
                     "vantage": "same_deployment"}]}}
    ev["manifest"] = {"event_count": len(events), "proposal_id": pid,
                      "section_sha256": {k: canonical_sha256(v) for k, v in ev.items()}}
    return ev


def _observer(pid: str, zone: str, observed: float, t: int) -> dict:
    body = {"schema": "ot-observation/v1", "operation_id": pid, "zone_id": zone,
            "observed_setpoint_c": observed, "sim_time_s": t, "vantage": "same_test_deployment"}
    return {**body, "observation_digest": "sha256:" + canonical_sha256(body)}


def _trail(pid: str, zone: str, value: float, t: int, *, applied: bool = True, twin_value: float | None = None,
           export_op: bool = True, tool: str | None = None, vantage: str = "same_deployment") -> dict:
    row = {"proposal_id": pid, "applied": applied, "zone_id": zone, "to_c": value, "sim_time_s": t,
           "decision": "verify", "reasons": ["authority_resolved_review"],
           "reviewer": {"model": "careful", "approved": True, "reason": "plan step"}}
    if tool:
        row["tool_name"] = tool
    actual = value if twin_value is None else twin_value
    # The twin's operation record, shaped as the twin exports it (GET /v1/operations/{id}).
    op = {"operation_id": pid, "status": "APPLIED", "tool_name": "set_zone_setpoint", "zone_id": zone,
          "target": zone, "requested_setpoint_c": value, "requested_value": value,
          "new_setpoint_c": actual, "new_value": actual, "sim_time_s": t}
    rec = {"proposal_id": pid, "ledger_row": row,
           "envelope": {"decision": "verify", "effect": {"ledger_entry": {"effect_vantage": vantage}}},
           "evidence": _evidence(pid, actual if applied else None, executions=1 if applied else 0),
           "twin_operation": op if (applied and export_op) else None,
           "effect_observation": _observer(pid, zone, actual, t) if applied else None}
    return rec


def _write(work: Path, trails: list[dict], *, capture: bool = True) -> Path:
    """The trail files, then the ledger with the capture digests the demo writes after export."""
    (work / "decisions").mkdir(parents=True, exist_ok=True)
    rows = []
    for t in trails:
        (work / "decisions" / f"{t['proposal_id']}.json").write_text(json.dumps(t), "utf-8")
        row = dict(t["ledger_row"])
        if capture:
            row["capture"] = demo.capture_digests(t)
        rows.append(row)
    (work / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    return work


def _work(tmp_path: Path) -> Path:
    trails = [_trail("p-ok-1", "ZONE-01", 20.0, 61200), _trail("p-ok-2", "ZONE-02", 19.5, 62100),
              _trail("p-bad", "ZONE-01", 21.0, 63000, twin_value=20.0),
              _trail("p-noop", "ZONE-03", 20.5, 64000, export_op=False),
              _trail("p-refused", "ZONE-01", 24.0, 65000, applied=False),
              _trail("p-tank", "TANK-01", 47.0, 66000, tool="set_tank_setpoint")]
    return _write(tmp_path / "demo-work", trails)


def _one(tmp_path: Path, trail: dict, *, capture: bool = True, mutate=None) -> dict:
    """Run the reference edge over one trail; ``mutate`` changes the trail file after capture."""
    work = _write(tmp_path / "w", [trail], capture=capture)
    if mutate is not None:
        path = work / "decisions" / f"{trail['proposal_id']}.json"
        t = json.loads(path.read_text("utf-8"))
        mutate(t)
        path.write_text(json.dumps(t), "utf-8")
    s = edge_pass.run_pass(work, tmp_path / "out", [TEMPLATE], "one")
    (e,) = s["edges"]
    return {"summary": s, "edge": e, "finding": e["findings"][0] if e["findings"] else None}


def test_pass_classifies_trails_and_reports_findings(tmp_path):
    work = _work(tmp_path)
    s = edge_pass.run_pass(work, tmp_path / "out", [TEMPLATE], "t1")
    assert s["cases"] == 4 and s["skipped"] == {"not_applied": 1, "other_tool": 1, "no_twin_operation_exported": 1}
    (e,) = s["edges"]
    assert e["available"] and e["fixtures_sha256_matches"] and e["unanswered"] == []
    assert e["counts"] == {"ESTABLISHED": 2, "CONTRADICTED": 1, "NOT_ESTABLISHED": 1}
    assert e["counts_on_recorded_inputs"] == {"ESTABLISHED": 2, "CONTRADICTED": 1, "NOT_ESTABLISHED": 1}
    # Every observation here is same_deployment: strict vantage establishes nothing.
    assert e["counts_strict_vantage"] == {"NOT_ESTABLISHED": 3, "CONTRADICTED": 1}
    by = {f["proposal_id"]: f for f in e["findings"]}
    assert by["p-bad"]["verdict"] == "CONTRADICTED" and by["p-bad"]["claim_id"] == "declared_effect_observed"
    assert by["p-noop"]["verdict"] == "NOT_ESTABLISHED" and by["p-noop"]["claim_id"] == "call_binding"
    src = s["inputs"]["by_claim"]
    assert src["evidence_digest_integrity"] == {"ledger capture digest": 4}
    assert src["single_dispatch"] == {"REMORA lifecycle: 1 execution_result event(s)": 4}
    assert src["observation_after_dispatch"] == {"observer record sim_time_s (sealed)": 4}
    md = (tmp_path / "out" / "edges.md").read_text("utf-8")
    assert "## Edges beside the run" in md and "`p-bad`" in md and "never change the run's outcome" in md
    assert "Strict vantage" in md and "Where each claim's input comes from" in md
    assert json.loads((tmp_path / "out" / "edges.json").read_text("utf-8"))["schema"] == "ot-edge-pass/v1"


def test_the_case_is_built_from_records_not_made_up():
    rec = _trail("p", "ZONE-01", 20.0, 61200)
    rec["evidence"]["effect_verification"]["history"][0]["observed"]["setpoint_c"] = 19.0
    rec["twin_operation"]["new_setpoint_c"] = 20.0
    case, basis, _ = edge_pass.trail_case(rec, demo.capture_digests(rec))
    # The observation is REMORA's readback, not the twin record's new_setpoint_c.
    assert case["observation"]["observed_setpoint_c"] == 19.0
    assert case["observation"]["observed_at_sim_s"] == 61200
    assert case["execution"]["executions"] == 1 and case["execution"]["executed"] is True
    assert basis["declared_effect_observed"]["source"].startswith("REMORA effect_verification readback")
    assert all(b["recorded"] for b in basis.values())


def test_a_correct_trail_is_established(tmp_path):
    r = _one(tmp_path, _trail("p", "ZONE-01", 20.0, 61200))
    assert r["edge"]["counts"] == {"ESTABLISHED": 1} and r["edge"]["counts_on_recorded_inputs"] == {"ESTABLISHED": 1}
    assert r["summary"]["inputs"]["findings"] == []


def test_strict_vantage_needs_an_independent_observation(tmp_path):
    same = _one(tmp_path / "a", _trail("p", "ZONE-01", 20.0, 61200))
    assert same["edge"]["counts_strict_vantage"] == {"NOT_ESTABLISHED": 1}
    indep = _one(tmp_path / "b", _trail("p", "ZONE-01", 20.0, 61200, vantage="independent_reader"))
    assert indep["edge"]["counts_strict_vantage"] == {"ESTABLISHED": 1}


def test_no_readback_is_not_established(tmp_path):
    t = _trail("p", "ZONE-01", 20.0, 61200)
    t["evidence"]["effect_verification"]["history"] = []
    t["evidence"]["manifest"]["section_sha256"]["effect_verification"] = canonical_sha256(
        t["evidence"]["effect_verification"])
    r = _one(tmp_path, t)
    assert r["finding"]["verdict"] == "NOT_ESTABLISHED" and r["finding"]["claim_id"] == "declared_effect_observed"


def test_an_old_trail_without_the_observer_record_is_not_established(tmp_path):
    t = _trail("p", "ZONE-01", 20.0, 61200)
    t["effect_observation"] = None
    r = _one(tmp_path, t)
    assert r["finding"]["verdict"] == "NOT_ESTABLISHED" and r["finding"]["claim_id"] == "observation_after_dispatch"
    assert r["summary"]["inputs"]["by_claim"]["observation_after_dispatch"] == {
        "missing: no sealed observer record with a simulated time": 1}


def test_a_double_execution_is_contradicted(tmp_path):
    t = _trail("p", "ZONE-01", 20.0, 61200)
    t["evidence"] = _evidence("p", 20.0, executions=2)
    r = _one(tmp_path, t)
    assert r["finding"]["verdict"] == "CONTRADICTED" and r["finding"]["claim_id"] == "single_dispatch"


def test_an_incomplete_lifecycle_is_not_established(tmp_path):
    t = _trail("p", "ZONE-01", 20.0, 61200)
    t["evidence"]["manifest"]["event_count"] = 5
    r = _one(tmp_path, t)
    assert r["finding"]["verdict"] == "NOT_ESTABLISHED" and r["finding"]["claim_id"] == "single_dispatch"


def test_evidence_changed_after_capture_is_contradicted(tmp_path):
    def tamper(t):
        t["evidence"]["effect_verification"]["history"][0]["detail"] = "edited"
    r = _one(tmp_path, _trail("p", "ZONE-01", 20.0, 61200), mutate=tamper)
    assert r["finding"]["verdict"] == "CONTRADICTED" and r["finding"]["claim_id"] == "evidence_digest_integrity"


def test_without_a_capture_digest_remoras_export_manifest_decides(tmp_path):
    ok = _one(tmp_path / "a", _trail("p", "ZONE-01", 20.0, 61200), capture=False)
    assert ok["edge"]["counts_on_recorded_inputs"] == {"ESTABLISHED": 1}
    assert ok["summary"]["inputs"]["by_claim"]["evidence_digest_integrity"] == {
        "REMORA export manifest (per-section SHA-256)": 1}
    # A section edited after REMORA exported it: the edge recomputes and agrees, the pass does not.
    t = _trail("p", "ZONE-01", 20.0, 61200)
    t["evidence"]["lifecycle"]["events"][0]["payload"]["note"] = "edited"
    bad = _one(tmp_path / "b", t, capture=False)
    assert bad["edge"]["counts"] == {"ESTABLISHED": 1}
    assert bad["edge"]["counts_on_recorded_inputs"] == {"CONTRADICTED": 1}
    (f,) = bad["summary"]["inputs"]["findings"]
    assert f["claim_id"] == "evidence_digest_integrity" and "lifecycle" in f["reason"]


def test_a_recomputed_digest_alone_is_not_established(tmp_path):
    t = _trail("p", "ZONE-01", 20.0, 61200)
    del t["evidence"]["manifest"]["section_sha256"]
    r = _one(tmp_path, t, capture=False)
    assert r["edge"]["counts"] == {"ESTABLISHED": 1}
    assert r["edge"]["counts_on_recorded_inputs"] == {"NOT_ESTABLISHED": 1}


def test_contradicting_observer_and_readback_are_contradicted(tmp_path):
    t = _trail("p", "ZONE-01", 20.0, 61200)
    t["effect_observation"] = _observer("p", "ZONE-01", 19.0, 61200)
    r = _one(tmp_path, t)
    assert r["edge"]["counts_on_recorded_inputs"] == {"CONTRADICTED": 1}
    assert r["summary"]["inputs"]["findings"][0]["claim_id"] == "declared_effect_observed"


def test_a_broken_observer_seal_is_contradicted_and_not_used(tmp_path):
    def tamper(t):
        t["effect_observation"]["sim_time_s"] = 1
    r = _one(tmp_path, _trail("p", "ZONE-01", 20.0, 61200), mutate=tamper)
    assert r["edge"]["counts_on_recorded_inputs"] == {"CONTRADICTED": 1}
    assert r["finding"]["claim_id"] == "observation_after_dispatch"  # the time was not used
    assert "seal" in r["summary"]["inputs"]["findings"][0]["reason"]


def test_capture_digests_are_kept_out_of_the_trail():
    rec = _trail("p", "ZONE-01", 20.0, 61200)
    before = copy.deepcopy(rec)
    cap = demo.capture_digests(rec)
    assert rec == before and "capture" not in rec["ledger_row"]
    assert cap == {"evidence_sha256": canonical_sha256(rec["evidence"]),
                   "effect_observation_sha256": canonical_sha256(rec["effect_observation"])}


def test_unavailable_edge_is_reported_not_counted(tmp_path):
    work = _work(tmp_path)
    broken = tmp_path / "edges" / "broken-edge"
    shutil.copytree(TEMPLATE, broken, ignore=shutil.ignore_patterns("__pycache__"))
    e = json.loads((broken / "edge.json").read_text("utf-8"))
    e["edge_id"] = "broken-edge"
    (broken / "edge.json").write_text(json.dumps(e), "utf-8")
    (broken / "adapter.py").write_text("import sys\nsys.exit(3)\n", "utf-8")
    s = edge_pass.run_pass(work, tmp_path / "out", [TEMPLATE, broken], "t2")
    avail = {x["edge_id"]: x["available"] for x in s["edges"]}
    assert avail == {"template-reference-verifier": True, "broken-edge": False}
    assert next(x for x in s["edges"] if x["edge_id"] == "broken-edge")["exit"] == 3
    assert edge_pass.main(["--work", str(work), "--out", str(tmp_path / "out2"), "--edge", str(broken)]) == 1
    assert edge_pass.main(["--work", str(work), "--out", str(tmp_path / "out3"), "--edge", str(TEMPLATE)]) == 0


def test_every_registered_edge_answers_the_same_on_the_synthetic_trails(tmp_path):
    work = _work(tmp_path)
    s = edge_pass.run_pass(work, tmp_path / "out", edge_pass.offline_effect_edges(), "agree")
    answers = {e["edge_id"]: (e["counts"], {(f["proposal_id"], f["verdict"], f["claim_id"]) for f in e["findings"]})
               for e in s["edges"]}
    assert all(e["available"] for e in s["edges"]) and len(set(map(repr, answers.values()))) == 1, answers


def test_registered_offline_effect_edges_include_the_template_first():
    edges = edge_pass.offline_effect_edges()
    assert edges[0] == TEMPLATE
    for d in edges:
        e = json.loads((d / "edge.json").read_text("utf-8"))
        assert e["mode"] == "offline" and e["boundary"] == "effect_evidence"
