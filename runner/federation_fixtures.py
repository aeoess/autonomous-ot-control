"""Build a federation fixture set from a governed demo run (docs/federation/README.md).

    python -m runner.federation_fixtures --work artifacts/<run>-work --project aoc-demo \\
        --out federation/fixtures/v1 --testbed-commit <sha>

Input:
- the demo's decision files (`decisions/<proposal_id>.json`: ledger row,
  REMORA envelope, REMORA evidence);
- the twin's own operation record for each executed proposal, read from the
  still-running demo stack through the harness's twin read token. The twin
  keys operations by REMORA's proposal id, so this is an independent witness
  of what the plant did.

Output: `cases/*.json` (`contracts/federation-case-v1.schema.json`) and
`manifest.json` listing every case with its kind, its seeded fault and its
expected verdict.

- **Positive cases** are recorded trails of executed, approved writes whose
  effect was observed.
- **Negative cases** are copies of positives with exactly one fault seeded:

  | Fault | What changes |
  |---|---|
  | `effect_value_not_reached` | the claim names a value the plant never reached |
  | `proposal_swapped` | the twin record belongs to another proposal |
  | `evidence_digest_mismatch` | one byte of the evidence changed |
  | `observed_before_dispatch` | the observation time precedes the dispatch |
  | `executed_without_approval` | approved false, decision verify |
  | `replayed_execution` | executions = 2 |

An existing directory is refused, so a fixture set is never overwritten.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from runner.acceptance import Stack

FAULTS = ("effect_value_not_reached", "proposal_swapped", "evidence_digest_mismatch",
          "observed_before_dispatch", "executed_without_approval", "replayed_execution")


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
    else:  # pragma: no cover
        raise ValueError(fault)
    return c


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.federation_fixtures")
    p.add_argument("--work", required=True)
    p.add_argument("--project", default="aoc-demo")
    p.add_argument("--out", required=True)
    p.add_argument("--testbed-commit", default="UNKNOWN")
    p.add_argument("--max-positives", type=int, default=12)
    a = p.parse_args(argv)
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
    entries = []
    for case in positives + negatives:
        (out / "cases" / f"{case['case_id']}.json").write_text(
            json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        entries.append({"case_id": case["case_id"], "kind": case["kind"], "fault": case["fault"],
                        "expected_verdict": "ESTABLISHED" if case["kind"] == "positive" else "CONTRADICTED"})
    manifest = {"schema": "ot-federation-fixtures/v1", "fixture_set": out.name,
                "testbed_commit": a.testbed_commit, "source_run": Path(a.work).name,
                "scope": ("Decision trails from a governed demo of the simulated building (same-test-deployment "
                          "vantage); the twin's operation log is the independent witness. Not a real plant."),
                "cases": entries}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(positives)} positive, {len(negatives)} negative cases in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
