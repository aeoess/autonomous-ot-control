"""authority_fixture: the five cases of issue #27, the stand-in verifier and the offline harness.

- the committed set is sealed and is exactly what a rebuild gives (Ed25519 is deterministic);
- every case is built as it says: the stand-in verifier's refusal reason is the case's delegation
  fault, and each work order binds that case's delegation by its JCS SHA-256;
- the harness runs both checks on the same input and passes with the stand-in verifier: delegation
  check, work-order check and oracle all as the manifest fixed them, one case admitted;
- a verifier that passes everything is caught on cases 2 to 4; a crashing verifier, a malformed
  answer and a missing answer are their own classes, never refusals; a changed fixture fails the set.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from runner import authority_fixtures as af
from runner import authority_harness as ah
from runner import work_order as wo

ROOT = Path(__file__).resolve().parents[2]
SET = ROOT / "federation" / "authority" / "fixtures" / "t1"
VERIFIER = ROOT / "federation" / "authority" / "template-verifier"


def _verifier(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / name
    shutil.copytree(VERIFIER, d, ignore=shutil.ignore_patterns("__pycache__"))
    (d / "adapter.py").write_text(
        "import json, sys\nfrom pathlib import Path\na = sys.argv\n"
        "fx, out = Path(a[a.index('--fixtures') + 1]), Path(a[a.index('--out') + 1])\n"
        "cases = [json.loads(p.read_text('utf-8')) for p in sorted((fx / 'cases').glob('*.json'))]\n" + body, "utf-8")
    return d


def test_committed_set_is_sealed_and_rebuilds_byte_for_byte(tmp_path):
    assert af.check(SET) == []
    m = af.build(tmp_path / "t1")
    for f in m["files"] + [{"path": "manifest.json"}]:
        assert (tmp_path / "t1" / f["path"]).read_bytes() == (SET / f["path"]).read_bytes(), f["path"]


def test_cases_are_built_as_they_say():
    m = json.loads((SET / "manifest.json").read_text("utf-8"))
    assert [c["case_id"] for c in m["cases"]] == [c[0] for c in af.CASES]
    roots, keys = m["delegation_root_keys"], m["delegation_signer_keys"]
    import importlib.util
    spec = importlib.util.spec_from_file_location("authority_template_adapter", VERIFIER / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for entry in m["cases"]:
        case = json.loads((SET / "cases" / f"{entry['case_id']}.json").read_text("utf-8"))
        verdict, reason = mod.decide(case, roots, keys)
        assert verdict == entry["expected"]["aps"]
        if entry["delegation_fault"]:
            assert reason == entry["delegation_fault"], (entry["case_id"], reason)
        assert case["work_order"]["delegation_sha256"] == wo.delegation_sha256(case["delegation"])
        assert wo.verify_signature(case["work_order"], m["work_order_issuer_keys"]) is None


def test_harness_passes_with_the_stand_in_and_records_both_verdicts(tmp_path):
    r = ah.run(SET, VERIFIER, tmp_path / "out")
    assert r["pass"] and r["set_problems"] == []
    c = r["counts"]
    assert c["aps_as_expected"] == c["work_order_as_expected"] == c["oracle_agrees"] == 5 and c["admitted"] == 1
    by = {x["case_id"]: x for x in r["cases"]}
    assert by["af-5-value-out-of-range"]["aps"]["verdict"] == "pass"
    assert by["af-5-value-out-of-range"]["work_order"]["reasons"] == ["value_above_range"]
    assert by["af-5-value-out-of-range"]["oracle"]["rule"] == "outside_work_order"
    assert all(by[k]["oracle"]["rule"] == "delegation_invalid" for k in ("af-2-widened", "af-3-expired", "af-4-out-of-scope"))
    assert "PASS" in (tmp_path / "out" / "authority-result.md").read_text("utf-8")


def test_a_verifier_that_passes_everything_is_caught(tmp_path):
    v = _verifier(tmp_path, "lenient", "out.write_text(json.dumps({'verdicts': [{'case_id': c['case_id'], 'verdict': 'pass'} for c in cases]}))\n")
    r = ah.run(SET, v, tmp_path / "out")
    assert not r["pass"]
    assert sorted(x["case_id"] for x in r["cases"] if not x["aps_as_expected"]) == ["af-2-widened", "af-3-expired", "af-4-out-of-scope"]
    # cases 1 to 4 get through: 2 to 4 pass the work-order check, so only the delegation check stops them
    assert r["counts"]["admitted"] == 4 and not next(x for x in r["cases"] if x["case_id"].startswith("af-5"))["admitted"]


def test_errors_malformed_and_missing_answers_are_not_refusals(tmp_path):
    crash = _verifier(tmp_path, "crash", "raise SystemExit(3)\n")
    r = ah.run(SET, crash, tmp_path / "o1")
    assert not r["pass"] and r["counts"]["aps_classes"]["error"] == 5
    assert all(x["aps"]["verdict"] is None for x in r["cases"])
    odd = _verifier(tmp_path, "odd", "out.write_text(json.dumps({'verdicts': [{'case_id': c['case_id'], 'verdict': 'maybe'} for c in cases[1:]]}))\n")
    r = ah.run(SET, odd, tmp_path / "o2")
    assert r["counts"]["aps_classes"] == {"answered": 0, "error": 0, "timeout": 0, "malformed": 4, "unanswered": 1}
    assert r["counts"]["admitted"] == 0 and not r["pass"]


def test_a_changed_fixture_fails_the_set(tmp_path):
    s = tmp_path / "t1"
    shutil.copytree(SET, s)
    p = s / "cases" / "af-1-valid.json"
    case = json.loads(p.read_text("utf-8"))
    case["call"]["value"] = 20.0
    p.write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", "utf-8")
    r = ah.run(s, VERIFIER, tmp_path / "out")
    assert r["set_problems"] == ["changed cases/af-1-valid.json"] and not r["pass"]
