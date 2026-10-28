"""Regression cases for the offline #177 mutation crosswalk (not live authority)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from runner import edge_mutations as mutations

ROOT = Path(__file__).resolve().parents[2]
POSITIVES = sorted((ROOT / "federation/fixtures/v2/cases").glob("pos-*.json"))
EDGES = ["_template", "agent-evidence-vectors-axes"]


@pytest.fixture(params=EDGES)
def adapter(request):
    path = ROOT / "federation/edges" / request.param / "adapter.py"
    spec = importlib.util.spec_from_file_location("gap_" + request.param, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(adapter, case):
    checked = adapter.check(case)
    return adapter.verdict(checked, "same_deployment") if isinstance(checked, dict) else checked


def change(case, path, value):
    obj = case
    for key in path[:-1]:
        obj = obj.setdefault(key, {})
    obj[path[-1]] = value


@pytest.mark.parametrize("decision", ["verify", "escalate"])
def test_reviewer_refusal_cannot_be_overridden_by_outer_approval(adapter, decision):
    for path in POSITIVES:
        case = json.loads(path.read_bytes())
        case["decision"].update(decision=decision, approved=True, reviewer={"approved": False})
        assert result(adapter, case)[::2] == ("CONTRADICTED", "decision_correspondence")


@pytest.mark.parametrize("path", [
    ("remora_envelope", "request_id"),
    ("remora_envelope", "request", "request_id"),
    ("remora_evidence", "proposal_id"),
    ("remora_evidence", "manifest", "proposal_id"),
    ("remora_evidence", "proposal", "proposal_id"),
])
def test_every_recorded_proposal_binding_must_match_even_with_a_fresh_digest(adapter, path):
    for seed in POSITIVES:
        case = json.loads(seed.read_bytes())
        change(case, path, "other-proposal")
        case["evidence_sha256"] = mutations.canonical_sha256(case["remora_evidence"])
        assert result(adapter, case)[::2] == ("CONTRADICTED", "call_binding")


def test_accept_without_reviewer_and_optional_envelope_remains_supported(adapter):
    case = json.loads(POSITIVES[0].read_bytes())
    case["decision"].update(decision="accept", approved=False, reviewer=None)
    case.pop("remora_envelope")
    assert result(adapter, case)[0] == "ESTABLISHED"


@pytest.mark.parametrize("reviewer", [None, {}, {"approved": 1}, {"approved": "true"}])
def test_review_needs_an_explicit_boolean_approval(adapter, reviewer):
    case = json.loads(POSITIVES[0].read_bytes())
    case["decision"].update(decision="verify", approved=True, reviewer=reviewer)
    assert result(adapter, case)[::2] == ("NOT_ESTABLISHED", "decision_correspondence")


def test_a_deny_with_an_outer_approval_flag_is_still_a_deny(adapter):
    case = json.loads(POSITIVES[0].read_bytes())
    case["decision"].update(decision="deny", approved=True)
    assert result(adapter, case)[::2] == ("CONTRADICTED", "decision_correspondence")


def test_missing_reviewer_does_not_erase_an_explicit_dispatch_contradiction(adapter):
    case = json.loads(POSITIVES[0].read_bytes())
    case["decision"].update(decision="verify", approved=True, reviewer=None)
    case["execution"]["executed"] = False
    assert result(adapter, case)[::2] == ("CONTRADICTED", "decision_correspondence")


@pytest.mark.parametrize("path,value", [
    (("unexpected",), 1),
    (("execution", "executions"), True),
    (("execution", "executed"), 1),
    (("execution", "dispatched_at_sim_s"), True),
    (("observation", "observed_at_sim_s"), False),
    (("observation", "observed_setpoint_c"), "19.0"),
    (("claim", "setpoint_c"), True),
    (("claim", "setpoint_c"), float("nan")),
    (("claim", "setpoint_c"), 10**400),
    (("twin_operation", "new_setpoint_c"), float("nan")),
    (("remora_evidence", "nonfinite"), float("inf")),
    (("decision", "approved"), 1),
    (("decision", "reviewer"), []),
    (("claim",), []),
    (("schema",), "unknown"),
])
def test_malformed_cases_cannot_establish_a_claim(adapter, path, value):
    case = json.loads(POSITIVES[0].read_bytes())
    change(case, path, value)
    case["evidence_sha256"] = mutations.canonical_sha256(case["remora_evidence"])
    assert result(adapter, case)[0] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("edge", EDGES)
def test_wire_errors_are_isolated_from_valid_cases(edge, tmp_path):
    fx = tmp_path / "fx"
    (fx / "cases").mkdir(parents=True)
    (fx / "manifest.json").write_text("{}")
    case = json.loads(POSITIVES[0].read_bytes())
    (fx / "cases/valid.json").write_text(json.dumps(case))
    bad = {"syntax": "{", "root": "[]", "duplicate": json.dumps(case)[:-1] + ',"execution":{}}',
           "nonfinite": json.dumps(case)[:-1] + ',"extra":NaN}'}
    oversized = json.loads(json.dumps(case))
    oversized["case_id"] = "overflow"
    oversized["claim"]["setpoint_c"] = 10**400
    oversized["twin_operation"].update(requested_setpoint_c=10**400, new_setpoint_c=10**400)
    oversized["observation"]["observed_setpoint_c"] = 10**400
    bad["overflow"] = json.dumps(oversized)
    for name, raw in bad.items():
        (fx / "cases" / (name + ".json")).write_text(raw)
    out = tmp_path / "result.json"
    run = subprocess.run([sys.executable, str(ROOT / "federation/edges" / edge / "adapter.py"),
                          "run", "--fixtures", str(fx), "--out", str(out)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    answers = {v["case_id"]: v["verdict"] for v in json.loads(out.read_text())["verdicts"]}
    assert answers.pop(case["case_id"]) == "ESTABLISHED"
    assert answers == {name: "NOT_ESTABLISHED" for name in bad}


def test_key_order_control_survives_serialization_and_kills_an_unsorted_hasher(tmp_path):
    seed = json.loads(POSITIVES[0].read_bytes())
    cases = mutations.case_mutants([seed])
    mutations.write_set(cases, "test", tmp_path / "set", "sealed positives")
    row = next(m for m in cases if m["operator"] == "pc_evidence_key_order")
    path = tmp_path / "set/cases" / (row["case"]["case_id"] + ".json")
    case = json.loads(path.read_text())
    raw = json.dumps(case["remora_evidence"], sort_keys=False, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(raw.encode()).hexdigest() != case["evidence_sha256"]


def test_mutation_set_exercises_the_four_missing_variants_and_malformed_admission():
    seed = json.loads(POSITIVES[0].read_bytes())
    cases = {m["operator"]: m for m in mutations.case_mutants([seed])}
    expected = {"dc_reviewer_refused", "cb_envelope_request", "cb_evidence_proposal", "de_twin_value_only",
                "malformed_extra_field", "malformed_boolean_count"}
    assert expected <= cases.keys()
    twin = cases["de_twin_value_only"]["case"]
    assert twin["observation"] == seed["observation"] and twin["claim"] == seed["claim"]
    assert twin["twin_operation"]["new_setpoint_c"] != seed["twin_operation"]["new_setpoint_c"]


def test_both_builtins_answer_every_serialized_development_mutant(adapter, tmp_path):
    seeds = [json.loads(p.read_bytes()) for p in POSITIVES]
    mutants = mutations.case_mutants(seeds)
    fx = tmp_path / "set"
    mutations.write_set(mutants, "development-only", fx, "v2 positive controls")
    out = tmp_path / "result.json"
    assert adapter.main(["run", "--fixtures", str(fx), "--out", str(out)]) == 0
    expected = json.loads((fx / "expected.json").read_text())
    answers = {v["case_id"]: v for v in json.loads(out.read_text())["verdicts"]}
    score = mutations.score(expected, answers)
    assert score["wrong"] == [] and score["unanswered"] == 0
    assert score["kill_rate"] == 1.0 and score["false_refusals"] == 0
