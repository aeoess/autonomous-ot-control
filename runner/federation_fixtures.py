"""Build and seal a federation fixture set from a governed demo run (docs/federation/README.md).

    python -m runner.federation_fixtures --work artifacts/<run>-work --project aoc-demo \\
        --out federation/fixtures/v1 --testbed-commit <sha>
    python -m runner.federation_fixtures --seal federation/fixtures/<set>   # (re)compute digests and claims

Input:
- the demo's decision files (`decisions/<proposal_id>.json`: ledger row,
  REMORA envelope, REMORA evidence);
- the twin's own operation record for each executed proposal, read from the
  still-running demo stack through the harness's twin read token. The twin
  keys operations by REMORA's proposal id, so this is an independent witness
  of what the plant did.

Output: `cases/*.json` (`contracts/federation-case-v1.schema.json`) and a
sealed `manifest.json` (`contracts/federation-fixtures-v1.schema.json`)
listing every case with its kind, its seeded fault, the claim that fault
contradicts, the expected verdict, the SHA-256 of every case file and the
package digest over those digests.

- **Positive cases** are recorded trails of executed, approved writes whose
  effect was observed.
- **Negative cases** are copies of positives with exactly one fault seeded:

  | Fault | What changes | Claim it contradicts |
  |---|---|---|
  | `effect_value_not_reached` | the claim names a value the plant never reached | `declared_effect_observed` |
  | `proposal_swapped` | the twin record belongs to another proposal | `call_binding` |
  | `evidence_digest_mismatch` | one byte of the evidence changed | `evidence_digest_integrity` |
  | `observed_before_dispatch` | the observation time precedes the dispatch | `observation_after_dispatch` |
  | `executed_without_approval` | approved false, decision verify | `decision_correspondence` |
  | `replayed_execution` | executions = 2 | `single_dispatch` |

An existing directory is refused, so a fixture set is never overwritten.
Sealing an existing set rewrites only its manifest.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

#: The six faults of fixture set v1 (sealed 2026-10-27, never edited).
FAULTS_V1 = ("effect_value_not_reached", "proposal_swapped", "evidence_digest_mismatch",
             "observed_before_dispatch", "executed_without_approval", "replayed_execution")
#: Added in v2 after the reference verifier was found to accept missing and
#: contradictory evidence: three claims with their evidence removed (expected
#: NOT_ESTABLISHED, never assumed) and one trail that disagrees with itself.
FAULTS_V2 = ("dispatch_time_missing", "observation_time_missing", "execution_count_missing",
             "denied_but_applied")
FAULTS = FAULTS_V1 + FAULTS_V2
#: What a correct verifier answers for each seeded fault.
EXPECTED_VERDICT = {f: "CONTRADICTED" for f in FAULTS_V1} | {
    "dispatch_time_missing": "NOT_ESTABLISHED",
    "observation_time_missing": "NOT_ESTABLISHED",
    "execution_count_missing": "NOT_ESTABLISHED",
    "denied_but_applied": "CONTRADICTED",
}

#: One claim per seeded fault. An edge names which of these it checks.
CLAIMS: dict[str, str] = {
    "decision_correspondence": "a dispatch is attested only when a captured ACCEPT, or an approved "
                               "VERIFY/ESCALATE, is bound to the proposal",
    "single_dispatch": "the approved proposal executed exactly once",
    "call_binding": "the twin's own operation record belongs to this proposal (operation_id = proposal_id)",
    "declared_effect_observed": "the twin recorded APPLIED for the claimed zone and value, and the "
                                "observation shows that value",
    "observation_after_dispatch": "the effect was observed at or after the dispatch",
    "evidence_digest_integrity": "evidence_sha256 is the canonical SHA-256 of remora_evidence",
}
FAULT_CLAIM: dict[str, str] = {
    "effect_value_not_reached": "declared_effect_observed",
    "proposal_swapped": "call_binding",
    "evidence_digest_mismatch": "evidence_digest_integrity",
    "observed_before_dispatch": "observation_after_dispatch",
    "executed_without_approval": "decision_correspondence",
    "replayed_execution": "single_dispatch",
    "dispatch_time_missing": "observation_after_dispatch",
    "observation_time_missing": "observation_after_dispatch",
    "execution_count_missing": "single_dispatch",
    "denied_but_applied": "decision_correspondence",
}
PACKAGE_DIGEST_RULE = ("sha256 over the UTF-8 text formed by one line per files entry, '<path> <sha256>\\n', "
                       "sorted by path. manifest.json is outside the package and is never hashed into it.")


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def positive(rec: dict[str, Any], twin_op: dict[str, Any] | None) -> dict[str, Any] | None:
    row = rec["ledger_row"]
    if not row.get("applied") or twin_op is None:
        return None
    pid = rec["proposal_id"]
    evidence = rec.get("evidence")
    return {
        "schema": "ot-federation-case/v1", "case_id": f"pos-{pid}", "kind": "positive", "fault": None,
        "claim": {"proposal_id": pid, "tool": "set_zone_setpoint", "zone_id": row["zone_id"],
                  "setpoint_c": row["to_c"], "claimed_effect": "EFFECT_VERIFIED"},
        "decision": {"decision": row.get("decision"), "reasons": row.get("reasons") or [],
                     "approved": bool((row.get("reviewer") or {}).get("approved")),
                     "reviewer": row.get("reviewer")},
        "execution": {"executed": True, "dispatched_at_sim_s": row["sim_time_s"], "executions": 1},
        "twin_operation": twin_op,
        "observation": {"observed_setpoint_c": twin_op["new_setpoint_c"],
                        "observed_at_sim_s": int(twin_op["sim_time_s"])},
        "remora_evidence": evidence, "remora_envelope": rec.get("envelope"),
        "evidence_sha256": canonical_sha256(evidence),
    }


def seed_fault(case: dict[str, Any], fault: str, other: dict[str, Any]) -> dict[str, Any]:
    c = copy.deepcopy(case)
    c["kind"], c["fault"] = "negative", fault
    c["case_id"] = f"neg-{fault}-{case['claim']['proposal_id']}"
    if fault == "effect_value_not_reached":
        c["claim"]["setpoint_c"] = round(c["claim"]["setpoint_c"] + 1.0, 1)
    elif fault == "proposal_swapped":
        c["twin_operation"] = copy.deepcopy(other["twin_operation"])
    elif fault == "evidence_digest_mismatch":
        c["remora_evidence"] = {"tampered": True, "original": c["remora_evidence"]}
    elif fault == "observed_before_dispatch":
        c["observation"]["observed_at_sim_s"] = c["execution"]["dispatched_at_sim_s"] - 900
    elif fault == "executed_without_approval":
        c["decision"]["approved"], c["decision"]["decision"] = False, "verify"
    elif fault == "replayed_execution":
        c["execution"]["executions"] = 2
    elif fault == "dispatch_time_missing":
        del c["execution"]["dispatched_at_sim_s"]
    elif fault == "observation_time_missing":
        del c["observation"]["observed_at_sim_s"]
    elif fault == "execution_count_missing":
        del c["execution"]["executions"]
    elif fault == "denied_but_applied":
        c["decision"]["decision"], c["decision"]["approved"] = "deny", False
        c["execution"]["executed"] = False  # the twin's APPLIED record stays: the trail disagrees with itself
    else:  # pragma: no cover
        raise ValueError(fault)
    return c


def manifest_entry(case: dict[str, Any]) -> dict[str, Any]:
    fault = case.get("fault")
    return {"case_id": case["case_id"], "kind": case["kind"], "fault": fault,
            "claim_id": FAULT_CLAIM[fault] if fault else None,
            "expected_verdict": "ESTABLISHED" if case["kind"] == "positive" else EXPECTED_VERDICT[fault]}


def sealed_against(verifier_commit: str, root: Path) -> dict[str, Any]:
    """Pin the verifier and scorer a set is sealed against: a commit that already exists,
    plus the digests of the two files at that commit (read from git, not from the
    working tree, so the declaration cannot drift from the history it names)."""
    from runner.edge_check import REFERENCE_VERIFIER_PATH, SCORER_PATH, git_file_at, sha256_bytes

    if not re.fullmatch(r"[0-9a-f]{40}", verifier_commit):
        raise ValueError("verifier_commit must be a full 40-hex commit id that already exists")
    out = {"verifier_commit": verifier_commit}
    for role, path in (("verifier", REFERENCE_VERIFIER_PATH), ("scorer", SCORER_PATH)):
        raw = git_file_at(verifier_commit, path)
        if raw is None:
            raise ValueError(f"{path} is not readable at {verifier_commit[:12]} in this repository's history")
        out[f"{role}_path"] = path
        out[f"{role}_sha256"] = sha256_bytes(raw)
    out["note"] = ("The commit that adds this set is later than verifier_commit and is never named here; "
                   "python -m runner.edge_check --check-provenance fetches these files at verifier_commit, "
                   "checks the digests and runs that verifier against this set.")
    return out


def derive_set(src: Path, out: Path, *, faults: tuple[str, ...] = FAULTS_V2, verifier_commit: str,
               note: str = "", root: Path | None = None) -> dict[str, Any]:
    """A new fixture set from an existing one: every case copied byte for byte, plus one new
    negative per fault in ``faults`` seeded from the source's positives. The source is never touched.

    ``testbed_commit`` stays the revision the trails were recorded on (the source's). The
    verifier and scorer the set is sealed against are a separate, earlier commit with file
    digests (``sealed_against``), so the set never names the commit that contains it."""
    out.mkdir(parents=True, exist_ok=False)
    (out / "cases").mkdir()
    src_manifest = json.loads((src / "manifest.json").read_text("utf-8"))
    positives = []
    for path in sorted((src / "cases").glob("*.json")):
        (out / "cases" / path.name).write_bytes(path.read_bytes())
        case = json.loads(path.read_text("utf-8"))
        if case["kind"] == "positive":
            positives.append(case)
    if len(positives) < 2:
        raise SystemExit("need at least two positives to seed new negatives")
    for i, fault in enumerate(faults):
        case = seed_fault(positives[i % len(positives)], fault, positives[(i + 1) % len(positives)])
        (out / "cases" / f"{case['case_id']}.json").write_text(
            json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"schema": "ot-federation-fixtures/v1", "fixture_set": out.name,
                "testbed_commit": src_manifest.get("testbed_commit", "UNKNOWN"),
                "testbed_commit_note": ("The revision the decision trails were recorded on, inherited from the "
                                        "source set. It says nothing about which verifier sealed this set: see "
                                        "sealed_against."),
                "derived_from": src_manifest.get("fixture_set"),
                "source_trails_commit": src_manifest.get("testbed_commit"),
                "source_run": src_manifest.get("source_run"), "scope": src_manifest.get("scope", ""),
                "added_faults": list(faults),
                "sealed_against": sealed_against(verifier_commit, root or src.parents[2]),
                "derivation_note": note}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return seal_manifest(out)


def seal_manifest(fixtures: Path) -> dict[str, Any]:
    """Compute per-file digests, the package digest and the claim map for an existing set; rewrite manifest.json."""
    from runner.edge_check import package_digest, sha256_file  # local import: edge_check imports nothing from here

    mp = fixtures / "manifest.json"
    manifest = json.loads(mp.read_text("utf-8")) if mp.is_file() else {}
    cases = []
    files = []
    for path in sorted((fixtures / "cases").glob("*.json")):
        case = json.loads(path.read_text("utf-8"))
        cases.append(manifest_entry(case))
        files.append({"path": f"cases/{path.name}", "sha256": sha256_file(path)})
    manifest.update({
        "schema": "ot-federation-fixtures/v1",
        "fixture_set": manifest.get("fixture_set", fixtures.name),
        "claims": [{"claim_id": k, "statement": v} for k, v in CLAIMS.items()],
        "cases": cases,
        "files": files,
        "package_digest": package_digest(files),
        "package_digest_rule": PACKAGE_DIGEST_RULE,
        "digest_rules": {
            "evidence_sha256": "SHA-256 over the UTF-8 bytes of json.dumps(remora_evidence, sort_keys=True, "
                               "separators=(',', ':'), ensure_ascii=False)",
            "fixtures_sha256": "SHA-256 over the raw bytes of manifest.json, reported by the edge in its result",
        },
    })
    mp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.federation_fixtures")
    p.add_argument("--seal", metavar="DIR", help="(re)seal an existing fixture set's manifest and exit")
    p.add_argument("--derive-from", metavar="SRC", help="build --out from an existing set plus the v2 faults")
    p.add_argument("--verifier-commit", default="",
                   help="for --derive-from: the existing commit whose reference verifier and scorer seal the set")
    p.add_argument("--note", default="", help="derivation_note for a derived set")
    p.add_argument("--work")
    p.add_argument("--project", default="aoc-demo")
    p.add_argument("--out")
    p.add_argument("--testbed-commit", default="UNKNOWN")
    p.add_argument("--max-positives", type=int, default=12)
    a = p.parse_args(argv)
    if a.seal:
        m = seal_manifest(Path(a.seal))
        print(f"sealed {a.seal}: {len(m['cases'])} cases, {m['package_digest']}")
        return 0
    if a.derive_from:
        if not (a.out and a.verifier_commit):
            p.error("--derive-from needs --out and --verifier-commit")
        m = derive_set(Path(a.derive_from), Path(a.out), verifier_commit=a.verifier_commit, note=a.note)
        sa = m["sealed_against"]
        print(f"derived {a.out} from {a.derive_from}: {len(m['cases'])} cases, {m['package_digest']}, "
              f"sealed against verifier {sa['verifier_commit'][:12]} ({sa['verifier_sha256'][:12]}…)")
        return 0
    if not (a.work and a.out):
        p.error("--work and --out are required to build a set")
    from runner.acceptance import Stack  # Docker-side dependency, only when building

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "cases").mkdir()
    s = Stack(["docker", "compose", "-p", a.project, "-f", "compose.yaml", "-f", "compose.governed.yaml"])
    positives: list[dict[str, Any]] = []
    for f in sorted(Path(a.work, "decisions").glob("*.json")):
        rec = json.loads(f.read_text("utf-8"))
        if not rec["ledger_row"].get("applied"):
            continue
        op = s.step("operation", operation_id=rec["proposal_id"])
        case = positive(rec, op.get("body") if op.get("ok") else None)
        if case is not None:
            positives.append(case)
        if len(positives) >= a.max_positives:
            break
    if len(positives) < 2:
        raise SystemExit("need at least two executed proposals to build negatives")
    negatives = [seed_fault(positives[i % len(positives)], fault, positives[(i + 1) % len(positives)])
                 for i, fault in enumerate(FAULTS)]
    for case in positives + negatives:
        (out / "cases" / f"{case['case_id']}.json").write_text(
            json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"schema": "ot-federation-fixtures/v1", "fixture_set": out.name,
                "testbed_commit": a.testbed_commit, "source_run": Path(a.work).name,
                "scope": ("Decision trails from a governed demo of the simulated building (same-test-deployment "
                          "vantage); the twin's operation log is the independent witness. Not a real plant.")}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    seal_manifest(out)
    print(f"{len(positives)} positive, {len(negatives)} negative cases in {out}, sealed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
