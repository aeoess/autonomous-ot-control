"""The runner contract, the record contract and the invariants federation/index.json cites.

Every key of runner.edge_check.RUNNER_CONTRACT is pinned here for the --run
path; the index's enforced_by entries resolve to real tests; records bind the
sealed bytes and derive their level; RESULTS.md matches the committed records.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from runner import edge_check
from runner.edge_check import RUNNER_CONTRACT, derive_level
from tests.unit.test_federation_kit import make_fixture_set

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_template"
V1 = ROOT / "federation" / "fixtures" / "v1"
RECORD_SCHEMA = json.loads((ROOT / "contracts" / "edge-run-record-v1.schema.json").read_text("utf-8"))
INDEX = json.loads((ROOT / "federation" / "index.json").read_text("utf-8"))


@pytest.fixture()
def fixtures(tmp_path):
    return make_fixture_set(tmp_path / "v-test")


def _edge(tmp_path: Path, adapter_body: str | None = None, **manifest_overrides) -> Path:
    """A copy of the template edge, optionally with a replaced adapter body."""
    dst = tmp_path / "edges" / "probe-edge"
    shutil.copytree(TEMPLATE, dst, ignore=shutil.ignore_patterns("__pycache__"))
    edge = json.loads((dst / "edge.json").read_text("utf-8"))
    edge["edge_id"] = "probe-edge"
    edge.update(manifest_overrides)
    (dst / "edge.json").write_text(json.dumps(edge, indent=2), "utf-8")
    if adapter_body is not None:
        (dst / "adapter.py").write_text(adapter_body, "utf-8")
    return dst


def _run(edge_dir: Path, fixtures: Path, out: Path, *extra: str) -> int:
    return edge_check.main(["--run", str(edge_dir), "--fixtures", str(fixtures), "--out", str(out), *extra])


# -- runner contract -----------------------------------------------------------

def test_every_runner_contract_property_is_pinned_below():
    pinned = {"fresh_working_directory", "verifier_success_exit_zero", "verifier_failure_exit_nonzero",
              "no_report_on_failure", "no_pass_on_failure", "existing_output_refused",
              "dependency_failure_exit_nonzero", "killed_verifier_nonzero_or_unknown",
              "malformed_result_fails_closed"}
    assert set(RUNNER_CONTRACT) == pinned and all(RUNNER_CONTRACT.values())
    assert set(RECORD_SCHEMA["properties"]["runner_contract"]["required"]) == pinned


def test_verifier_success_exit_zero_and_fresh_working_directory(fixtures, tmp_path):
    body = (TEMPLATE / "adapter.py").read_text("utf-8").replace(
        "    root = Path(a.fixtures)",
        "    import os\n    Path(os.getcwd(), 'marker.txt').write_text('x')\n    root = Path(a.fixtures)")
    edge = _edge(tmp_path, body)
    out = tmp_path / "out"
    assert _run(edge, fixtures, out) == 0
    assert (out / "report.md").is_file() and (out / "run-record.json").is_file()
    assert not (out / "work").exists() and not (out / "marker.txt").exists()
    assert not list(ROOT.glob("marker.txt"))


def test_verifier_failure_exit_nonzero_no_report_no_pass(fixtures, tmp_path):
    edge = _edge(tmp_path, "import sys\nsys.exit(3)\n")
    out = tmp_path / "out"
    assert _run(edge, fixtures, out) != 0
    assert not (out / "report.md").exists() and not (out / "run-record.json").exists()
    assert (out / "adapter-stderr.txt").exists()


def test_dependency_failure_exit_nonzero(fixtures, tmp_path):
    edge = _edge(tmp_path, "import a_module_that_does_not_exist_anywhere\n")
    out = tmp_path / "out"
    assert _run(edge, fixtures, out) != 0
    assert not (out / "report.md").exists()


def test_killed_verifier_nonzero_or_unknown(fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(edge_check, "ADAPTER_TIMEOUT_S", 1)
    edge = _edge(tmp_path, "import time\ntime.sleep(30)\n")
    out = tmp_path / "out"
    assert _run(edge, fixtures, out) != 0
    assert not (out / "report.md").exists()


def test_malformed_result_fails_closed(fixtures, tmp_path):
    edge = _edge(tmp_path, "import sys\nfrom pathlib import Path\n"
                           "Path(sys.argv[sys.argv.index('--out') + 1]).write_text('{not json')\n")
    out = tmp_path / "out"
    assert _run(edge, fixtures, out) == 1
    report = (out / "report.md").read_text("utf-8")
    assert "**Result: FAIL**" in report and "not valid JSON" in report
    rec = json.loads((out / "run-record.json").read_text("utf-8"))
    assert rec["status"] == "FAIL" and rec["result"]["claims_established"] == []


def test_existing_output_refused(fixtures, tmp_path):
    edge = _edge(tmp_path)
    out = tmp_path / "out"
    assert _run(edge, fixtures, out) == 0
    with pytest.raises(FileExistsError):
        _run(edge, fixtures, out)
    # The scoring entry point refuses too.
    assert edge_check.main(["--edge", str(edge), "--fixtures", str(fixtures),
                            "--result", str(out / "result.json"), "--out", str(out)]) == 1


def test_run_edge_script_fails_closed_on_adapter_failure(fixtures, tmp_path):
    """The bash wrapper's --local path inherits the contract (needs bash)."""
    if shutil.which("bash") is None:
        pytest.fail("bash is required for this test")
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy", repo / "deploy")
    shutil.copytree(ROOT / "runner", repo / "runner", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "federation" / "edges").mkdir(parents=True)
    shutil.copytree(fixtures, repo / "federation" / "fixtures" / "v1")
    edge = repo / "federation" / "edges" / "broken-edge"
    shutil.copytree(TEMPLATE, edge, ignore=shutil.ignore_patterns("__pycache__"))
    e = json.loads((edge / "edge.json").read_text("utf-8"))
    e["edge_id"] = "broken-edge"
    (edge / "edge.json").write_text(json.dumps(e), "utf-8")
    (edge / "adapter.py").write_text("import sys\nsys.exit(5)\n", "utf-8")
    proc = subprocess.run(["bash", "deploy/scripts/run-edge.sh", "broken-edge", "--local"], cwd=repo,
                          capture_output=True, text=True, env={**__import__("os").environ, "PYTHON": sys.executable})
    assert proc.returncode != 0
    assert not list((repo / "artifacts").rglob("report.md"))


# -- records -------------------------------------------------------------------

def test_record_validates_against_schema_and_binds_the_sealed_set(tmp_path):
    out = tmp_path / "out"
    assert edge_check.main(["--run", str(TEMPLATE), "--fixtures", str(V1), "--out", str(out)]) == 0
    rec = json.loads((out / "run-record.json").read_text("utf-8"))
    jsonschema.validate(rec, RECORD_SCHEMA)
    manifest, sha = edge_check.read_manifest(V1)
    assert rec["fixtures"]["manifest_sha256"] == sha
    assert rec["fixtures"]["package_digest"] == manifest["package_digest"]
    assert rec["fixtures"]["testbed_commit"] == manifest["testbed_commit"]
    assert rec["edge"]["pin"] == {"kind": "git_commit", "value": "in-tree"}
    assert edge_check.validate_record(rec) == []
    assert len(rec["does_not_establish"]) >= 6 + len(edge_check.TESTBED_NON_CLAIMS)


def test_record_ceiling_booleans_are_false(tmp_path):
    out = tmp_path / "out"
    assert edge_check.main(["--run", str(TEMPLATE), "--fixtures", str(V1), "--out", str(out)]) == 0
    rec = json.loads((out / "run-record.json").read_text("utf-8"))
    assert rec["ceiling"] == {"implies_endorsement": False, "implies_production_safety": False,
                              "implies_broader_validity": False, "confers_authority": False}
    rec["ceiling"]["implies_endorsement"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(rec, RECORD_SCHEMA)
    assert edge_check.validate_record(rec)


@pytest.mark.parametrize("evaluator, operator, host, level", [
    ({"kind": "reference", "imports_reference_verifier": False, "imports_testbed_code": False},
     "PRODUCER", "PRODUCER_CONTROLLED", "L0_SELF_TEST"),
    ({"kind": "reference", "imports_reference_verifier": False, "imports_testbed_code": False},
     "EXTERNAL", "EXTERNAL", "L1_REPRODUCTION"),
    ({"kind": "second_implementation", "imports_reference_verifier": True, "imports_testbed_code": False},
     "EXTERNAL", "EXTERNAL", "L1_REPRODUCTION"),
    ({"kind": "second_implementation", "imports_reference_verifier": False, "imports_testbed_code": True},
     "EXTERNAL", "EXTERNAL", "L2_SECOND_IMPLEMENTATION"),
    ({"kind": "second_implementation", "imports_reference_verifier": False, "imports_testbed_code": False},
     "PRODUCER", "PRODUCER_CONTROLLED", "L2_SECOND_IMPLEMENTATION"),
    ({"kind": "second_implementation", "imports_reference_verifier": False, "imports_testbed_code": False},
     "EXTERNAL", "PRODUCER_CONTROLLED", "L3_INDEPENDENT_RECOMPUTATION"),
    ({"kind": "second_implementation", "imports_reference_verifier": False, "imports_testbed_code": False},
     "EXTERNAL", "EXTERNAL", "L4_INDEPENDENT_HOST_RUN"),
])
def test_level_derivation(evaluator, operator, host, level):
    assert derive_level(evaluator, operator, host) == level


def test_record_level_is_derived_not_declared(tmp_path):
    out = tmp_path / "out"
    assert edge_check.main(["--run", str(TEMPLATE), "--fixtures", str(V1), "--out", str(out),
                            "--operator", "EXTERNAL", "--host", "EXTERNAL"]) == 0
    rec = json.loads((out / "run-record.json").read_text("utf-8"))
    assert rec["independence_level"] == "L1_REPRODUCTION"
    rec["independence_level"] = "L4_INDEPENDENT_HOST_RUN"
    assert any("derived" in p for p in edge_check.validate_record(rec))


def test_tampered_fixture_set_is_refused(tmp_path):
    copy = tmp_path / "v1"
    shutil.copytree(V1, copy)
    case = next(copy.glob("cases/pos-*.json"))
    data = json.loads(case.read_text("utf-8"))
    data["claim"]["setpoint_c"] = 99.0
    case.write_text(json.dumps(data), "utf-8")
    probs = edge_check.check_fixture_set(copy)
    assert any("digest mismatch" in p for p in probs)
    out = tmp_path / "out"
    assert edge_check.main(["--run", str(TEMPLATE), "--fixtures", str(copy), "--out", str(out)]) == 1
    assert not (out / "report.md").exists()
    # An unlisted extra case is refused too, and so is an unsealed set.
    shutil.copytree(V1, tmp_path / "v1b")
    (tmp_path / "v1b" / "cases" / "pos-extra.json").write_text("{}", "utf-8")
    assert any("not listed" in p for p in edge_check.check_fixture_set(tmp_path / "v1b"))
    m = json.loads((tmp_path / "v1b" / "manifest.json").read_text("utf-8"))
    del m["files"]
    (tmp_path / "v1b" / "manifest.json").write_text(json.dumps(m), "utf-8")
    assert any("not sealed" in p for p in edge_check.check_fixture_set(tmp_path / "v1b"))


def test_committed_records_validate_and_results_table_matches_the_records():
    recs = edge_check.load_records()
    assert recs, "at least the template's L0 record is committed"
    for path, rec in recs:
        jsonschema.validate(rec, RECORD_SCHEMA)
        assert edge_check.validate_record(rec) == [], path
        assert path.parent.name == rec["edge"]["edge_id"]
    assert edge_check.RESULTS_MD.read_text("utf-8") == edge_check.render_results(recs)


test_results_table_matches_the_records = test_committed_records_validate_and_results_table_matches_the_records


# -- the index and its invariants ---------------------------------------------

def test_index_validates_and_every_entry_point_exists():
    schema = json.loads((ROOT / "contracts" / "federation-index-v1.schema.json").read_text("utf-8"))
    jsonschema.validate(INDEX, schema)
    for name, rel in INDEX["entry_points"].items():
        assert (ROOT / rel).exists(), f"{name}: {rel}"
    assert {b["id"] for b in INDEX["boundaries"]} == set(edge_check.BOUNDARIES)
    assert set(INDEX["independence_levels"]) == set(edge_check.LEVELS)


def test_every_invariant_is_enforced_by_something_that_exists():
    for inv in INDEX["invariants"]:
        for ref in inv["enforced_by"]:
            if "::" in ref:
                file, name = ref.split("::", 1)
                text = (ROOT / file).read_text("utf-8")
                assert re.search(rf"^(def {re.escape(name)}\b|{re.escape(name)} = )", text, re.M), ref
            else:
                assert (ROOT / ref).exists(), ref


def test_the_scorer_has_no_permit_path():
    """An edge can only add refusals: nothing in the scorer or the runner turns a verdict into a permission."""
    for rel in ("runner/edge_check.py", "deploy/scripts/run-edge.sh", "federation/edges/_template/adapter.py"):
        text = (ROOT / rel).read_text("utf-8")
        assert not re.search(r"\bpermit\b|TWIN_ACTUATOR_TOKEN|/internal/v1/actuate|remora\.sdk", text), rel


def test_no_runtime_code_reads_the_registry():
    for rel in ("twin", "adapter", "deploy/ot", "runner"):
        for p in (ROOT / rel).rglob("*.py"):
            assert "edge-registry" not in p.read_text("utf-8"), p
