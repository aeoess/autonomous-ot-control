"""Usage accounting for offline edges (runner.edge_pass usage.json, runner.edge_usage).

- every pass writes usage.json beside edges.json: per edge one verifier run over
  the whole case set, tied to the edge's version digest, the input digest and
  the run id, with runtime, availability, cases processed and findings;
- a second edge is compared with the reference verifier only as agreement or
  as candidates for review; confirmed, unique and false findings stay null
  (live trails have no ground truth);
- aggregated across passes, re-running the same input on the same edge
  version adds verifier runs and processed cases but no new distinct cases or
  findings; a failed run lowers availability and processes nothing.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from runner import edge_pass, edge_usage

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_edge_pass as T  # noqa: E402  (the synthetic demo export shared with the edge pass tests)


def _second_edge(tmp_path: Path, *, broken: bool = False) -> Path:
    d = tmp_path / "edges" / ("broken-edge" if broken else "second-edge")
    shutil.copytree(T.TEMPLATE, d, ignore=shutil.ignore_patterns("__pycache__"))
    e = json.loads((d / "edge.json").read_text("utf-8"))
    e.update(edge_id=d.name, maintainer="someone")
    (d / "edge.json").write_text(json.dumps(e), "utf-8")
    if broken:
        (d / "adapter.py").write_text("import sys\nsys.exit(3)\n", "utf-8")
    return d


def test_each_pass_writes_usage_tied_to_version_input_and_run(tmp_path):
    work = T._work(tmp_path)
    second = _second_edge(tmp_path)
    s = edge_pass.run_pass(work, tmp_path / "out", [T.TEMPLATE, second], "run-1")
    u = json.loads((tmp_path / "out" / "usage.json").read_text("utf-8"))
    assert s["usage"] == "usage.json" and u["schema"] == "ot-edge-usage/v1" and u["run_id"] == "run-1"
    assert u["input_digest"] == s["case_set_manifest_sha256"] and u["cases"] == 4
    ref, other = u["edges"]
    assert ref["edge_id"] == "template-reference-verifier" and len(ref["edge_digest"]) == 64
    assert ref["verifier_runs"] == 1 and ref["run_ok"] and ref["runtime_ms"] >= 0
    assert ref["cases_sent"] == ref["cases_processed"] == 4 and len(ref["answered_case_digests"]) == 4
    assert ref["not_established"] == 1 and len(ref["findings"]) == 2
    assert other["edge_digest"] != ref["edge_digest"]
    assert other["vs_reference"] == {"compared": 4, "agree": 4, "edge_only_findings": [], "reference_only_findings": []}
    for r in (ref, other):
        assert r["findings_confirmed"] is None and r["unique_findings"] is None and r["false_findings"] is None


def test_repeats_add_use_but_not_new_findings_and_failures_lower_availability(tmp_path):
    work = T._work(tmp_path)
    broken = _second_edge(tmp_path, broken=True)
    for i in (1, 2):
        edge_pass.run_pass(work, tmp_path / f"pass-{i}" / "edges", [T.TEMPLATE, broken], "same-run")
    s = edge_usage.aggregate(edge_usage.load([tmp_path]))
    by = {r["edge_id"]: r for r in s["edges"]}
    ref = by["template-reference-verifier"]
    assert s["usage_files"] == 2 and ref["passes"] == 1 and len(ref["versions"]) == 1
    assert ref["verifier_runs"] == 2 and ref["cases_processed"] == 8
    assert ref["distinct_cases"] == 4 and ref["distinct_findings"] == 2 and ref["availability"] == 1.0
    b = by["broken-edge"]
    assert b["verifier_runs"] == 2 and b["runs_failed"] == 2 and b["availability"] == 0.0
    assert b["cases_processed"] == 0 and b["distinct_cases"] == 0
    assert {c["maintainer"] for c in s["by_contributor"]} >= {"someone"}
    assert "Use is not value" in edge_usage.render_md(s)
    assert edge_usage.main(["--root", str(tmp_path), "--out", str(tmp_path / "sum")]) == 0
    assert json.loads((tmp_path / "sum" / "usage-summary.json").read_text("utf-8"))["schema"] == "ot-edge-usage-summary/v1"
    assert edge_usage.main(["--root", str(tmp_path / "nothing-here")]) == 1
