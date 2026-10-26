"""Deployment-owned OT tool callables and the REMORA registration seam (OT-003).

What this is
------------
REMORA's execution server dispatches *deployment-owned callables* through its
internal ``GovernedToolDispatcher`` after it has authorized a call. This module
provides those callables for the three frozen v1 OT tools and a ``register()``
entrypoint that REMORA discovers via its deployment registry hook
(``REMORA_TOOL_REGISTRY_MODULE``, see ``upstream/remora.lock.json`` and
``docs/integration-contracts.md``).

What this is NOT
----------------
It is not an authorization engine. The callables run only *after* REMORA has
authorized and bound the call; they receive the binding as an ``ExecutionContext``
that REMORA constructs (tenant, principal, single-use operation id, the signed
ToolSpec digest, the expected plant revision). They never read authority labels
from agent input, never mint a grant/lease, and never retry a side effect on
``UNKNOWN`` (INV-03, INV-04, INV-05, INV-10).

Integration risk (D-03): the exact shape of REMORA's registrar at the pinned
commit must be reconciled by an integration test against the running execution
image before the Docker image claim is promoted beyond ``IMPLEMENTED``. The
``Registrar`` protocol below is the minimal contract this adapter depends on;
it is duck-typed so the real REMORA object or a test double both satisfy it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from adapter.contracts import WRITE_TOOL, ToolSpec, ToolSpecError

ACTUATION_SCHEMA = "ot-actuation-command/v1"
UNIT = "degC"


class AdapterError(RuntimeError):
    """A deployment-owned callable refused or could not complete a call."""


class EffectUnknown(AdapterError):
    """The actuator outcome is indeterminate. Never a retry permit (INV-05/INV-10)."""


@dataclass(frozen=True)
class ExecutionContext:
    """Binding supplied by REMORA's dispatcher at execute time.

    Every field originates in the REMORA control plane, never in agent input
    (INV-03). ``operation_id`` is REMORA's single-use execution identity and is
    used verbatim as the twin's idempotency key so a replay cannot create a
    second effect (INV-04).
    """
    tenant_id: str
    principal_id: str
    operation_id: str
    toolspec_digest: str
    policy_version: str
    expected_state_revision: int

    def __post_init__(self) -> None:
        for name in ("tenant_id", "principal_id", "operation_id", "toolspec_digest",
                     "policy_version"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v:
                raise AdapterError(f"execution context missing {name}")
        if not isinstance(self.expected_state_revision, int) or self.expected_state_revision < 0:
            raise AdapterError("execution context has invalid expected_state_revision")


class ActuatorClient:
    """Holds the twin's actuator credential and reaches the private write route.

    This object is constructed only inside the protected REMORA execution image,
    where ``TWIN_ACTUATOR_TOKEN`` is mounted (INV-02). No agent/runner/Worker
    process constructs it, and it is never handed a caller-supplied identity.
    """

    def __init__(self, base_url: str, actuator_token: str, *,
                 client: httpx.Client | None = None, timeout_s: float = 5.0) -> None:
        if not actuator_token or len(actuator_token) < 24:
            raise AdapterError("actuator token missing or too short")
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._headers = {"Authorization": f"Bearer {actuator_token}"}

    def actuate(self, command: dict[str, Any]) -> httpx.Response:
        return self._client.post("/internal/v1/actuate/setpoint", json=command,
                                 headers=self._headers)


def build_setpoint_command(ctx: ExecutionContext, canonical_args: dict[str, Any]) -> dict[str, Any]:
    """Assemble the ``ot-actuation-command/v1`` body from the bound context.

    The operation id and expected revision come from REMORA's context, not from
    the agent; the arguments are the canonical form validated against the signed
    ToolSpec.
    """
    return {
        "schema": ACTUATION_SCHEMA,
        "operation_id": ctx.operation_id,
        "zone_id": canonical_args["zone_id"],
        "setpoint_c": canonical_args["setpoint_c"],
        "units": UNIT,
        "expected_state_revision": ctx.expected_state_revision,
    }


def set_zone_setpoint(ctx: ExecutionContext, arguments: dict[str, Any], *,
                      actuator: ActuatorClient, toolspec: ToolSpec) -> dict[str, Any]:
    """Deployment-owned WRITE callable: apply one governed setpoint change.

    Fails closed on any contract violation or digest mismatch (INV-05). Returns
    the twin's effect receipt (``effect-receipt-v1``) augmented with the bound
    identity. A duplicate ``operation_id`` returns the stored receipt without
    re-applying (INV-04); an indeterminate outcome raises ``EffectUnknown`` and
    is *not* retried here.
    """
    if ctx.toolspec_digest != toolspec.digest:
        raise AdapterError("toolspec digest mismatch; refusing to dispatch")
    try:
        canonical_args = toolspec.canonical_setpoint_arguments(arguments)
    except ToolSpecError as e:
        raise AdapterError(f"argument refused by signed contract: {e}") from None
    command = build_setpoint_command(ctx, canonical_args)
    try:
        resp = actuator.actuate(command)
    except httpx.HTTPError as e:
        # No acknowledgement: the effect may or may not have happened. Report
        # UNKNOWN for REMORA to reconcile via the observer; never auto-retry.
        raise EffectUnknown(f"actuator unreachable: {type(e).__name__}") from None
    if resp.status_code in (200, 409):
        receipt = resp.json()
        return {
            **receipt,
            "tenant_id": ctx.tenant_id,
            "principal_id": ctx.principal_id,
            "toolspec_digest": toolspec.digest,
            "policy_version": ctx.policy_version,
        }
    if resp.status_code == 401:
        raise AdapterError("actuator rejected adapter credential")
    raise EffectUnknown(f"actuator returned HTTP {resp.status_code}")


# -- registration seam -------------------------------------------------------

@runtime_checkable
class Registrar(Protocol):
    """Minimal contract this adapter needs from REMORA's deployment registrar.

    The real REMORA object at the pinned commit must be reconciled against this
    shape by an integration test (D-03); a test double implements the same
    method for offline unit tests.
    """

    def register_tool(self, *, tool_name: str, effect: str, callable: Any,
                      spec: dict[str, Any]) -> None: ...


def register(registrar: Registrar, *, toolspec: ToolSpec,
             actuator: ActuatorClient | None = None) -> list[str]:
    """Register exactly the frozen v1 OT tools with REMORA's execution server.

    Only ``set_zone_setpoint`` is a WRITE and only it is given the
    actuator-backed callable. The read tools are declared for completeness; the
    simulator's read path already serves them with a read-only identity, so they
    carry no actuator credential. Returns the registered tool names.

    This does not create policy, grants or leases — REMORA owns those (INV-11).
    """
    names: list[str] = []
    for tool in toolspec.raw["tools"]:
        name = tool["tool_name"]
        spec = {"toolspec_digest": toolspec.digest, "policy_version": toolspec.policy_version,
                "arguments": tool.get("arguments", {}), "limits": tool.get("limits")}
        if name == WRITE_TOOL:
            if actuator is None:
                raise AdapterError("write tool registration requires an actuator client")

            def _write(ctx: ExecutionContext, arguments: dict[str, Any],
                       _actuator: ActuatorClient = actuator,
                       _toolspec: ToolSpec = toolspec) -> dict[str, Any]:
                return set_zone_setpoint(ctx, arguments, actuator=_actuator, toolspec=_toolspec)

            registrar.register_tool(tool_name=name, effect="WRITE", callable=_write, spec=spec)
        else:
            registrar.register_tool(tool_name=name, effect="READ", callable=None, spec=spec)
        names.append(name)
    return names
