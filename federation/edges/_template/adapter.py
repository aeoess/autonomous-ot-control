"""Reference offline edge: is each case's claimed effect backed by the evidence?

Copy this directory to federation/edges/<your-edge-id>/ (or let
`python -m runner.edge_check --init <your-edge-id>` do it) and replace check()
with your own logic, or keep this file as a thin wrapper around your tool. It
uses only the standard library and runs with no network:

    python adapter.py run --fixtures <dir> --out <result.json>

For every case under <dir>/cases/ it writes one verdict:

- ESTABLISHED: every check below holds.
- CONTRADICTED: one of them fails. The reason names the first that failed and
  claim_id names the claim it contradicts.
- NOT_ESTABLISHED: a piece of evidence needed for a check is missing.

The checks, in order, and the claim each one serves (the claim ids are those
of federation/fixtures/<set>/manifest.json):

1. decision_correspondence: the change was approved (or accepted) before it executed;
2. single_dispatch: it executed exactly once;
3. call_binding: the twin's own operation record exists for operation_id = proposal_id;
4. declared_effect_observed: that record shows APPLIED with the claimed zone and value,
   and the observation shows the claimed setpoint;
5. observation_after_dispatch: the observation was made at or after the dispatch;
6. evidence_digest_integrity: evidence_sha256 is the SHA-256 of the canonical JSON of
   remora_evidence (sort_keys, separators (",", ":"), ensure_ascii=False, UTF-8).

If you replace check() with your own implementation, set evaluator.kind to
"second_implementation" in edge.json; the independence level of your run
follows from that declaration, the operator and the host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EDGE_ID = "template-reference-verifier"


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check(case: dict[str, Any]) -> tuple[str, str, str | None]:
    """Return (verdict, reason, claim_id of the failing or missing check)."""
    claim, decision, execution = case["claim"], case["decision"], case["execution"]
    op, obs = case.get("twin_operation"), case.get("observation")
    if execution.get("executed") and not decision.get("approved") and decision.get("decision") != "accept":
        return "CONTRADICTED", "executed without an approval or an ACCEPT", "decision_correspondence"
    if execution.get("executions", 1) != 1:
        return "CONTRADICTED", f"executed {execution.get('executions')} times", "single_dispatch"
    if op is None:
        return "NOT_ESTABLISHED", "no twin operation record to compare with", "call_binding"
    if op.get("operation_id") != claim["proposal_id"]:
        return "CONTRADICTED", "the twin operation belongs to another proposal", "call_binding"
    if op.get("status") != "APPLIED":
        return "CONTRADICTED", f"the twin recorded {op.get('status')}, not APPLIED", "declared_effect_observed"
    if op.get("zone_id") != claim["zone_id"] or abs(float(op.get("new_setpoint_c", -999)) - claim["setpoint_c"]) > 1e-6:
        return "CONTRADICTED", "the twin applied a different zone or value than claimed", "declared_effect_observed"
    if obs is None:
        return "NOT_ESTABLISHED", "no observation of the effect", "declared_effect_observed"
    if abs(float(obs["observed_setpoint_c"]) - claim["setpoint_c"]) > 1e-6:
        return "CONTRADICTED", "the observed setpoint differs from the claim", "declared_effect_observed"
    dispatched = execution.get("dispatched_at_sim_s")
    if dispatched is not None and obs.get("observed_at_sim_s", dispatched) < dispatched:
        return "CONTRADICTED", "the effect was observed before the dispatch", "observation_after_dispatch"
    if case["evidence_sha256"] != canonical_sha256(case["remora_evidence"]):
        return "CONTRADICTED", "the evidence digest does not match the evidence", "evidence_digest_integrity"
    return "ESTABLISHED", "approval, single dispatch, twin record, observation and digest agree", None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--fixtures", required=True)
    r.add_argument("--out", required=True)
    a = p.parse_args(argv)
    root = Path(a.fixtures)
    manifest = (root / "manifest.json").read_bytes()
    verdicts = []
    for path in sorted((root / "cases").glob("*.json")):
        case = json.loads(path.read_text("utf-8"))
        verdict, reason, claim_id = check(case)
        entry: dict[str, Any] = {"case_id": case["case_id"], "verdict": verdict, "reason": reason}
        if claim_id:
            entry["claim_id"] = claim_id
        verdicts.append(entry)
    result = {"schema": "ot-edge-result/v1", "edge_id": EDGE_ID,
              "fixtures_sha256": hashlib.sha256(manifest).hexdigest(), "verdicts": verdicts}
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
