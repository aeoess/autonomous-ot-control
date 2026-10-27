"""One acceptance step, executed inside the `harness` container (OT-005).

The host driver (``runner/acceptance.py``) runs

    docker compose exec -T harness python -m runner.harness_step <op> '<json>'

and reads one JSON object from stdout. Each op is a single REMORA SDK call, a
single raw REST call, or a single twin read, made with exactly one role's
credential, so a judged result always names who did what. Errors come back as
data (``ok: false`` with the error type and HTTP status), never as a crash, so
a refusal is evidence rather than a broken step.

The container holds the operator, approver and viewer tokens and the twin read
token. It has no actuator or control token and no plant-private route.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable

import httpx

ROLES = {"operator": "REMORA_OPERATOR_TOKEN", "approver": "REMORA_APPROVER_TOKEN",
         "viewer": "REMORA_VIEWER_TOKEN"}
TIMEOUT_S = 30.0


def _token(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    return os.environ[ROLES[role]]


def _remora(method: str, path: str, role: str, body: dict[str, Any] | None = None,
            *, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    started = time.monotonic()
    try:
        r = httpx.request(method, os.environ["REMORA_URL"] + path, json=body, timeout=timeout,
                          headers={"Authorization": f"Bearer {_token(role)}"})
    except httpx.HTTPError as e:
        return {"ok": False, "status": None, "error": type(e).__name__,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
    try:
        payload: Any = r.json()
    except ValueError:
        payload = {"raw": r.text[:500]}
    return {"ok": r.is_success, "status": r.status_code, "body": payload,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}


def _call(args: dict[str, Any]) -> dict[str, Any]:
    call = {"tool_name": args["tool_name"], "arguments": args["arguments"],
            "target_environment": args.get("target_environment", "simulated_pilot")}
    if args.get("intent_ref"):
        call["intent_ref"] = args["intent_ref"]
    return call


def op_assess(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("POST", "/v1/execution/assess", a.get("role", "operator"), _call(a))


def op_approve(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("POST", "/v1/execution/approve", a.get("role", "approver"),
                   {"item_id": a["item_id"], "approval_ttl_seconds": a.get("ttl", 900)})


def op_reject(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("POST", "/v1/execution/reject", a.get("role", "approver"),
                   {"item_id": a["item_id"], "reason": a.get("reason", "acceptance")})


def op_execute(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("POST", "/v1/execution/execute", a.get("role", "operator"),
                   {"item_id": a["item_id"], "tool_call": _call(a)})


def op_execute_accepted(a: dict[str, Any]) -> dict[str, Any]:
    """Redeem an ACCEPT token with the exact assessed call."""
    return _remora("POST", "/v1/execution/execute-accepted", a.get("role", "operator"),
                   {"execution_token": a["execution_token"], "tool_call": _call(a)})


def op_proposal(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("GET", f"/v1/execution/proposals/{a['proposal_id']}", a.get("role", "viewer"))


def op_envelope(a: dict[str, Any]) -> dict[str, Any]:
    """REMORA's DecisionEnvelope for one proposal."""
    return _remora("GET", f"/v1/execution/proposals/{a['proposal_id']}/envelope",
                   a.get("role", "viewer"))


def op_evidence(a: dict[str, Any]) -> dict[str, Any]:
    """REMORA's evidence bundle (sections + hashed manifest), verbatim."""
    return _remora("GET", f"/v1/execution/proposals/{a['proposal_id']}/evidence",
                   a.get("role", "viewer"))


def op_record_effect(a: dict[str, Any]) -> dict[str, Any]:
    """Observe the effect through the read-only observer and record it in REMORA."""
    import remora.sdk as sdk

    from adapter.verifier import EffectAttestor
    from twin.effect_observer import EffectObserver

    observer = EffectObserver(os.environ["TWIN_READ_URL"], os.environ["TWIN_READ_TOKEN"],
                              tenant_id="ot-pilot", run_id=a.get("run_id", "demo"))
    with sdk.RemoraClient(base_url=os.environ["REMORA_URL"], token=_token("operator")) as c:
        try:
            out = EffectAttestor(observer, sdk=sdk).attest(
                proposal_id=a["proposal_id"], execution_id=a.get("execution_id") or a["proposal_id"],
                toolspec_hash=a.get("toolspec_hash", ""), zone_id=a["zone_id"],
                expected_setpoint_c=float(a["setpoint_c"]), operation_id=a["proposal_id"], client=c)
        except Exception as e:  # reported as data
            return {"ok": False, "error": type(e).__name__, "detail": str(e)[:300]}
    v = out.get("verification") or {}
    return {"ok": bool(out.get("remora_recorded")), "effect_status": v.get("status"),
            "observation": out.get("observation"), "record_error": out.get("remora_record_error")}


def op_lifecycle(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("GET", f"/v1/execution/proposals/{a['proposal_id']}/lifecycle",
                   a.get("role", "viewer"))


def op_audit_verify(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("GET", "/v1/execution/audit/verify", a.get("role", "viewer"))


def op_unresolved(a: dict[str, Any]) -> dict[str, Any]:
    return _remora("GET", "/v1/execution/limits/unresolved", a.get("role", "approver"))


def op_ready(a: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        r = httpx.get(os.environ["REMORA_URL"] + "/v1/ready", timeout=5.0)
        return {"ok": r.is_success, "status": r.status_code,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
    except httpx.HTTPError as e:
        return {"ok": False, "status": None, "error": type(e).__name__}


def _twin(path: str) -> dict[str, Any]:
    try:
        r = httpx.get(os.environ["TWIN_READ_URL"] + path, timeout=5.0,
                      headers={"Authorization": f"Bearer {os.environ['TWIN_READ_TOKEN']}"})
    except httpx.HTTPError as e:
        return {"ok": False, "status": None, "error": type(e).__name__}
    return {"ok": r.is_success, "status": r.status_code,
            "body": r.json() if r.content else None}


def op_energy(a: dict[str, Any]) -> dict[str, Any]:
    return _twin("/v1/energy")


def op_zone(a: dict[str, Any]) -> dict[str, Any]:
    return _twin(f"/v1/zones/{a['zone_id']}")


def op_status(a: dict[str, Any]) -> dict[str, Any]:
    return _twin("/v1/status")


def op_operation(a: dict[str, Any]) -> dict[str, Any]:
    return _twin(f"/v1/operations/{a['operation_id']}")


def op_direct_actuate(a: dict[str, Any]) -> dict[str, Any]:
    """AT-05: the harness tries the actuator itself. It must find no route."""
    try:
        r = httpx.post(os.environ["TWIN_ACTUATOR_PROBE_URL"] + "/internal/v1/actuate/setpoint",
                       timeout=3.0, json={"schema": "ot-actuation-command/v1",
                                          "operation_id": "harness-direct-0001",
                                          "zone_id": "ZONE-01", "setpoint_c": 22.0,
                                          "units": "degC", "expected_state_revision": 0})
        return {"ok": False, "status": r.status_code, "reachable": True}
    except httpx.HTTPError as e:
        return {"ok": True, "status": None, "reachable": False, "error": type(e).__name__}


def op_sdk_approve(a: dict[str, Any]) -> dict[str, Any]:
    """Approve through remora.sdk, to check the typed client, not only REST."""
    import remora.sdk as sdk

    with sdk.RemoraClient(base_url=os.environ["REMORA_URL"], token=_token("approver")) as c:
        try:
            res = c.approve(a["item_id"])
        except Exception as e:  # reported as data: the point is what the SDK does
            return {"ok": False, "error": type(e).__name__, "detail": str(e)[:300]}
    return {"ok": True, "status_text": res.status,
            "audit_deferred": getattr(res.audit, "deferred", None)}


def op_env(a: dict[str, Any]) -> dict[str, Any]:
    """Which credentials this container holds, by name only (INV-02)."""
    names = ("TWIN_ACTUATOR_TOKEN", "TWIN_CONTROL_TOKEN", "TWIN_READ_TOKEN",
             *ROLES.values())
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
