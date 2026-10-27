"""Finding 3 (after PR #20): the reference verifier accepted missing and contradictory evidence.

Four schema-valid mutations of a positive case came back ESTABLISHED:
a missing dispatch time (the time check was skipped), a missing observation
time (defaulted to the dispatch time), a missing execution count (defaulted
to 1), and decision=deny / approved=false / executed=false while the twin's
own record still says APPLIED (the authorization check only ran when
executed was true).

Missing evidence a claim needs is NOT_ESTABLISHED. Evidence that disagrees
with itself is CONTRADICTED. Neither is ever ESTABLISHED.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

from runner.federation_fixtures import EXPECTED_VERDICT, FAULTS, FAULTS_V1, seed_fault

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_template" / "adapter.py"
V1 = ROOT / "federation" / "fixtures" / "v1"
CASE_SCHEMA = json.loads((ROOT / "contracts" / "federation-case-v1.schema.json").read_text("utf-8"))


def _adapter():
    spec = importlib.util.spec_from_file_location("template_adapter_evidence", TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check():
    return _adapter().check


@pytest.fixture
def positive() -> dict:
    path = sorted(V1.glob("cases/pos-*.json"))[0]
    return json.loads(path.read_text("utf-8"))


def verdict(check, case: dict) -> str:
    jsonschema.validate(case, CASE_SCHEMA)  # every mutation here is schema-valid
    return check(case)[0]


def test_unchanged_positive_is_established(check, positive):
    assert verdict(check, positive) == "ESTABLISHED"


@pytest.mark.parametrize("remove", [
    ("execution", "dispatched_at_sim_s"),
    ("observation", "observed_at_sim_s"),
    ("execution", "executions"),
])
def test_missing_required_evidence_is_not_established(check, positive, remove):
    section, field = remove
    case = copy.deepcopy(positive)
    del case[section][field]
    assert verdict(check, case) == "NOT_ESTABLISHED"


def test_deny_and_not_executed_with_an_applied_record_is_contradicted(check, positive):
    case = copy.deepcopy(positive)
    case["decision"]["decision"], case["decision"]["approved"] = "deny", False
    case["execution"]["executed"] = False
    assert case["twin_operation"]["status"] == "APPLIED"
    assert verdict(check, case) == "CONTRADICTED"


def test_not_executed_with_an_observation_and_applied_record_is_contradicted(check, positive):
    """executed=false but the trail still carries an APPLIED record: the evidence disagrees with itself."""
    case = copy.deepcopy(positive)
    case["execution"]["executed"] = False
    assert verdict(check, case) == "CONTRADICTED"


@pytest.mark.parametrize("fault", FAULTS_V1)
def test_every_v1_sealed_fault_is_still_caught(check, positive, fault):
    other = json.loads(sorted(V1.glob("cases/pos-*.json"))[1].read_text("utf-8"))
    assert verdict(check, seed_fault(positive, fault, other)) == "CONTRADICTED"


@pytest.mark.parametrize("fault", FAULTS)
def test_every_seeded_fault_gets_its_expected_verdict(check, positive, fault):
    other = json.loads(sorted(V1.glob("cases/pos-*.json"))[1].read_text("utf-8"))
    assert verdict(check, seed_fault(positive, fault, other)) == EXPECTED_VERDICT[fault]
