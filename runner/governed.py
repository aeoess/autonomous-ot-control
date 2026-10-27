"""Governed proposal runner: remora.sdk ToolCall client harness (OT-003).

This is the *untrusted* proposing side. It runs with a role-restricted operator
SDK token and the read token only — it holds **no** actuator credential and has
no route to the twin's write listener. It proposes a ``set_zone_setpoint`` call
to REMORA via ``remora.sdk`` and reacts to REMORA's decision. It never
self-approves and never dispatches an effect itself: the only path to the
actuator is REMORA's protected dispatcher and the deployment-owned callable in
``deploy/ot/ot_registry.py`` (INV-01, INV-02, INV-06). This substantiates the
OT-003 gate "zero direct effect from SDK-only actor".

The decision branching is a pure function (``decide``) so it is testable without
the SDK installed. Actual proposal/execution requires the pinned ``remora.sdk``
from the REMORA execution image; absent it, the runner fails closed with a clear
error rather than inventing an authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

TARGET_ENVIRONMENT = "simulated_pilot"
WRITE_TOOL = "set_zone_setpoint"

# Projection vocabulary (SDD §12). Not a competing REMORA lifecycle.
STATE_AUTHORIZED = "AUTHORIZED"
STATE_WAITING_APPROVAL = "WAITING_APPROVAL"
STATE_REFUSED = "REFUSED"
STATE_DISPATCHED = "DISPATCHED"
STATE_UNKNOWN = "UNKNOWN"

# Canonical REMORA decision actions. The runner branches on the action *name*
# so the logic needs no import of the SDK enum. Verified against
# remora.policy.report.DecisionAction at commit 2594d08b: the members are
# ACCEPT, VERIFY, ABSTAIN, ESCALATE (there is no REFUSE/DENY). Any unmapped
# action fails closed below.
ACTION_ACCEPT = "ACCEPT"
REVIEW_ACTIONS = frozenset({"VERIFY", "ESCALATE"})
REFUSE_ACTIONS = frozenset({"ABSTAIN"})


class GovernedError(RuntimeError):
    pass


class SDKUnavailable(GovernedError):
    """The pinned remora.sdk is not importable; governed dispatch cannot run."""


@dataclass(frozen=True)
class Decision:
    """What the runner should do with REMORA's assessment, and why."""
    state: str
    dispatch: bool
    action: str
    detail: str


def _action_name(assessment: Any) -> str:
    action = getattr(assessment, "action", None)
    if action is None:
        raise GovernedError("assessment has no action")
    # Accept an enum (``.name``/``.value``) or a plain string.
    name = getattr(action, "name", None) or getattr(action, "value", None) or action
    return str(name).upper()


def decide(assessment: Any) -> Decision:
    """Map a REMORA assessment to a runner action. Fails closed by default.

    ACCEPT is the only action that permits dispatch, and even then the dispatch
    is performed by REMORA's ``execute_accepted`` using REMORA's issued token and
    the exact proposed call — never by this runner directly. VERIFY/ESCALATE go
    to a separate reviewer principal with no dispatch. Anything else is refused.
    """
    name = _action_name(assessment)
    if name == ACTION_ACCEPT:
        return Decision(STATE_AUTHORIZED, True, name, "remora accepted; execute exact bound call")
    if name in REVIEW_ACTIONS:
        return Decision(STATE_WAITING_APPROVAL, False, name,
                        "routed to separate reviewer; runner never self-approves")
    if name in REFUSE_ACTIONS:
        return Decision(STATE_REFUSED, False, name, "remora refused; zero dispatch")
    # Unknown/unmapped action: fail closed, do not guess.
    return Decision(STATE_REFUSED, False, name, "unmapped decision action; failing closed")


def _require_sdk() -> Any:
    try:
        import remora.sdk as sdk  # noqa: PLC0415 - pinned deployment dependency
    except Exception as e:
        raise SDKUnavailable(
            "remora.sdk is not available. Governed dispatch requires the pinned "
            "REMORA execution image (upstream/remora.lock.json); this runner never "
            "authorizes or dispatches on its own.") from e
    return sdk


def propose_call(client: Any, *, tool_name: str, arguments: dict[str, Any], intent_ref: str,
                 sdk: Any | None = None) -> dict[str, Any]:
    """Propose one governed tool call to REMORA and act on its decision."""
    sdk = sdk if sdk is not None else _require_sdk()
    call = sdk.ToolCall(
        tool_name=tool_name,
        arguments=arguments,
        target_environment=TARGET_ENVIRONMENT,
        intent_ref=intent_ref,
    )
    assessment = client.assess(call)
    decision = decide(assessment)
    out: dict[str, Any] = {"state": decision.state, "action": decision.action,
                           "detail": decision.detail, "intent_ref": intent_ref,
                           "tool_name": tool_name, "arguments": arguments}
    if tool_name == WRITE_TOOL:
        out["zone_id"] = arguments.get("zone_id")
        out["requested_setpoint_c"] = arguments.get("setpoint_c")
    if not decision.dispatch:
        return out
    token = getattr(assessment, "execution_token", None)
    if not token:
        # ACCEPT without a usable token: refuse rather than fabricate one.
        return {**out, "state": STATE_REFUSED, "detail": "accept without execution token"}
    try:
        receipt = client.execute_accepted(token, call)
    except Exception as e:
        return {**out, "state": STATE_UNKNOWN,
                "detail": f"execution outcome unknown: {type(e).__name__}: {e}"}
    return {**out, "state": STATE_DISPATCHED, "receipt": receipt}


def propose_setpoint(client: Any, *, zone_id: str, setpoint_c: float, intent_ref: str,
                     sdk: Any | None = None) -> dict[str, Any]:
    """Propose one setpoint change to REMORA and act on its decision.

    ``client`` is a ``remora.sdk`` client (or a compatible object in tests). On
    ACCEPT, dispatch is delegated to ``client.execute_accepted`` with REMORA's
    execution token and the exact proposed call. Returns a projection dict; an
    exception from execution is reported as UNKNOWN and is **not** retried.
    """
    return propose_call(client, tool_name=WRITE_TOOL,
                        arguments={"zone_id": zone_id, "setpoint_c": setpoint_c},
                        intent_ref=intent_ref, sdk=sdk)


def main(argv: list[str] | None = None,
         propose: Callable[..., dict[str, Any]] = propose_setpoint) -> int:
    """CLI entrypoint. Requires a reachable REMORA service and operator token.

    Kept deliberately thin: the scored governed-vs-baseline run is OT-005 (PR 3).
    Here we only demonstrate a single governed proposal end to end.
    """
    import argparse
    import json
    import os

    p = argparse.ArgumentParser(prog="runner.governed")
    p.add_argument("--zone", default="ZONE-01")
    p.add_argument("--setpoint", type=float, required=True)
    p.add_argument("--intent-ref", required=True)
    a = p.parse_args(argv)

    sdk = _require_sdk()
    base_url = os.environ["REMORA_URL"]
    token = os.environ["REMORA_OPERATOR_TOKEN"]
    with sdk.RemoraClient(base_url=base_url, token=token) as client:
        result = propose(client, zone_id=a.zone, setpoint_c=a.setpoint,
                         intent_ref=a.intent_ref, sdk=sdk)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["state"] in (STATE_DISPATCHED, STATE_WAITING_APPROVAL) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
