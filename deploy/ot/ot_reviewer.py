"""The reviewer (SDD-OT-002 SR-06, F-06): the only process that holds REMORA's APPROVER credential.

    docker compose exec -T reviewer python /app/ot_reviewer.py <op> '<json>'

Before SR-06 the harness held the operator and the approver token, so the process that proposed a change could
also approve it. Now the approver token lives here only, in its own container (the REMORA execution image),
beside REMORA's viewer token, the twin's read token and the work-order authority's public key.

Two ways to use it:

* ``approve`` / ``reject`` / ``sdk_approve``: the acceptance suite's tester, a human role, says what to approve.
  The suite plays proposer and tester from one driver, and says so; the credential still never sits with the
  proposer's token.
* ``decide``: the independent reviewer model of the A/B/C. The proposer names the item, the proposal and the call;
  the reviewer believes none of it until REMORA's own record of the assessment (the ``assessed`` event, read with
  the viewer token) binds them: the record names this item, its ``tool_call_hash`` is the hash of this call
  (REMORA's own function), and its ``intent_authority_hash`` is the hash of the work order this reviewer resolves
  for the call's ``intent_ref`` (REMORA's bundle and hash, run here). Then it approves only when all of these hold:

  1. the call's ``intent_ref`` names a work order signed by the work-order authority (``adapter.work_orders``);
  2. the work order is valid on the twin's current simulated day (``valid_on``);
  3. the work order permits this value for this zone (``permits``);
  4. the step from the zone's current setpoint, read from the twin, is inside the signed contract's limit.

  Otherwise it rejects the item, with the first reason that failed. It knows no strategy and no proposer state,
  so it approves an in-plan step the strategy would not take; that is the point of keeping it independent.

Every op prints one JSON object; errors come back as data.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx

ROLES = {"approver": "REMORA_APPROVER_TOKEN", "viewer": "REMORA_VIEWER_TOKEN"}
TIMEOUT_S = 30.0
TOOLSPEC = Path(os.environ.get("OT_TOOLSPEC_PATH", "/app/contracts/ot-toolspec-v1.json"))
TENANT = os.environ.get("OT_TENANT", "ot-pilot")


def _remora(method: str, path: str, role: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        r = httpx.request(method, os.environ["REMORA_URL"] + path, json=body, timeout=TIMEOUT_S,
                          headers={"Authorization": f"Bearer {os.environ[ROLES[role]]}"})
    except httpx.HTTPError as e:
        return {"ok": False, "status": None, "error": type(e).__name__,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
    try:
        payload: Any = r.json()
    except ValueError:
        payload = {"raw": r.text[:500]}
    return {"ok": r.is_success, "status": r.status_code, "body": payload,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}


def _twin(path: str) -> dict[str, Any]:
    r = httpx.get(os.environ["TWIN_READ_URL"] + path, timeout=TIMEOUT_S,
                  headers={"Authorization": f"Bearer {os.environ['TWIN_READ_TOKEN']}"})
    r.raise_for_status()
    return r.json()


def op_approve(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("POST", "/v1/execution/approve", "approver",
                   {"item_id": a["item_id"], "approval_ttl_seconds": a.get("ttl", 900)})


def op_reject(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("POST", "/v1/execution/reject", "approver",
                   {"item_id": a["item_id"], "reason": a.get("reason", "acceptance")})


def op_sdk_approve(a: dict[str, Any]) -> dict[str, Any]:
    """Approve through remora.sdk, to check the typed client, not only REST."""
    import remora.sdk as sdk

    with sdk.RemoraClient(base_url=os.environ["REMORA_URL"], token=os.environ[ROLES["approver"]]) as c:
        try:
            res = c.approve(a["item_id"])
        except Exception as e:  # reported as data: the point is what the SDK does
            return {"ok": False, "error": type(e).__name__, "detail": str(e)[:300]}
    return {"ok": True, "status_text": res.status, "audit_deferred": getattr(res.audit, "deferred", None)}


def bound(assessed: dict[str, Any], item_id: str, call: dict[str, Any], call_hash: Callable[..., str],
          intent_hash: Callable[[str], str | None]) -> tuple[bool, str]:
    """Whether REMORA's record of the assessment binds this item, this call and this work order."""
    if assessed.get("review_item_id") != item_id:
        return False, "REMORA's record of the proposal names another review item"
    try:
        h = call_hash(name=str(call.get("tool_name")), arguments=call.get("arguments"), tenant=TENANT,
                      target=assessed.get("target_environment"))
    except (TypeError, ValueError):
        return False, "the call has no canonical form"
    if h != assessed.get("tool_call_hash"):
        return False, "the call is not the one REMORA assessed"
    ref = call.get("intent_ref")
    if ref:
        if assessed.get("intent_resolution_status") != "resolved" or intent_hash(ref) != assessed.get("intent_authority_hash"):
            return False, f"{ref} is not the work order REMORA resolved for this proposal"
    return True, "bound"


def judge(call: dict[str, Any], intents: dict[str, Any], trusted: dict[str, str],
          sim_day: str, current_setpoint: Callable[[str], float], max_delta_c: float) -> tuple[bool, str]:
    """The independent model's verdict on one (bound) call, from the reviewer's own sources only."""
    from adapter.work_orders import verify_entry

    tool, args = call.get("tool_name"), call.get("arguments") or {}
    ref = call.get("intent_ref")
    if not ref:
        return False, "no work order"
    entry = intents.get(ref)
    kid, why = verify_entry(ref, entry, trusted)
    if kid is None:
        return False, f"{ref} is not a work order signed by the authority ({why})"
    if entry.get("valid_on") != sim_day:
        return False, f"{ref} is valid on {entry.get('valid_on')}, not on {sim_day}"
    zone, value = args.get("zone_id"), args.get("setpoint_c")
    permitted = ((entry.get("permits") or {}).get(tool) or {}).get(zone)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not permitted \
            or not any(abs(float(value) - float(v)) < 1e-9 for v in permitted):
        return False, f"{ref} does not permit {value!r} for {zone} with {tool}"
    step = abs(float(value) - current_setpoint(zone))
    if step > max_delta_c + 1e-9:
        return False, f"a {step:.1f} degC step exceeds the contract's {max_delta_c}"
    return True, f"{ref} (signed {kid}) permits {value} on {zone} today; step {step:.1f} degC"


def _intent_hash(ref: str) -> str | None:
    """REMORA's hash of the work order its bundle resolves for ``ref``, computed here with the same code."""
    sys.path.insert(0, "/app")
    import ot_bundle
    from remora.toolcall.semantic_bundle import compute_intent_authority_hash
    resolved = ot_bundle.resolve_intent(ref)
    return compute_intent_authority_hash(resolved) if resolved is not None else None


def op_decide(a: dict[str, Any]) -> dict[str, Any]:
    from adapter.work_orders import load_trusted
    from remora.policy.observation import canonical_tool_call_hash

    item, pid, call = a["item_id"], a["proposal_id"], a.get("call") or {}
    lc = _remora("GET", f"/v1/execution/proposals/{pid}/lifecycle", "viewer")
    if not lc["ok"]:
        return {"ok": False, "approved": False, "reason": f"proposal unreadable ({lc.get('status')})"}
    try:
        assessed = next(e["payload"] for e in lc["body"]["events"] if e.get("event") == "assessed")
        ok, reason = bound(assessed, item, call, canonical_tool_call_hash, _intent_hash)
        if ok:
            intents = json.loads(Path(os.environ["REMORA_INTENT_SOURCE_FILE"]).read_text("utf-8"))
            trusted = load_trusted(Path(os.environ["REMORA_OT_WORK_ORDER_KEYS"]))
            sim_day = str(_twin("/v1/status")["sim_utc"])[:10]
            spec = json.loads(TOOLSPEC.read_text("utf-8"))
            max_delta = float(next(t for t in spec["tools"]
                                   if t["tool_name"] == "set_zone_setpoint")["limits"]["max_delta_c"])
            ok, reason = judge(call, intents, trusted, sim_day,
                               lambda z: float(_twin(f"/v1/zones/{z}")["setpoint_c"]), max_delta)
    except (OSError, ValueError, KeyError, StopIteration, httpx.HTTPError) as e:
        ok, reason = False, f"reviewer could not check its sources: {type(e).__name__}"
    act = op_approve({"item_id": item}) if ok else op_reject({"item_id": item, "reason": f"independent reviewer: {reason}"})
    return {"ok": True, "approved": ok and bool(act.get("ok")), "reason": reason, "model": "independent",
            "action": {"op": "approve" if ok else "reject", "status": act.get("status"), "ok": act.get("ok")}}


def op_unresolved(a: dict[str, Any]) -> dict[str, Any]:
    """Limit operations whose outcome is unknown (review capability)."""
    return _remora("GET", "/v1/execution/limits/unresolved", "approver")


def op_resolve(a: dict[str, Any]) -> dict[str, Any]:
    """Settle an unknown limit operation; REMORA checks the evidence against the plant first (AT-12)."""
    return _remora("POST", "/v1/execution/limits/resolve", "approver",
                   {"operation_id": a["operation_id"], "executed": a["executed"],
                    "evidence_ref": a["evidence_ref"], "reason": a.get("reason", "acceptance")})


def op_env(a: dict[str, Any]) -> dict[str, Any]:
    """Which credentials this container holds, by name only."""
    names = ("REMORA_OPERATOR_TOKEN", "REMORA_APPROVER_TOKEN", "REMORA_VIEWER_TOKEN", "REMORA_VERIFIER_TOKEN",
             "TWIN_READ_TOKEN", "TWIN_ACTUATOR_TOKEN", "TWIN_CONTROL_TOKEN")
    return {"ok": True, "present": sorted(n for n in names if os.environ.get(n))}


OPS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    name[3:]: fn for name, fn in globals().items() if name.startswith("op_") and callable(fn)
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in OPS:
        print(json.dumps({"ok": False, "error": "unknown_op", "ops": sorted(OPS)}))
        return 2
    args = json.loads(argv[1]) if len(argv) > 1 else {}
    print(json.dumps(OPS[argv[0]](args), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
