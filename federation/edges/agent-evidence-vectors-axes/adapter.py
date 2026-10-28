"""Offline edge: the trails read through agent-evidence-vectors' invocation-axes reader.

    python adapter.py run --fixtures <dir> --out <result.json>

invocation_axes_reader.py is agent_evidence_vectors/invocation_axes/reader.py from
agent-evidence-vectors==0.28.0 (PyPI), unmodified, standard library only. The
sandbox has no network, so the file sits beside this adapter; the adapter refuses
to run unless its SHA-256 is the one the 0.28.0 wheel carries. To use the package
instead: pip install agent-evidence-vectors==0.28.0, same file, same digest.

Each trail is mapped onto the reader's inputs:
- request: MCP tools/call {name: claim.tool, arguments: {proposal_id, zone_id,
  setpoint_c}}; setpoints travel as fixed three-decimal strings because the reader
  refuses floats.
- decision: allow for accept, or verify/escalate with approved true; deny otherwise.
- capture package: request.json, decision.json and evidence.json (compact sorted
  JSON of remora_evidence, pinned by the trail's own evidence_sha256).
- execution bundle: its call binding is computed from the twin's own operation
  record (operation_id, zone, requested setpoint); dispatch from execution.executed;
  postcondition is the claim; observation fields come from the trail's observation.

By default the observation counts as an independent reading, as the testbed's
guide scores it. The trails record that observation's vantage as same_deployment;
under the reader's own rule that is not independent, and the reason text says so
on every established case. With --strict-vantage the recorded vantage decides:
declared_effect_observed is then NOT_ESTABLISHED on every trail.

The reader has no dispatch-count axis and no observation-time axis, so
single_dispatch and observation_after_dispatch are two plain checks here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# Maintainer integration: validate raw trail facts before accepting the mapped
# reader verdict. The vendored upstream reader remains byte-for-byte unchanged.
EDGE_ID = "agent-evidence-vectors-axes"
HERE = Path(__file__).resolve().parent
READER_VERSION = "0.28.0"
READER_SHA256 = "a86c2c4830af011f3707c8164ca1350c9b965d2a960967bff83c79532df54938"
ORDER = ("decision_correspondence", "single_dispatch", "call_binding",
         "declared_effect_observed", "observation_after_dispatch", "evidence_digest_integrity")


def load_reader() -> Any:
    path = HERE / "invocation_axes_reader.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != READER_SHA256:
        raise SystemExit("invocation_axes_reader.py is not the agent-evidence-vectors 0.28.0 file")
    spec = importlib.util.spec_from_file_location("invocation_axes_reader", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["invocation_axes_reader"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


R = load_reader()


def _request(proposal_id: str, zone: str, setpoint: float, tool: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": tool, "arguments": {"proposal_id": proposal_id, "zone_id": zone, "setpoint_c": f"{setpoint:.3f}"}}}


def _decision(case: dict[str, Any]) -> str:
    d = case["decision"]
    return "allow" if d.get("decision") == "accept" or (
        d.get("decision") in ("verify", "escalate") and d.get("approved") is True) else "deny"


def _evidence_text(case: dict[str, Any]) -> str:
    return json.dumps(case["remora_evidence"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)


STRICT = False  # --strict-vantage: take the observation source from the recorded vantage


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _vantage(case: dict[str, Any]) -> str:
    env = case.get("remora_envelope") or {}
    entry = (env.get("effect") or {}).get("ledger_entry") or {} if isinstance(env, dict) else {}
    return entry.get("effect_vantage", "unrecorded") if isinstance(entry, dict) else "unrecorded"


def _build(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    c = case["claim"]
    req = _request(c["proposal_id"], c["zone_id"], c["setpoint_c"], c["tool"])
    binding = R.invocation_binding(req)
    objs = {"request.json": json.dumps(req, sort_keys=True),
            "decision.json": json.dumps({"request_binding": binding, "decision": _decision(case)}, sort_keys=True),
            "evidence.json": _evidence_text(case)}
    listed = [{"name": n, "sha256": R.sha256_tag(t.encode())} for n, t in sorted(objs.items())]
    for m in listed:
        if m["name"] == "evidence.json":
            m["sha256"] = "sha256:" + case["evidence_sha256"]
    manifest = {"schema": R.CAPTURE_SCHEMA, "objects": listed}
    capture = {"pinned_package_digest": R.sha256_tag(R.canonical(manifest)), "manifest": manifest, "objects": objs}
    op, obs, ex = case.get("twin_operation"), case.get("observation"), case["execution"]
    bundle = None
    if op is not None and all(op.get(k) is not None for k in ("operation_id", "zone_id", "requested_setpoint_c")):
        bundle = {"schema": R.EXECUTION_SCHEMA,
                  "call_binding": R.invocation_binding(_request(op["operation_id"], op["zone_id"], op["requested_setpoint_c"], c["tool"])),
                  "dispatch": {"outcome": "DISPATCHED" if ex.get("executed") else "REFUSED"},
                  "execution_report": {"status": "success" if op.get("status") == "APPLIED" else "failure"},
                  "postcondition": {"expected_fields": {"setpoint_c": f"{c['setpoint_c']:.3f}"}}}
        # An observation without a numeric observed value is no observation (the case contract makes
        # declared_effect_observed NOT_ESTABLISHED); it is left out rather than formatted or guessed.
        if obs is not None and _number(obs.get("observed_setpoint_c")):
            bundle["observation"] = {"source": _vantage(case) if STRICT else R.INDEPENDENT_SOURCE,
                                     "fields": {"setpoint_c": f"{obs['observed_setpoint_c']:.3f}"}}
    return req, capture, bundle


def check(case: dict[str, Any]) -> dict[str, tuple[str, str]]:
    req, capture, bundle = _build(case)
    binding = R.invocation_binding(req)
    integ = R.judge_package(capture)
    corr = R.judge_correspondence(capture, binding, integ)
    eff = R.judge_effect(bundle, binding, corr)
    ex, obs = case["execution"], case.get("observation")
    out: dict[str, tuple[str, str]] = {}
    if eff["reason"] == "dispatched_after_deny":
        out["decision_correspondence"] = ("CONTRADICTED", "dispatched without an allow or an approval bound to the call")
    elif corr["result"] != "ESTABLISHED":
        out["decision_correspondence"] = (corr["result"], corr["reason"])
    elif corr["decision"] == "allow":
        out["decision_correspondence"] = ("ESTABLISHED", "allow bound to the call")
    else:
        out["decision_correspondence"] = ("NOT_ESTABLISHED", "deny bound to the call")
    if ex.get("executions") is None or ex.get("executed") is None:
        out["single_dispatch"] = ("NOT_ESTABLISHED", "execution count or dispatch evidence missing")
    elif ex["executions"] != 1:
        out["single_dispatch"] = ("CONTRADICTED", f"executed {ex.get('executions')} times")
    elif ex["executed"] is not True:
        out["single_dispatch"] = ("NOT_ESTABLISHED", "no dispatch recorded")
    else:
        out["single_dispatch"] = ("ESTABLISHED", "executed once")
    out["call_binding"] = (("CONTRADICTED", "the twin operation record belongs to another call")
                           if eff["reason"] == "bundle_bound_to_other_call" else ("ESTABLISHED", "twin operation bound to the call"))
    out["declared_effect_observed"] = (eff["result"], eff["reason"])
    op, claimed = case.get("twin_operation"), case["claim"]
    if not op or any(op.get(k) is None for k in ("operation_id", "zone_id", "requested_setpoint_c")):
        out["call_binding"] = ("NOT_ESTABLISHED", "operation binding evidence missing")
    elif op["requested_setpoint_c"] != claimed["setpoint_c"]:
        out["call_binding"] = ("CONTRADICTED", "the twin operation record belongs to another call")
    if op and op.get("status") == "APPLIED" and ex.get("executed") is False:
        out["decision_correspondence"] = ("CONTRADICTED", "APPLIED operation contradicts the no-dispatch record")
    if eff["result"] != "CONTRADICTED":
        if not op or op.get("status") is None or op.get("new_setpoint_c") is None:
            out["declared_effect_observed"] = ("NOT_ESTABLISHED", "applied status or actual value missing")
        elif op["status"] != "APPLIED" or op["new_setpoint_c"] != claimed["setpoint_c"]:
            out["declared_effect_observed"] = ("CONTRADICTED", "twin status or actual value contradicts the claimed effect")
        elif obs is None or obs.get("observed_setpoint_c") is None:
            out["declared_effect_observed"] = ("NOT_ESTABLISHED", "no observed value")
        elif obs.get("observed_setpoint_c") != claimed["setpoint_c"]:
            out["declared_effect_observed"] = ("CONTRADICTED", "raw observation contradicts the claimed effect")
    dispatch_time = ex.get("dispatched_at_sim_s")
    observation_time = (obs or {}).get("observed_at_sim_s")
    if dispatch_time is None or observation_time is None:
        out["observation_after_dispatch"] = ("NOT_ESTABLISHED", "dispatch or observation time missing")
    elif observation_time < dispatch_time:
        out["observation_after_dispatch"] = ("CONTRADICTED", "observed before the dispatch")
    else:
        out["observation_after_dispatch"] = ("ESTABLISHED", "observed at or after the dispatch")
    out["evidence_digest_integrity"] = (integ["result"], integ["reason"])
    return out


def verdict(claims: dict[str, tuple[str, str]], vantage: str) -> tuple[str, str, str | None]:
    bad = [c for c in ORDER if claims[c][0] == "CONTRADICTED"]
    if bad:
        return "CONTRADICTED", "; ".join(f"{c}: {claims[c][1]}" for c in bad), bad[0]
    open_ = [c for c in ORDER if claims[c][0] != "ESTABLISHED"]
    if open_:
        return "NOT_ESTABLISHED", "; ".join(f"{c}: {claims[c][1]}" for c in open_), open_[0]
    return "ESTABLISHED", f"six claims established; observation vantage recorded as {vantage}, which the reader does not count as independent", None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adapter")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--fixtures", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--strict-vantage", action="store_true",
                   help="count an observation as independent only when its recorded vantage says so")
    a = p.parse_args(argv)
    global STRICT
    STRICT = a.strict_vantage
    root = Path(a.fixtures)
    manifest = (root / "manifest.json").read_bytes()
    verdicts = []
    for path in sorted((root / "cases").glob("*.json")):
        case = json.loads(path.read_text("utf-8"))
        try:
            v, reason, claim_id = verdict(check(case), _vantage(case))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            # A case this adapter cannot read is not established; it never stops the run for the others.
            v, reason, claim_id = "NOT_ESTABLISHED", f"malformed case: {type(e).__name__}: {e}", None
        entry: dict[str, Any] = {"case_id": case.get("case_id", path.stem), "verdict": v, "reason": reason}
        if claim_id:
            entry["claim_id"] = claim_id
        verdicts.append(entry)
    result = {"schema": "ot-edge-result/v1", "edge_id": EDGE_ID,
              "fixtures_sha256": hashlib.sha256(manifest).hexdigest(), "verdicts": verdicts}
    Path(a.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
