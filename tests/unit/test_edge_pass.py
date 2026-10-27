"""runner.edge_pass: offline edges beside a governed run (ADR-004).

On a synthetic work directory shaped like the demo's `decisions/` export:
- every applied setpoint trail becomes a case; refused proposals and other
  tools are counted and left out;
- the reference edge establishes a consistent trail, contradicts one whose
  twin record disagrees with the ledger, and cannot establish one with no
  exported twin record;
- an edge that cannot run is reported unavailable, and the pass still
  completes on the edges that could;
- the pass fails only when no edge ran at all.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from runner import edge_pass

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_template"


def _trail(pid: str, zone: str, value: float, t: int, *, applied: bool = True, twin_value: float | None = None,
           export_op: bool = True, tool: str | None = None) -> dict:
    row = {"applied": applied, "zone_id": zone, "to_c": value, "sim_time_s": t, "decision": "verify",
           "reasons": ["authority_resolved_review"],
           "reviewer": {"model": "careful", "approved": True, "reason": "plan step"}}
    if tool:
        row["tool_name"] = tool
    op = {"operation_id": pid, "status": "APPLIED", "zone_id": zone,
          "new_setpoint_c": value if twin_value is None else twin_value, "sim_time_s": t}
    return {"proposal_id": pid, "ledger_row": row, "envelope": {"decision": "verify"},
            "evidence": {"sections": {"assessed": {"pid": pid}}},
            "twin_operation": op if (applied and export_op) else None}


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "demo-work"
    (work / "decisions").mkdir(parents=True)
    trails = [_trail("p-ok-1", "ZONE-01", 20.0, 61200), _trail("p-ok-2", "ZONE-02", 19.5, 62100),
              _trail("p-bad", "ZONE-01", 21.0, 63000, twin_value=20.0),
              _trail("p-noop", "ZONE-03", 20.5, 64000, export_op=False),
              _trail("p-refused", "ZONE-01", 24.0, 65000, applied=False),
              _trail("p-tank", "TANK-01", 47.0, 66000, tool="set_tank_setpoint")]
    for t in trails:
        (work / "decisions" / f"{t['proposal_id']}.json").write_text(json.dumps(t), "utf-8")
    return work


def test_pass_classifies_trails_and_reports_findings(tmp_path):
    work = _work(tmp_path)
    s = edge_pass.run_pass(work, tmp_path / "out", [TEMPLATE], "t1")
    assert s["cases"] == 4 and s["skipped"] == {"not_applied": 1, "other_tool": 1, "no_twin_operation_exported": 1}
    (e,) = s["edges"]
    assert e["available"] and e["fixtures_sha256_matches"] and e["unanswered"] == []
    assert e["counts"] == {"ESTABLISHED": 2, "CONTRADICTED": 1, "NOT_ESTABLISHED": 1}
    by = {f["proposal_id"]: f for f in e["findings"]}
    assert by["p-bad"]["verdict"] == "CONTRADICTED" and by["p-bad"]["claim_id"] == "declared_effect_observed"
    assert by["p-noop"]["verdict"] == "NOT_ESTABLISHED" and by["p-noop"]["claim_id"] == "call_binding"
    md = (tmp_path / "out" / "edges.md").read_text("utf-8")
    assert "## Edges beside the run" in md and "`p-bad`" in md and "never change the run's outcome" in md
    assert json.loads((tmp_path / "out" / "edges.json").read_text("utf-8"))["schema"] == "ot-edge-pass/v1"


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


def test_registered_offline_effect_edges_include_the_template_first():
    edges = edge_pass.offline_effect_edges()
    assert edges[0] == TEMPLATE
    for d in edges:
        e = json.loads((d / "edge.json").read_text("utf-8"))
        assert e["mode"] == "offline" and e["boundary"] == "effect_evidence"
