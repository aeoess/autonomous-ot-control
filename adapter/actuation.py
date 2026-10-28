"""Actuator effect primitives used by the deployment-owned OT tool callable.

These are REMORA-free and offline-testable. The REMORA execution image's tool
registry module (``deploy/ot/ot_registry.py``) composes them into the callable
REMORA dispatches. Keeping the HTTP/effect logic here means it can be driven
against the real twin actuator app in unit tests without REMORA installed.
"""
from __future__ import annotations

from typing import Any

import httpx

from adapter.contracts import ToolSpec, ToolSpecError

ACTUATION_SCHEMA = "ot-actuation-command/v1"
PLANT_COMMAND_SCHEMA = "ot-plant-command/v1"
UNIT = "degC"


class ActuationError(RuntimeError):
    """A setpoint write was refused with zero (or no new) plant effect."""


class EffectUnknown(RuntimeError):
    """The actuator outcome is indeterminate. Never a retry permit (INV-05/INV-10)."""


class ActuatorClient:
    """Holds the twin's actuator credential and reaches the private write route.

    Constructed only inside the protected REMORA execution image, where
    ``TWIN_ACTUATOR_TOKEN`` is mounted (INV-02). No agent/runner/Worker process
    constructs it.
    """

    def __init__(self, base_url: str, actuator_token: str, *,
                 client: httpx.Client | None = None, timeout_s: float = 5.0) -> None:
        if not actuator_token or len(actuator_token) < 24:
            raise ActuationError("actuator token missing or too short")
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._headers = {"Authorization": f"Bearer {actuator_token}"}

    def post_setpoint(self, command: dict[str, Any]) -> httpx.Response:
        return self._client.post("/internal/v1/actuate/setpoint", json=command,
                                 headers=self._headers)

    def post_plant(self, command: dict[str, Any]) -> httpx.Response:
        return self._client.post("/internal/v1/actuate/plant", json=command,
                                 headers=self._headers)


def build_setpoint_command(operation_id: str, expected_state_revision: int,
                           canonical_args: dict[str, Any]) -> dict[str, Any]:
    """Assemble an ``ot-actuation-command/v1`` body.

    ``operation_id`` is REMORA's stable execution identity (its proposal id),
    not an agent-supplied value, so a REMORA re-invocation of the same proposal
    reuses the same idempotency key and the twin applies at most one effect
    (INV-04).
    """
    return {
        "schema": ACTUATION_SCHEMA,
        "operation_id": operation_id,
        "zone_id": canonical_args["zone_id"],
        "setpoint_c": canonical_args["setpoint_c"],
        "units": UNIT,
        "expected_state_revision": expected_state_revision,
    }


def apply_setpoint(actuator: ActuatorClient, toolspec: ToolSpec, *, operation_id: str,
                   expected_state_revision: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate against the signed contract and apply one governed setpoint.

    Returns the twin's effect receipt on an applied (or idempotently replayed)
    effect. Raises ``ActuationError`` when the contract or the local guard
    refuses — a refusal is not a successful effect and must not be recorded as
    one (MEDIUM: 409 is distinct from 200). Raises ``EffectUnknown`` when the
    outcome is indeterminate, which is never retried here.
    """
    try:
        canonical_args = toolspec.canonical_setpoint_arguments(arguments)
    except ToolSpecError as e:
        raise ActuationError(f"argument refused by signed contract: {e}") from None
    command = build_setpoint_command(operation_id, expected_state_revision, canonical_args)
    try:
        resp = actuator.post_setpoint(command)
    except httpx.HTTPError as e:
        raise EffectUnknown(f"actuator unreachable: {type(e).__name__}") from None
    if resp.status_code == 401:
        raise ActuationError("actuator rejected adapter credential")
    try:
        body = resp.json()
    except ValueError:
        raise EffectUnknown(f"actuator returned non-JSON HTTP {resp.status_code}") from None
    if resp.status_code == 200 and body.get("status") == "APPLIED":
        return body  # applied, or idempotent replay of a prior APPLIED (body.replayed)
    if resp.status_code == 409:
        reason = body.get("reason", "rejected")
        if reason == "operation_id_reused":
            # Same operation id, different request: a binding/replay conflict.
            raise ActuationError(f"operation_id_reused:{operation_id}")
        # Local guard refusal: zero plant change. Surface as a refusal.
        raise ActuationError(f"guard_refused:{reason}")
    raise EffectUnknown(f"actuator returned HTTP {resp.status_code}")


def _clean_for_digest(v: Any) -> Any:
    if isinstance(v, float) and v != v:
        return "NaN"
    if isinstance(v, float) and v in (float("inf"), float("-inf")):
        return str(v)
    if isinstance(v, dict):
        return {k: _clean_for_digest(val) for k, val in v.items()}
    return v


def setpoint_request_digest(command: dict[str, Any]) -> str:
    """The twin's request digest of an ``ot-actuation-command/v1`` (twin.model.Twin.request_digest)."""
    from twin.state import canonical_digest
    return canonical_digest({k: _clean_for_digest(command.get(k))
                             for k in ("zone_id", "setpoint_c", "units", "expected_state_revision")})


def plant_request_digest(command: dict[str, Any]) -> str:
    """The twin's request digest of an ``ot-plant-command/v1`` (twin.model.Twin.plant_request_digest)."""
    from twin.state import canonical_digest
    return canonical_digest({k: _clean_for_digest(command.get(k))
                             for k in ("tool_name", "arguments", "expected_state_revision")})


class OperationIdReused(ActuationError):
    """A stored operation answers a different call: never a replay, never a receipt (SDD-OT-002 SR-09)."""


def check_replay(prior: dict[str, Any], command: dict[str, Any], *, plant: bool) -> None:
    """Raise unless ``command``, at the revision the stored operation was bound to, is that operation's request.

    The digest is the twin's own, over the canonical arguments the adapter would send now, with the
    ``expected_state_revision`` the original request carried (``state_revision_before``).
    """
    bound = {**command, "expected_state_revision": prior.get("state_revision_before")}
    digest = plant_request_digest(bound) if plant else setpoint_request_digest(bound)
    if prior.get("request_digest") != digest:
        raise OperationIdReused("operation_id_reused: the stored operation was a different request")


def build_plant_command(operation_id: str, expected_state_revision: int,
                        tool_name: str, canonical_args: dict[str, Any]) -> dict[str, Any]:
    """Assemble an ``ot-plant-command/v1`` body bound to REMORA's proposal id."""
    return {
        "schema": PLANT_COMMAND_SCHEMA,
        "operation_id": operation_id,
        "tool_name": tool_name,
        "arguments": canonical_args,
        "expected_state_revision": expected_state_revision,
    }


def apply_plant_command(actuator: ActuatorClient, toolspec: ToolSpec, *, operation_id: str,
                        expected_state_revision: int, tool_name: str,
                        arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate against the signed contract and apply one governed plant command."""
    try:
        canonical_args = toolspec.canonical_tool_arguments(tool_name, arguments)
    except ToolSpecError as e:
        raise ActuationError(f"argument refused by signed contract: {e}") from None
    command = build_plant_command(operation_id, expected_state_revision, tool_name, canonical_args)
    try:
        resp = actuator.post_plant(command)
    except httpx.HTTPError as e:
        raise EffectUnknown(f"actuator unreachable: {type(e).__name__}") from None
    if resp.status_code == 401:
        raise ActuationError("actuator rejected adapter credential")
    try:
        body = resp.json()
    except ValueError:
        raise EffectUnknown(f"actuator returned non-JSON HTTP {resp.status_code}") from None
    if resp.status_code == 200 and body.get("status") == "APPLIED":
        return body
    if resp.status_code == 409:
        reason = body.get("reason", "rejected")
        if reason == "operation_id_reused":
            raise ActuationError(f"operation_id_reused:{operation_id}")
        raise ActuationError(f"guard_refused:{reason}")
    raise EffectUnknown(f"actuator returned HTTP {resp.status_code}")

