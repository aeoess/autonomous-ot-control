"""The federation onboarding kit works end to end and cannot pass a lazy edge.

On synthetic trails shaped like the demo's output:
- the template adapter must establish every positive case and contradict
  every seeded fault;
- an adapter that answers ESTABLISHED to everything must FAIL the scoring;
- an empty or mismatched result must fail;
- the template manifest must carry what the onboarding guide requires.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from runner import edge_check
from runner.federation_fixtures import FAULTS, positive, seed_fault

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_template"


def _rec(pid: str, zone: str, value: float, t: int) -> tuple[dict, dict]:
    rec = {"proposal_id": pid,
           "ledger_row": {"applied": True, "zone_id": zone, "to_c": value, "sim_time_s": t,
                          "decision": "verify", "reasons": ["authority_resolved_review"],
                          "reviewer": {"model": "careful", "approved": True, "reason": "plan step"}},
           "envelope": {"decision": "verify"}, "evidence": {"sections": {"assessed": {"pid": pid}}}}
    op = {"operation_id": pid, "status": "APPLIED", "zone_id": zone, "new_setpoint_c": value,
          "sim_time_s": t}
    return rec, op


@pytest.fixture()
def fixtures(tmp_path):
    pos = [positive(*_rec("p-1", "ZONE-01", 20.0, 61200)), positive(*_rec("p-2", "ZONE-02", 19.0, 62100)),
           positive(*_rec("p-3", "ZONE-01", 21.0, 18000))]
    neg = [seed_fault(pos[i % 3], f, pos[(i + 1) % 3]) for i, f in enumerate(FAULTS)]
    root = tmp_path / "v-test"
    (root / "cases").mkdir(parents=True)
    entries = []
    for c in pos + neg:
        (root / "cases" / f"{c['case_id']}.json").write_text(json.dumps(c), "utf-8")
        entries.append({"case_id": c["case_id"], "kind": c["kind"], "fault": c["fault"]})
    (root / "manifest.json").write_text(json.dumps({"fixture_set": "v-test", "cases": entries}), "utf-8")
    return root


def _template_adapter():
    spec = importlib.util.spec_from_file_location("template_adapter", TEMPLATE / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _score(fixtures: Path, result: dict) -> dict:
    raw = (fixtures / "manifest.json").read_bytes()
    return edge_check.score(json.loads(raw), hashlib.sha256(raw).hexdigest(), result)


def test_the_template_establishes_positives_and_catches_every_fault(fixtures, tmp_path):
    out = tmp_path / "result.json"
    assert _template_adapter().main(["run", "--fixtures", str(fixtures), "--out", str(out)]) == 0
    s = _score(fixtures, json.loads(out.read_text("utf-8")))
    assert s["ok"], s["problems"]
    assert s["positives_established"] == s["positives"] == 3
    assert s["negatives_caught"] == s["negatives"] == len(FAULTS)
    assert all(v == 1 for v in s["by_fault"].values())


def test_a_lazy_edge_that_says_established_to_everything_fails(fixtures):
    raw = (fixtures / "manifest.json").read_bytes()
    cases = json.loads(raw)["cases"]
    lazy = {"schema": "ot-edge-result/v1", "edge_id": "lazy",
            "fixtures_sha256": hashlib.sha256(raw).hexdigest(),
            "verdicts": [{"case_id": c["case_id"], "verdict": "ESTABLISHED", "reason": "ok"} for c in cases]}
    s = _score(fixtures, lazy)
    assert not s["ok"] and s["negatives_passed"] == len(FAULTS)


@pytest.mark.parametrize("broken", ["empty", "wrong_set", "missing_case"])
def test_incomplete_or_mismatched_results_fail(fixtures, tmp_path, broken):
    out = tmp_path / "r.json"
    _template_adapter().main(["run", "--fixtures", str(fixtures), "--out", str(out)])
    result = json.loads(out.read_text("utf-8"))
    if broken == "empty":
        result["verdicts"] = []
    elif broken == "wrong_set":
        result["fixtures_sha256"] = "0" * 64
    else:
        result["verdicts"] = result["verdicts"][1:]
    assert not _score(fixtures, result)["ok"]


def test_the_template_manifest_has_what_the_guide_requires():
    schema = json.loads((ROOT / "contracts" / "edge-manifest-v1.schema.json").read_text("utf-8"))
    edge = json.loads((TEMPLATE / "edge.json").read_text("utf-8"))
    assert set(schema["required"]) <= set(edge)
    assert edge["mode"] in schema["properties"]["mode"]["enum"]
    assert edge["boundary"] in schema["properties"]["boundary"]["enum"]
    guide = (ROOT / "docs" / "federation" / "README.md").read_text("utf-8")
    for must in ("run-edge.sh", "consent_ref", "claim ceiling", "negative control", "--init"):
        assert must in guide


def test_edge_scaffolder_and_validator(tmp_path):
    (tmp_path / "federation" / "edges").mkdir(parents=True)
    import shutil
    shutil.copytree(TEMPLATE, tmp_path / "federation" / "edges" / "_template")
    dst = edge_check.init_edge("demo-edge-01", project="Demo Edge", maintainer="alice", root=tmp_path)
    assert edge_check.validate_edge_manifest(dst) == []
    assert edge_check.main(["--validate", str(dst)]) == 0
    # Running the scaffolded adapter against the frozen v1 fixtures must pass 100%.
    v1 = ROOT / "federation" / "fixtures" / "v1"
    out = tmp_path / "res.json"
    spec = importlib.util.spec_from_file_location("demo_adapter", dst / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["run", "--fixtures", str(v1), "--out", str(out)]) == 0
    assert edge_check.main(["--edge", str(dst), "--fixtures", str(v1),
                            "--result", str(out), "--out", str(tmp_path / "rep")]) == 0

