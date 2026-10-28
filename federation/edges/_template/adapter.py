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
import math
import sys
from pathlib import Path
from typing import Any

EDGE_ID = "template-reference-verifier"


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _case_error(case: Any) -> str | None:
    """Validate the consumed case shape with stdlib only (also in copied/sandboxed edges).

    Optional evidence stays optional; a missing value is handled by its claim.
    This admission guard is maintainer integration, not upstream reader logic.
    """
    required = {"schema", "case_id", "kind", "claim", "decision", "execution",
                "twin_operation", "observation", "remora_evidence", "evidence_sha256"}
    if not isinstance(case, dict) or not required <= case.keys():
        return "missing required case fields"
    if case.keys() - required - {"fault", "remora_envelope"}:
        return "unknown top-level field"
    if case["schema"] != "ot-federation-case/v1" or case["kind"] not in ("positive", "negative"):
        return "unknown schema or case kind"
    if not isinstance(case["case_id"], str) or ("fault" in case and case["fault"] is not None
                                               and not isinstance(case["fault"], str)):
        return "invalid case id or fault"
    digest = case["evidence_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return "invalid evidence digest"
    try:
        json.dumps(case, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return "not finite JSON"

    def number(v: Any) -> bool:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False
        try:
            return math.isfinite(v)
        except OverflowError:
            return False  # JSON integers may exceed the numeric range consumed by the adapter.

    def integer(v: Any) -> bool:
        return number(v) and v % 1 == 0

    claim, decision, execution = case["claim"], case["decision"], case["execution"]
    strings = ("proposal_id", "tool", "zone_id", "claimed_effect")
    if not isinstance(claim, dict) or not all(isinstance(claim.get(k), str) for k in strings) \
            or not number(claim.get("setpoint_c")):
        return "invalid claim"
    if not isinstance(decision, dict) or not isinstance(decision.get("decision"), str) \
            or type(decision.get("approved")) is not bool:
        return "invalid decision"
    if "reasons" in decision and not isinstance(decision["reasons"], list):
        return "invalid decision reasons"
    if decision.get("reviewer") is not None and not isinstance(decision["reviewer"], dict):
        return "invalid reviewer"
    if not isinstance(execution, dict) or type(execution.get("executed")) is not bool:
        return "invalid execution"
    if "executions" in execution and not integer(execution["executions"]):
        return "invalid execution count"
    if execution.get("dispatched_at_sim_s") is not None and not integer(execution["dispatched_at_sim_s"]):
        return "invalid dispatch time"
    for section in ("twin_operation", "observation"):
        value = case[section]
        if value is not None and not isinstance(value, dict):
            return "invalid " + section
    obs = case["observation"]
    if obs is not None:
        if "observed_setpoint_c" in obs and not number(obs["observed_setpoint_c"]):
            return "invalid observed value"
        if "observed_at_sim_s" in obs and not integer(obs["observed_at_sim_s"]):
            return "invalid observation time"
    op = case["twin_operation"]
    if op is not None:
        for key in ("requested_setpoint_c", "new_setpoint_c"):
            if op.get(key) is not None and not number(op[key]):
                return "invalid twin value"
    return None


def _binding_issue(case: dict[str, Any]) -> tuple[str, str] | None:
    """Check recorded direct and native REMORA identity paths, without inventing absent IDs."""
    paths = (("remora_envelope", "request_id"), ("remora_envelope", "request", "request_id"),
             ("remora_evidence", "proposal_id"), ("remora_evidence", "manifest", "proposal_id"),
             ("remora_evidence", "proposal", "proposal_id"))
    for path in paths:
        value: Any = case
        for key in path:
            if value is None:  # optional opaque sections may be absent/null
                break
            if not isinstance(value, dict):
                return "NOT_ESTABLISHED", "unreadable proposal binding at " + ".".join(path)
            if key not in value:
                break
            value = value[key]
        else:
            if value != case["claim"]["proposal_id"]:
                return "CONTRADICTED", "proposal binding differs at " + ".".join(path)
    return None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON member")
        obj[key] = value
    return obj


def _invalid_constant(value: str) -> Any:
    raise ValueError("non-finite JSON constant")


def check(case: dict[str, Any]) -> tuple[str, str, str | None]:
    """Return (verdict, reason, claim_id of the failing or missing check).

    Every claim names the evidence it needs. Evidence that is absent makes the
    claim NOT_ESTABLISHED; nothing is assumed in its place. Evidence that
    disagrees with other evidence in the same trail makes it CONTRADICTED.
    Only a trail that carries everything, consistently, is ESTABLISHED.
    """
    error = _case_error(case)
    if error:
        return "NOT_ESTABLISHED", "malformed case: " + error, None
    claim, decision, execution = case["claim"], case["decision"], case["execution"]
    op, obs = case.get("twin_operation"), case.get("observation")
    executed = execution.get("executed")
    approved = decision.get("decision") == "accept" or (
        decision.get("decision") in ("verify", "escalate") and decision.get("approved") is True)
    applied = op is not None and op.get("status") == "APPLIED"

    # 1. decision_correspondence: what the trail says happened must agree with itself,
    #    and an execution needs a captured ACCEPT or an approved decision.
    if executed is False and (applied or obs is not None):
        return ("CONTRADICTED", "the trail says nothing executed, yet the twin recorded APPLIED or an effect "
                "was observed", "decision_correspondence")
    if executed is not True:
        return "NOT_ESTABLISHED", "execution.executed is missing or not true", "decision_correspondence"
    if not approved:
        return "CONTRADICTED", "executed without an approval or an ACCEPT", "decision_correspondence"
    if decision["decision"] in ("verify", "escalate"):
        reviewer = decision.get("reviewer")
        if not isinstance(reviewer, dict) or type(reviewer.get("approved")) is not bool:
            return "NOT_ESTABLISHED", "reviewer approval missing or invalid", "decision_correspondence"
        if reviewer["approved"] is False:
            return "CONTRADICTED", "reviewer refused the executed proposal", "decision_correspondence"

    # 2. single_dispatch: the count must be present and be exactly one.
    executions = execution.get("executions")
    if executions is None:
        return "NOT_ESTABLISHED", "execution.executions is missing", "single_dispatch"
    if executions != 1:
        return "CONTRADICTED", f"executed {executions} times", "single_dispatch"

    # 3. call_binding: the twin's own record must exist and belong to this proposal.
    binding = _binding_issue(case)
    if binding:
        return binding[0], binding[1], "call_binding"
    if op is None:
        return "NOT_ESTABLISHED", "no twin operation record to compare with", "call_binding"
    if op.get("operation_id") != claim["proposal_id"]:
        return "CONTRADICTED", "the twin operation belongs to another proposal", "call_binding"

    # 4. declared_effect_observed: APPLIED for the claimed zone and value, and observed so.
    if op.get("status") != "APPLIED":
        return "CONTRADICTED", f"the twin recorded {op.get('status')}, not APPLIED", "declared_effect_observed"
    if op.get("zone_id") != claim["zone_id"] or op.get("new_setpoint_c") is None \
            or abs(float(op["new_setpoint_c"]) - claim["setpoint_c"]) > 1e-6:
        return "CONTRADICTED", "the twin applied a different zone or value than claimed", "declared_effect_observed"
    if obs is None:
        return "NOT_ESTABLISHED", "no observation of the effect", "declared_effect_observed"
    if obs.get("observed_setpoint_c") is None:
        return "NOT_ESTABLISHED", "observation.observed_setpoint_c is missing", "declared_effect_observed"
    if abs(float(obs["observed_setpoint_c"]) - claim["setpoint_c"]) > 1e-6:
        return "CONTRADICTED", "the observed setpoint differs from the claim", "declared_effect_observed"

    # 5. observation_after_dispatch: both times must be present; the observation must not precede the dispatch.
    dispatched, observed_at = execution.get("dispatched_at_sim_s"), obs.get("observed_at_sim_s")
    if dispatched is None:
        return "NOT_ESTABLISHED", "execution.dispatched_at_sim_s is missing", "observation_after_dispatch"
    if observed_at is None:
        return "NOT_ESTABLISHED", "observation.observed_at_sim_s is missing", "observation_after_dispatch"
    if observed_at < dispatched:
        return "CONTRADICTED", "the effect was observed before the dispatch", "observation_after_dispatch"

    # 6. evidence_digest_integrity.
    if case.get("evidence_sha256") is None or "remora_evidence" not in case:
        return "NOT_ESTABLISHED", "evidence or its digest is missing", "evidence_digest_integrity"
    if case["evidence_sha256"] != canonical_sha256(case["remora_evidence"]):
        return "CONTRADICTED", "the evidence digest does not match the evidence", "evidence_digest_integrity"
    return "ESTABLISHED", "approval, single dispatch, twin record, observation, timing and digest agree", None


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
        case: Any = None
        try:
            case = json.loads(path.read_text("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
            verdict, reason, claim_id = check(case)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError) as e:
            verdict, reason, claim_id = "NOT_ESTABLISHED", f"malformed case: {type(e).__name__}", None
        case_id = case.get("case_id") if isinstance(case, dict) else None
        entry: dict[str, Any] = {"case_id": case_id if isinstance(case_id, str) else path.stem,
                                 "verdict": verdict, "reason": reason}
        if claim_id:
            entry["claim_id"] = claim_id
        verdicts.append(entry)
    result = {"schema": "ot-edge-result/v1", "edge_id": EDGE_ID,
              "fixtures_sha256": hashlib.sha256(manifest).hexdigest(), "verdicts": verdicts}
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
