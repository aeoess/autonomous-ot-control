"""The federation onboarding kit works end to end and cannot pass a lazy edge.

On synthetic trails shaped like the demo's output, sealed exactly as the
builder seals a real set:
- the template adapter must establish every positive case and contradict
  every seeded fault, each under the claim the manifest names;
- an adapter that answers ESTABLISHED to everything must FAIL the scoring;
- an empty or mismatched result must fail;
- the template manifest must carry what the onboarding guide requires;
- the sealed v1 set in the repository verifies, and the scaffolded template
  passes on it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from runner import edge_check
from runner.federation_fixtures import CLAIMS, FAULT_CLAIM, FAULTS, positive, seal_manifest, seed_fault

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_template"
V1 = ROOT / "federation" / "fixtures" / "v1"


def _rec(pid: str, zone: str, value: float, t: int) -> tuple[dict, dict]:
    rec = {"proposal_id": pid,
           "ledger_row": {"applied": True, "zone_id": zone, "to_c": value, "sim_time_s": t,
                          "decision": "verify", "reasons": ["authority_resolved_review"],
                          "reviewer": {"model": "careful", "approved": True, "reason": "plan step"}},
           "envelope": {"decision": "verify"}, "evidence": {"sections": {"assessed": {"pid": pid}}}}
    op = {"operation_id": pid, "status": "APPLIED", "zone_id": zone, "new_setpoint_c": value,
          "sim_time_s": t}
    return rec, op


def make_fixture_set(root: Path) -> Path:
    pos = [positive(*_rec("p-1", "ZONE-01", 20.0, 61200)), positive(*_rec("p-2", "ZONE-02", 19.0, 62100)),
           positive(*_rec("p-3", "ZONE-01", 21.0, 18000))]
    neg = [seed_fault(pos[i % 3], f, pos[(i + 1) % 3]) for i, f in enumerate(FAULTS)]
    (root / "cases").mkdir(parents=True)
    for c in pos + neg:
        (root / "cases" / f"{c['case_id']}.json").write_text(json.dumps(c), "utf-8")
    (root / "manifest.json").write_text(json.dumps({"fixture_set": root.name, "testbed_commit": "0" * 40,
                                                     "scope": "synthetic test set"}), "utf-8")
    seal_manifest(root)
    return root


@pytest.fixture()
def fixtures(tmp_path):
    return make_fixture_set(tmp_path / "v-test")


def _template_adapter():
    spec = importlib.util.spec_from_file_location("template_adapter", TEMPLATE / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _score(fixtures: Path, result: dict) -> dict:
    raw = (fixtures / "manifest.json").read_bytes()
    return edge_check.score(json.loads(raw), hashlib.sha256(raw).hexdigest(), result)


def test_the_template_establishes_positives_and_catches_every_fault_per_claim(fixtures, tmp_path):
    out = tmp_path / "result.json"
    assert _template_adapter().main(["run", "--fixtures", str(fixtures), "--out", str(out)]) == 0
    result = json.loads(out.read_text("utf-8"))
    s = _score(fixtures, result)
    assert s["ok"], s["problems"]
    assert s["positives_established"] == s["positives"] == 3
    assert s["negatives_caught"] == s["negatives"] == len(FAULTS)
    assert all(v == 1 for v in s["by_fault"].values())
    assert set(s["claims_established"]) == set(CLAIMS)
    # The adapter names the same claim the manifest names for each seeded fault.
    by_case = {v["case_id"]: v for v in result["verdicts"]}
    for c in s["cases"]:
        if c["kind"] == "negative":
            assert by_case[c["case_id"]]["claim_id"] == c["claim_id"] == FAULT_CLAIM[c["fault"]]


def test_a_lazy_edge_that_says_established_to_everything_fails(fixtures):
    raw = (fixtures / "manifest.json").read_bytes()
    cases = json.loads(raw)["cases"]
    lazy = {"schema": "ot-edge-result/v1", "edge_id": "lazy",
            "fixtures_sha256": hashlib.sha256(raw).hexdigest(),
            "verdicts": [{"case_id": c["case_id"], "verdict": "ESTABLISHED", "reason": "ok"} for c in cases]}
    s = _score(fixtures, lazy)
    assert not s["ok"] and s["negatives_passed"] == len(FAULTS) and s["claims_established"] == []


@pytest.mark.parametrize("broken", ["empty", "wrong_set", "missing_case", "not_an_object"])
def test_incomplete_or_mismatched_results_fail(fixtures, tmp_path, broken):
    out = tmp_path / "r.json"
    _template_adapter().main(["run", "--fixtures", str(fixtures), "--out", str(out)])
    result = json.loads(out.read_text("utf-8"))
    if broken == "empty":
        result["verdicts"] = []
    elif broken == "wrong_set":
        result["fixtures_sha256"] = "0" * 64
    elif broken == "missing_case":
        result["verdicts"] = result["verdicts"][1:]
    else:
        result = ["not", "an", "object"]
    assert not _score(fixtures, result)["ok"]


def test_the_template_manifest_has_what_the_guide_requires():
    import jsonschema
    schema = json.loads((ROOT / "contracts" / "edge-manifest-v1.schema.json").read_text("utf-8"))
    edge = json.loads((TEMPLATE / "edge.json").read_text("utf-8"))
    jsonschema.validate(edge, schema)
    assert edge_check.validate_edge_manifest(TEMPLATE) == []
    assert {c["claim_id"] for c in edge["claims"]} == set(CLAIMS)
    assert edge["evaluator"]["kind"] == "reference"
    guide = (ROOT / "docs" / "federation" / "README.md").read_text("utf-8")
    for must in ("run-edge.sh", "consent_ref", "claim ceiling", "negative control", "--init",
                 "L4_INDEPENDENT_HOST_RUN", "federation/records/", "package_digest", "--results"):
        assert must in guide, must


def test_the_committed_v1_set_is_sealed_and_valid():
    import jsonschema
    assert edge_check.check_fixture_set(V1) == []
    manifest, _ = edge_check.read_manifest(V1)
    schema = json.loads((ROOT / "contracts" / "federation-fixtures-v1.schema.json").read_text("utf-8"))
    jsonschema.validate(manifest, schema)
    assert {c["claim_id"] for c in manifest["claims"]} == set(CLAIMS)
    negs = [c for c in manifest["cases"] if c["kind"] == "negative"]
    assert len(negs) == len(FAULTS) and {c["fault"] for c in negs} == set(FAULTS)
    assert all(c["claim_id"] == FAULT_CLAIM[c["fault"]] for c in negs)
    assert sum(1 for c in manifest["cases"] if c["kind"] == "positive") == 12


def test_edge_scaffolder_and_validator(tmp_path):
    (tmp_path / "federation" / "edges").mkdir(parents=True)
    import shutil
    shutil.copytree(TEMPLATE, tmp_path / "federation" / "edges" / "_template",
                    ignore=shutil.ignore_patterns("__pycache__"))
    dst = edge_check.init_edge("demo-edge-01", project="Demo Edge", maintainer="alice", root=tmp_path)
    assert edge_check.validate_edge_manifest(dst) == []
    assert edge_check.main(["--validate", str(dst)]) == 0
    edge = json.loads((dst / "edge.json").read_text("utf-8"))
    assert edge["edge_id"] == "demo-edge-01" and "<commit" in edge["pin"]["value"]
    # Running the scaffolded adapter against the sealed v1 fixtures must pass 100%.
    out = tmp_path / "run"
    assert edge_check.main(["--run", str(dst), "--fixtures", str(V1), "--out", str(out),
                            "--operator", "EXTERNAL", "--host", "EXTERNAL"]) == 0
    rec = json.loads((out / "run-record.json").read_text("utf-8"))
    assert rec["status"] == "PASS" and rec["result"]["negatives_caught"] == 6
    # Still our reference verifier, so an external run of it is a reproduction, not independent.
    assert rec["independence_level"] == "L1_REPRODUCTION"
