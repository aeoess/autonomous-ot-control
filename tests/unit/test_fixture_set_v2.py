"""Fixture set v2: v1's bytes plus four negatives, sealed against a verifier it names by commit and digest.

Provenance is kept in two parts. ``testbed_commit`` is the revision the
decision trails were recorded on, inherited from v1. ``sealed_against`` is
the verifier and scorer the set was sealed against: an existing, earlier
commit and the SHA-256 of the two files at that commit, never the commit
that adds the set. ``runner.edge_check --check-provenance`` fetches those
files from git, checks the digests, runs that verifier and requires the
verdict the manifest expects for every case, the four added faults first.
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path

from runner import edge_check
from runner.federation_fixtures import FAULTS, FAULTS_V2
from tests.unit.test_federation_kit import _score, _template_adapter

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "federation" / "fixtures" / "v1"
V2 = ROOT / "federation" / "fixtures" / "v2"


def test_v2_extends_v1_byte_for_byte():
    assert edge_check.check_fixture_set(V2) == []
    m1, _ = edge_check.read_manifest(V1)
    m2, _ = edge_check.read_manifest(V2)
    assert m2["derived_from"] == "v1" and m2["source_trails_commit"] == m1["testbed_commit"]
    files1 = {f["path"]: f["sha256"] for f in m1["files"]}
    files2 = {f["path"]: f["sha256"] for f in m2["files"]}
    assert all(files2[p] == sha for p, sha in files1.items()), "a v1 case changed on its way into v2"
    negs = [c for c in m2["cases"] if c["kind"] == "negative"]
    assert {c["fault"] for c in negs} == set(FAULTS) and len(negs) == len(FAULTS)
    assert set(m2["added_faults"]) == set(FAULTS_V2)
    assert {c["expected_verdict"] for c in negs if c["fault"] in FAULTS_V2} == {"NOT_ESTABLISHED", "CONTRADICTED"}


def test_v2_provenance_separates_trails_from_verifier_and_never_names_its_own_commit():
    m1, _ = edge_check.read_manifest(V1)
    m2, _ = edge_check.read_manifest(V2)
    assert m2["testbed_commit"] == m1["testbed_commit"], "testbed_commit is the trails' revision, inherited"
    sa = m2["sealed_against"]
    assert sa["verifier_commit"] != m2["testbed_commit"]
    head = edge_check._git("rev-parse", "HEAD")
    assert head, "git is needed to check provenance"
    assert sa["verifier_commit"] != head, "a set must not name the commit that contains it"
    assert edge_check._git("merge-base", "--is-ancestor", sa["verifier_commit"], head) is not None
    for role in ("verifier", "scorer"):
        raw = edge_check.git_file_at(sa["verifier_commit"], sa[f"{role}_path"])
        assert raw is not None, f"{sa[f'{role}_path']} at {sa['verifier_commit'][:12]}"
        assert edge_check.sha256_bytes(raw) == sa[f"{role}_sha256"]


def test_the_declared_verifier_fetched_from_git_gives_every_expected_verdict():
    assert edge_check.check_provenance(V2) == []


def test_a_wrong_declared_digest_is_caught():
    m2, _ = edge_check.read_manifest(V2)
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "v2"
        shutil.copytree(V2, bad)
        mb = copy.deepcopy(m2)
        mb["sealed_against"]["verifier_sha256"] = "0" * 64
        (bad / "manifest.json").write_text(json.dumps(mb, indent=2, sort_keys=True) + "\n", "utf-8")
        probs = edge_check.check_provenance(bad)
        assert probs and any("manifest declares" in p for p in probs)


def test_the_current_reference_verifier_answers_v2_as_expected():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "result.json"
        assert _template_adapter().main(["run", "--fixtures", str(V2), "--out", str(out)]) == 0
        s = _score(V2, json.loads(out.read_text("utf-8")))
    assert s["ok"] and s["negatives_caught"] == len(FAULTS) and s["positives_established"] == 12
