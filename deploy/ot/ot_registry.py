"""Deployment-owned REMORA tool registry for the OT setpoint pilot.

Loaded inside the protected REMORA execution image via
``REMORA_TOOL_REGISTRY_MODULE=ot_registry`` (see ``servers/execution_api.py`` and
``servers/tool_registry_research.py`` at the pinned commit). REMORA imports this
module once and calls ``register_tools(register)``; each registered callable has
the shape ``fn(arguments: dict) -> dict`` and closes over its credentials
app-side, exactly like ``deploy/ot-pilot/ot_registry.py`` upstream. Request
payloads can never add or replace a callable or its credentials.

Only ``set_zone_setpoint`` writes, and only it holds the twin actuator client.
The write callable binds REMORA's own proposal id (from ``CURRENT_PROPOSAL_ID``)
as the twin's single-use operation id so a REMORA re-invocation of the same
proposal cannot produce a second effect (INV-04, INV-10). It constructs no
grant, lease or policy — REMORA authorizes (INV-11).

The same module also supplies the cumulative-limits hooks REMORA calls when
limits are enabled: ``observe_limit_value`` (the plant's own value, for the
``observed`` meters) and ``check_limit_evidence`` (resolve an UNKNOWN outcome
from the twin's operation log, never on trust).

This file depends only on the REMORA-free ``adapter`` helpers plus ``httpx`` and
stdlib, so it imports offline for unit tests; ``CURRENT_PROPOSAL_ID`` is imported
lazily and may be injected in tests.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

import httpx

from adapter.actuation import ActuationError, ActuatorClient, EffectUnknown, apply_setpoint
from adapter.contracts import ToolSpec

READ_TIMEOUT_S = 5.0
# REMORA proposal ids are UUIDs; nothing else goes into a twin operation URL.
_OPERATION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _current_proposal_id() -> str:
    """REMORA's stable proposal id for the in-flight request, or fail closed.

    Read from ``servers.execution_api.CURRENT_PROPOSAL_ID`` — a version-pinned
    deployment integration (D-03). Imported lazily so this module loads without
    REMORA (tests inject an id instead).
    """
    try:
        from servers.execution_api import CURRENT_PROPOSAL_ID
    except Exception as e:  # pragma: no cover - only in the REMORA image
        raise ActuationError("CURRENT_PROPOSAL_ID unavailable; cannot bind operation id") from e
    holder = CURRENT_PROPOSAL_ID.get()
    proposal_id = (holder or {}).get("id") if isinstance(holder, dict) else None
    if not proposal_id:
        raise ActuationError("no proposal id bound to this request; refusing to dispatch")
    return str(proposal_id)


class OtTools:
    """The three frozen v1 tool callables, closing over their clients."""

    def __init__(self, *, actuator: ActuatorClient, read_base_url: str, read_token: str,
                 toolspec: ToolSpec, read_client: httpx.Client | None = None,
                 proposal_id: Callable[[], str] = _current_proposal_id) -> None:
        self._actuator = actuator
        self._toolspec = toolspec
        self._proposal_id = proposal_id
        self._read = read_client or httpx.Client(base_url=read_base_url, timeout=READ_TIMEOUT_S)
        self._read_headers = {"Authorization": f"Bearer {read_token}"}

    # -- read tools --------------------------------------------------------
    def read_zone_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        zone_id = arguments.get("zone_id")
        if zone_id not in self._toolspec.zones:
            raise ValueError(f"unknown zone {zone_id!r}")
        r = self._read.get(f"/v1/zones/{zone_id}", headers=self._read_headers)
        r.raise_for_status()
        return r.json()

    def read_building_energy(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments:
            raise ValueError("read_building_energy takes no arguments")
        r = self._read.get("/v1/energy", headers=self._read_headers)
        r.raise_for_status()
        return r.json()

    # -- write tool --------------------------------------------------------
    def _current_revision(self) -> int:
        r = self._read.get("/v1/status", headers=self._read_headers)
        r.raise_for_status()
        return int(r.json()["state_revision"])

    def _prior_effect(self, operation_id: str) -> dict[str, Any] | None:
        """The stored outcome for this proposal id, if it already ran."""
        r = self._read.get(f"/v1/operations/{operation_id}", headers=self._read_headers)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def set_zone_setpoint(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply one governed setpoint change (the only WRITE tool).

        Fails closed (raises) on any contract or guard refusal so REMORA records
        an error, not a successful effect. The operation id is REMORA's proposal
        id: a re-invocation of the same proposal finds the stored outcome in the
        twin operation log and returns it without re-applying (INV-04/INV-10), so
        the bound revision never has to be reconstructed.
        """
        operation_id = self._proposal_id()
        try:
            prior = self._prior_effect(operation_id)
            if prior is not None:
                if prior.get("status") != "APPLIED":
                    raise ActuationError(f"prior_outcome:{prior.get('reason') or prior.get('status')}")
                return {**prior, "replayed": True}
            revision = self._current_revision()
        except httpx.HTTPError as e:
            raise EffectUnknown(f"could not read plant state: {type(e).__name__}") from None
        return apply_setpoint(self._actuator, self._toolspec, operation_id=operation_id,
                              expected_state_revision=revision, arguments=arguments)


# -- env wiring --------------------------------------------------------------

def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise ActuationError(f"{name} is required for the OT tool registry")
    return v


def build_from_env() -> OtTools:
    toolspec = ToolSpec.load(os.environ.get("OT_TOOLSPEC_PATH") or None)
    actuator = ActuatorClient(_require_env("OT_TWIN_ACTUATOR_URL"),
                              _require_env("TWIN_ACTUATOR_TOKEN"))
    return OtTools(actuator=actuator, read_base_url=_require_env("OT_TWIN_READ_URL"),
                   read_token=_require_env("TWIN_READ_TOKEN"), toolspec=toolspec)


_TOOLS: OtTools | None = None


def _tools() -> OtTools:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = build_from_env()
    return _TOOLS


def register_tools(register: Callable[[str, Callable[[Any], Any]], None]) -> None:
    tools = _tools()
    register("read_zone_state", tools.read_zone_state)
    register("read_building_energy", tools.read_building_energy)
    register("set_zone_setpoint", tools.set_zone_setpoint)


# -- cumulative-limits hooks (REMORA_LIMITS_OBSERVER_MODULE / _EVIDENCE_MODULE) --

def observe_limit_value(tenant: str, quantity: str, resource: str) -> float:
    """The plant's own value for an ``observed`` meter (setpoint movement)."""
    if quantity != "setpoint_movement":
        raise KeyError(f"no observation for {quantity!r}")
    tools = _tools()
    # REMORA may call this with the agent's own argument (the limit preview at
    # assess ran before the signed contract until REMORA #1101). Only a zone
    # in the signed contract is ever put into a twin URL.
    if resource not in tools._toolspec.zones:
        raise KeyError(f"no observation for resource {resource!r}")
    r = tools._read.get(f"/v1/zones/{resource}", headers=tools._read_headers)
    r.raise_for_status()
    return float(r.json()["setpoint_c"])


def check_limit_evidence(tenant: str, operation: Any, evidence_ref: str) -> dict[str, Any]:
    """Resolve an UNKNOWN setpoint operation from the twin's operation log.

    ``evidence_ref`` is ``twin-op:<operation_id>``. The answer is whether the
    twin recorded that operation as APPLIED. A missing record is ``executed:
    False`` (the reservation is returned), never a guess.
    """
    kind, _, operation_id = evidence_ref.partition(":")
    if kind != "twin-op" or not _OPERATION_ID.fullmatch(operation_id):
        raise ValueError(f"evidence_ref {evidence_ref!r} is not twin-op:<operation_id>")
    tools = _tools()
    r = tools._read.get(f"/v1/operations/{operation_id}", headers=tools._read_headers)
    if r.status_code == 404:
        return {"executed": False, "source": "twin-operation-log"}
    r.raise_for_status()
    return {"executed": r.json().get("status") == "APPLIED", "source": "twin-operation-log"}
