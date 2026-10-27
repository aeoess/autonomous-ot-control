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
