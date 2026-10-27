# Deployment-owned semantic bundle for the OT setpoint pilot.
#
# Loaded inside the REMORA execution image via REMORA_SEMANTIC_BUNDLE_MODULE=ot_bundle.
# Modeled on the upstream reference deploy/ot-pilot/ot_bundle.py at the pinned
# commit; it declares the three frozen v1 OT tools, their contracts and the
# plant's state index so REMORA's assess path can judge tool-matches-goal and
# argument grounding. Unsetting REMORA_SEMANTIC_BUNDLE_MODULE falls back to the
# registry-only assess path (SemanticAssessment: empty hashes), which still
# governs via policy — the bundle strengthens it, it is not the authority.
#
# This module imports REMORA's routing types, so it is importable only inside the
# REMORA image. It is intentionally kept out of the offline-tested `adapter`
# package.
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from remora.toolcall.routing.compatibility import CoverageScope, StateIndex
from remora.toolcall.routing.goal_match import TaskIntent
from remora.toolcall.routing.tool_contract import ToolContract, ToolContractRegistry
from remora.toolcall.routing.tool_registry import ToolRegistry, ToolSignature
from remora.toolcall.semantic_bundle import ResolvedIntent, SemanticBundle

ZONES = ("ZONE-01", "ZONE-02", "ZONE-03")
CIRCUITS = ("CABLE-RAMP", "CABLE-GUTTER")


def build_semantic_bundle() -> SemanticBundle:
    registry = ToolRegistry(signatures={
        "read_zone_state": ToolSignature(name="read_zone_state", effect="read",
                                         required_params=("zone_id",)),
        "read_building_energy": ToolSignature(name="read_building_energy", effect="read",
                                              required_params=()),
        "set_zone_setpoint": ToolSignature(name="set_zone_setpoint", effect="write",
                                           required_params=("zone_id", "setpoint_c")),
        "set_zone_cooling_setpoint": ToolSignature(name="set_zone_cooling_setpoint", effect="write",
                                                   required_params=("zone_id", "cooling_c")),
        "set_zone_co2_setpoint": ToolSignature(name="set_zone_co2_setpoint", effect="write",
                                               required_params=("zone_id", "co2_ppm")),
        "set_zone_lighting_setpoint": ToolSignature(name="set_zone_lighting_setpoint", effect="write",
                                                    required_params=("zone_id", "lux")),
        "set_zone_lighting_mode": ToolSignature(
            name="set_zone_lighting_mode", effect="write",
            required_params=("zone_id", "mode", "daylight_harvesting"),
            param_values={"mode": ("presence", "schedule")}),
        "set_supply_air_setpoint": ToolSignature(name="set_supply_air_setpoint", effect="write",
                                                 required_params=("supply_air_c",)),
        "set_tank_setpoint": ToolSignature(name="set_tank_setpoint", effect="write",
                                           required_params=("tank_c",)),
        "set_heat_pump_limit": ToolSignature(name="set_heat_pump_limit", effect="write",
                                             required_params=("max_fraction",)),
        "set_ahu_enabled": ToolSignature(name="set_ahu_enabled", effect="write",
                                         required_params=("enabled",)),
        "set_heating_cable_mode": ToolSignature(
            name="set_heating_cable_mode", effect="write",
            required_params=("circuit", "mode"),
            param_values={"circuit": CIRCUITS,
                          "mode": ("auto", "thermostat", "sensor", "on", "off")}),
    })
    contracts = ToolContractRegistry([
        ToolContract(tool="read_zone_state", capability="process_monitoring",
                     effect="read", resource_type="zone", mutation=False,
                     argument_roles={"zone_id": "target_resource"}),
        ToolContract(tool="read_building_energy", capability="process_monitoring",
                     effect="read", resource_type="building", mutation=False,
                     argument_roles={}),
        ToolContract(tool="set_zone_setpoint", capability="process_control",
                     effect="update", resource_type="zone", mutation=True,
                     argument_roles={"zone_id": "target_resource"},
                     state_delta={"zone.setpoint": "changed"}),
        ToolContract(tool="set_zone_cooling_setpoint", capability="process_control",
                     effect="update", resource_type="zone", mutation=True,
                     argument_roles={"zone_id": "target_resource"},
                     state_delta={"zone.cooling_setpoint": "changed"}),
        ToolContract(tool="set_zone_co2_setpoint", capability="process_control",
                     effect="update", resource_type="zone", mutation=True,
                     argument_roles={"zone_id": "target_resource"},
                     state_delta={"zone.co2_setpoint": "changed"}),
        ToolContract(tool="set_zone_lighting_setpoint", capability="process_control",
                     effect="update", resource_type="zone", mutation=True,
                     argument_roles={"zone_id": "target_resource"},
                     state_delta={"zone.lighting_setpoint": "changed"}),
        ToolContract(tool="set_zone_lighting_mode", capability="process_control",
                     effect="update", resource_type="zone", mutation=True,
                     argument_roles={"zone_id": "target_resource"},
                     state_delta={"zone.lighting_mode": "changed"}),
        ToolContract(tool="set_supply_air_setpoint", capability="process_control",
                     effect="update", resource_type="ahu",
                     resource_aliases=("zone", "plant", "building"), mutation=True,
                     argument_roles={},
                     state_delta={"ahu.supply_air_setpoint": "changed"}),
        ToolContract(tool="set_tank_setpoint", capability="process_control",
                     effect="update", resource_type="tank",
                     resource_aliases=("zone", "plant", "building"), mutation=True,
                     argument_roles={},
                     state_delta={"tank.setpoint": "changed"}),
        ToolContract(tool="set_heat_pump_limit", capability="process_control",
                     effect="update", resource_type="heat_pump",
                     resource_aliases=("zone", "plant", "building"), mutation=True,
                     argument_roles={},
                     state_delta={"heat_pump.max_fraction": "changed"}),
        ToolContract(tool="set_ahu_enabled", capability="process_control",
                     effect="update", resource_type="ahu",
                     resource_aliases=("zone", "plant", "building"), mutation=True,
                     argument_roles={},
                     state_delta={"ahu.enabled": "changed"}),
        ToolContract(tool="set_heating_cable_mode", capability="process_control",
                     effect="update", resource_type="heating_cable",
                     resource_aliases=("zone", "plant", "building"), mutation=True,
                     argument_roles={"circuit": "target_resource"},
                     state_delta={"heating_cable.mode": "changed"}),
    ])
    state = StateIndex.from_values(
        set(ZONES) | set(CIRCUITS) | {"demo-building-001", "presence", "schedule",
                                      "auto", "thermostat", "sensor", "on", "off"},
        # The coverage domain must be the tool metadata's domain
        # (deploy/ot/ot_tool_metadata.json): REMORA looks a value up in the
        # index under the tool's domain, and a mismatch reads as UNKNOWN, which
        # never supports an ACCEPT.
        (CoverageScope("building_automation", frozenset({"zone_id", "circuit", "mode"}), closed_world=True),),
    )
    return SemanticBundle.build(registry=registry, contracts=contracts,
                                validators=None, state=state)


def resolve_intent(intent_ref: str) -> ResolvedIntent | None:
    cfg = os.environ.get("REMORA_INTENT_SOURCE_FILE", "").strip()
    if not cfg or not intent_ref:
        return None
    try:
        raw = Path(cfg).read_bytes()
        table = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    e = table.get(intent_ref)
    if not isinstance(e, dict) or not e.get("task_text"):
        return None
    auth = f"signed_work_order:{hashlib.sha256(raw).hexdigest()}"
    return ResolvedIntent(
        intent=TaskIntent(
            operation=str(e.get("operation", "")),
            resource_type=str(e.get("resource_type", "")),
            requested_effect=str(e.get("requested_effect", "")),
            target_entities=tuple(e.get("target_entities", ())),
            source_spans=tuple(e.get("source_spans", ())),
            action_spans=tuple(e.get("action_spans", ())),
            proposed_by=auth),
        task_text=str(e["task_text"]), authority=auth)
