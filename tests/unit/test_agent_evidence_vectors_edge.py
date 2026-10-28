"""Maintainer integration checks for the external, offline v1 edge."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EDGE = ROOT / "federation/edges/agent-evidence-vectors-axes"


def _adapter():
    spec = importlib.util.spec_from_file_location("external_axes_adapter", EDGE / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _positive():
    path = next((ROOT / "federation/fixtures/v1/cases").glob("pos-*.json"))
    return json.loads(path.read_bytes())


@pytest.mark.parametrize("section, field, value, expected_claim, expected", [
    ("twin_operation", "status", "REJECTED", "declared_effect_observed", "CONTRADICTED"),
    ("twin_operation", "new_setpoint_c", 23.0004, "declared_effect_observed", "CONTRADICTED"),
    ("twin_operation", "status", None, "declared_effect_observed", "NOT_ESTABLISHED"),
    ("twin_operation", "new_setpoint_c", None, "declared_effect_observed", "NOT_ESTABLISHED"),
    ("execution", "executions", None, "single_dispatch", "NOT_ESTABLISHED"),
    ("execution", "dispatched_at_sim_s", None, "observation_after_dispatch", "NOT_ESTABLISHED"),
    ("observation", "observed_at_sim_s", None, "observation_after_dispatch", "NOT_ESTABLISHED"),
    # Found by runner.edge_mutations (2026-10-28): this used to raise KeyError and stop the whole run,
    # and the comparison below it read a missing value as a contradicting one.
    ("observation", "observed_setpoint_c", None, "declared_effect_observed", "NOT_ESTABLISHED"),
    ("decision", "decision", "deny", "decision_correspondence", "CONTRADICTED"),
])
def test_missing_or_contradictory_evidence_never_establishes_a_claim(section, field, value, expected_claim, expected):
    adapter, case = _adapter(), _positive()
    if value is None:
        case[section].pop(field)
    else:
        case[section][field] = value
    assert adapter.check(case)[expected_claim][0] == expected


@pytest.mark.parametrize("section, field, claim", [
    ("observation", "observed_setpoint_c", "declared_effect_observed"),
    ("twin_operation", "requested_setpoint_c", "call_binding"),
])
def test_three_decimal_mapping_does_not_hide_a_different_value(section, field, claim):
    case = _positive()
    case[section][field] += 0.0004
    assert _adapter().check(case)[claim][0] == "CONTRADICTED"


def test_v1_reproduction_retains_the_submitted_verdicts_and_reasons():
    adapter = _adapter()
    record = json.loads((ROOT / "federation/records/agent-evidence-vectors-axes/20261027T171310Z.json").read_bytes())
    for row in record["cases"]:
        case = json.loads((ROOT / "federation/fixtures/v1/cases" / (row["case_id"] + ".json")).read_bytes())
        result, reason, _ = adapter.verdict(adapter.check(case), "same_deployment")
        assert (result, reason) == (row["verdict"], row["reason"])


def test_strict_vantage_does_not_promote_same_deployment_observation():
    adapter, case = _adapter(), _positive()
    adapter.STRICT = True
    assert adapter.check(case)["declared_effect_observed"][0] == "NOT_ESTABLISHED"


def test_dependency_bump_cannot_silently_leave_the_executed_checker_unchanged():
    adapter = _adapter()
    manifest = json.loads((EDGE / "edge.json").read_text())
    assert (EDGE / "requirements.txt").read_text().strip() == f"agent-evidence-vectors=={adapter.READER_VERSION}"
    assert manifest["pin"]["value"] == (
        f"agent-evidence-vectors=={adapter.READER_VERSION} (invocation_axes/reader.py, sha256 {adapter.READER_SHA256})")
    assert hashlib.sha256((EDGE / "invocation_axes_reader.py").read_bytes()).hexdigest() == adapter.READER_SHA256
    assert manifest["fixture_sets"] == ["v1"]
    assert manifest["licence"] == "Apache-2.0"
    assert "Copyright 2026 Sankalp Gilda" in (EDGE / "LICENSE.agent-evidence-vectors").read_text()


def test_a_malformed_case_is_not_established_and_never_stops_the_run(tmp_path):
    """One unreadable case must not cost every other case its verdict (found by runner.edge_mutations)."""
    import shutil
    import subprocess
    import sys
    fx = tmp_path / "fx"
    shutil.copytree(ROOT / "federation/fixtures/v1", fx)
    cases = sorted((fx / "cases").glob("pos-*.json"))
    broken = [json.loads(p.read_bytes()) for p in cases[:3]]
    del broken[0]["observation"]["observed_setpoint_c"]          # the reported KeyError
    broken[1]["remora_envelope"] = {}                             # no effect entry to read a vantage from
    broken[2]["claim"]["setpoint_c"] = "20.0"                     # a value the mapping cannot format
    for p, c in zip(cases[:3], broken):
        p.write_text(json.dumps(c), "utf-8")
    out = tmp_path / "r.json"
    r = subprocess.run([sys.executable, str(EDGE / "adapter.py"), "run", "--fixtures", str(fx), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    verdicts = {v["case_id"]: v for v in json.loads(out.read_text("utf-8"))["verdicts"]}
    assert len(verdicts) == len(list((fx / "cases").glob("*.json")))
    missing_value, no_envelope, string_value = (verdicts[c["case_id"]] for c in broken)
    assert missing_value["verdict"] == "NOT_ESTABLISHED" and missing_value["claim_id"] == "declared_effect_observed"
    assert string_value["verdict"] == "NOT_ESTABLISHED" and "malformed case" in string_value["reason"]
    # The envelope is optional in the case contract and only names the vantage: answered, recorded as unrecorded,
    # and not established under strict vantage.
    assert no_envelope["verdict"] == "ESTABLISHED" and "unrecorded" in no_envelope["reason"]
    adapter = _adapter()
    adapter.STRICT = True
    assert adapter.check(broken[1])["declared_effect_observed"][0] == "NOT_ESTABLISHED"
    # Every other case keeps its v1 verdict.
    record = {row["case_id"]: row for row in json.loads(
        (ROOT / "federation/records/agent-evidence-vectors-axes/20261027T171310Z.json").read_bytes())["cases"]}
    for cid, v in verdicts.items():
        if cid not in {c["case_id"] for c in broken}:
            assert (v["verdict"], v["reason"]) == (record[cid]["verdict"], record[cid]["reason"]), cid
