"""Sign the deployment's REMORA artifacts at deploy time (OT-003, D-05).

Runs once, inside the REMORA execution image (it imports ``remora``), on the
signer's side of the boundary: it receives the two Ed25519 seeds through its
environment, writes signed artifacts and prints the PUBLIC values REMORA needs.
The REMORA runtime never receives a seed, so it can verify these artifacts but
cannot author larger limits or a different tool contract (RMR-CR-001).

Outputs, under ``--out``:

* ``toolspec-bundle.json`` — a REMORA ToolSpec bundle (schema version 2) for
  the three frozen v1 tools, derived from ``contracts/ot-toolspec-v1.json``.
  Each spec attests the ``closure-sha256:`` digest REMORA measures for the
  callable ``ot_registry`` registers, so a changed callable or a changed
  module it calls refuses registration (``toolspec_callable_digest_mismatch``).
* ``limits.signed.json`` — ``deploy/ot/ot_limits.policy.json`` signed by the
  limit authority key.

stdout carries ``KEY=value`` lines for the gitignored ``.env``:
``REMORA_TOOLSPEC_VERIFY_KEYS``, ``REMORA_TOOLSPEC_PINNED_DIGEST`` and
``REMORA_LIMITS_TRUSTED_KEYS``.

The argument schema mirrors the twin guard's static bounds. REMORA's schema
validator has no ``multipleOf``, so the 0.1 degC resolution is not signed here;
the adapter's canonicalization and the twin guard enforce it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SPEC_VERSION = 1
TARGET_ENVIRONMENT = "simulated_pilot"
IMPLEMENTATION = "autonomous-ot-control/ot_registry"


def _description_pair(text: str) -> dict[str, str]:
    return {"description": text,
            "description_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _common(tool_id: str, digest: str, description: str) -> dict[str, Any]:
    return {
        "tool_id": tool_id,
        "version": SPEC_VERSION,
        "callable_digest": digest,
        "implementation_identity": f"{IMPLEMENTATION}.{tool_id}@v{SPEC_VERSION}",
        **_description_pair(description),
        "domain": "ot",
        "allowed_targets": [TARGET_ENVIRONMENT],
        "compensation_tool": None,
        "timeout_policy": {"dispatch_timeout_seconds": 10},
        # The callable reaches the twin only: the plant-private actuator for
        # the write tool, the observe listener for reads.
        "network_policy": {"egress": "twin-only"},
        "signing_identity": "",  # set to the key id by sign_with_seed
    }


def build_specs(toolspec: dict[str, Any], digests: dict[str, str]) -> list[dict[str, Any]]:
    """REMORA ToolSpecs for the governed OT surface, from the deployment contract (schema v5)."""
    tools = {t["tool_name"]: t for t in toolspec["tools"]}
    zones = list(tools["set_zone_setpoint"]["arguments"]["zone_id"]["enum"])
    sp = tools["set_zone_setpoint"]["arguments"]["setpoint_c"]
    zone_arg = {"type": "string", "enum": zones}
    circuits = ["CABLE-RAMP", "CABLE-GUTTER"]
    cable_modes = ["auto", "thermostat", "sensor", "on", "off"]
    light_modes = ["presence", "schedule"]

    read_zone = {
        **_common("read_zone_state", digests["read_zone_state"],
                  "Read one zone's measured temperature, setpoint and sample quality."),
        "argument_schema": {"type": "object", "properties": {"zone_id": zone_arg},
                            "required": ["zone_id"], "additionalProperties": False},
        "risk_tier": "low",
        "action_type": "read",
        "capabilities": ["process_monitoring"],
        "semantic_contract": {"capability": "process_monitoring", "effect": "read",
                              "resource_type": "zone", "mutation": False,
                              "argument_roles": {"zone_id": "target_resource"}},
        "credential_scope": ["twin:read"],
        "idempotency_contract": {"safe_to_retry": True, "key_derivation": "none"},
        "postcondition_reader": None,
        "downstream_capabilities": [],
    }
    read_energy = {
        **_common("read_building_energy", digests["read_building_energy"],
                  "Read the building's cumulative simulated energy and sampled power."),
        "argument_schema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        "risk_tier": "low",
        "action_type": "read",
        "capabilities": ["process_monitoring"],
        "semantic_contract": {"capability": "process_monitoring", "effect": "read",
                              "resource_type": "building", "mutation": False,
                              "argument_roles": {}},
        "credential_scope": ["twin:read"],
        "idempotency_contract": {"safe_to_retry": True, "key_derivation": "none"},
        "postcondition_reader": None,
        "downstream_capabilities": [],
    }
    write = {
        **_common("set_zone_setpoint", digests["set_zone_setpoint"],
                  "Change exactly one simulated zone setpoint within the signed bounds."),
        "argument_schema": {
            "type": "object",
            "properties": {
                "zone_id": zone_arg,
                "setpoint_c": {"type": "number", "minimum": float(sp["minimum"]),
                               "maximum": float(sp["maximum"])},
            },
            "required": ["zone_id", "setpoint_c"],
            "additionalProperties": False,
        },
        "risk_tier": "high",
        "action_type": "write",
        "capabilities": ["process_control"],
        "semantic_contract": {"capability": "process_control", "effect": "update",
                              "resource_type": "zone", "mutation": True,
                              "argument_roles": {"zone_id": "target_resource"}},
        "credential_scope": ["twin:actuate"],
        # The twin applies an operation id once; REMORA's proposal id is that id.
        "idempotency_contract": {"safe_to_retry": False, "key_derivation": "proposal_id"},
        "postcondition_reader": "ot-effect-observer/v1",
        "postcondition": {
            "target_selector": {"zone_id": {"$arg": "zone_id"}},
            "expected_fields": {"setpoint_c": {"$arg": "setpoint_c"}},
            "comparison_rules": {"setpoint_c": "exact"},
            "observation_deadline_seconds": 30,
            "repeatable": True,
        },
        "downstream_capabilities": [{
            "capability": "twin.actuate.setpoint",
            "resources": [f"twin://zone/{z}" for z in zones],
            "purpose": "apply_one_setpoint",
        }],
    }
    out = [read_zone, read_energy, write]

    def _plant_write(tool_id: str, desc: str, props: dict[str, Any], req: list[str],
                     tier: str, res_type: str, roles: dict[str, str],
                     selector: dict[str, Any], expected: dict[str, Any],
                     resources: list[str]) -> dict[str, Any]:
        return {
            **_common(tool_id, digests[tool_id], desc),
            "argument_schema": {
                "type": "object",
                "properties": props,
                "required": req,
                "additionalProperties": False,
            },
            "risk_tier": tier,
            "action_type": "write",
            "capabilities": ["process_control"],
            "semantic_contract": {"capability": "process_control", "effect": "update",
                                  "resource_type": res_type, "mutation": True,
                                  "argument_roles": roles},
            "credential_scope": ["twin:actuate"],
            "idempotency_contract": {"safe_to_retry": False, "key_derivation": "proposal_id"},
            "postcondition_reader": "ot-effect-observer/v1",
            "postcondition": {
                "target_selector": selector,
                "expected_fields": expected,
                "comparison_rules": {k: "exact" for k in expected},
                "observation_deadline_seconds": 30,
                "repeatable": True,
            },
            "downstream_capabilities": [{
                "capability": f"twin.actuate.{tool_id}",
                "resources": resources,
                "purpose": f"apply_{tool_id}",
            }],
        }

    plant_defs = [
        ("set_zone_cooling_setpoint",
         "Change one simulated zone cooling setpoint within the signed bounds (IL-01).",
         {"zone_id": zone_arg, "cooling_c": {"type": "number", "minimum": 20.0, "maximum": 28.0}},
         ["zone_id", "cooling_c"], "medium", "zone", {"zone_id": "target_resource"},
         {"zone_id": {"$arg": "zone_id"}}, {"cooling_c": {"$arg": "cooling_c"}},
         [f"twin://zone/{z}" for z in zones]),
        ("set_zone_co2_setpoint",
         "Change one simulated zone CO2 DCV target within the signed bounds (IL-07).",
         {"zone_id": zone_arg, "co2_ppm": {"type": "integer", "minimum": 600, "maximum": 1000}},
         ["zone_id", "co2_ppm"], "medium", "zone", {"zone_id": "target_resource"},
         {"zone_id": {"$arg": "zone_id"}}, {"co2_ppm": {"$arg": "co2_ppm"}},
         [f"twin://zone/{z}" for z in zones]),
        ("set_zone_lighting_setpoint",
         "Change one simulated zone maintained illuminance setpoint (IL-08).",
         {"zone_id": zone_arg, "lux": {"type": "integer", "minimum": 0, "maximum": 750}},
         ["zone_id", "lux"], "low", "zone", {"zone_id": "target_resource"},
         {"zone_id": {"$arg": "zone_id"}}, {"lux": {"$arg": "lux"}},
         [f"twin://zone/{z}" for z in zones]),
        ("set_zone_lighting_mode",
         "Change one simulated zone lighting control mode and daylight harvesting (IL-08).",
         {"zone_id": zone_arg, "mode": {"type": "string", "enum": light_modes},
          "daylight_harvesting": {"type": "boolean"}},
         ["zone_id", "mode", "daylight_harvesting"], "low", "zone", {"zone_id": "target_resource"},
         {"zone_id": {"$arg": "zone_id"}},
         {"mode": {"$arg": "mode"}, "daylight_harvesting": {"$arg": "daylight_harvesting"}},
         [f"twin://zone/{z}" for z in zones]),
        ("set_supply_air_setpoint",
         "Change the AHU supply-air temperature setpoint within 15..21 degC (IL-05, IL-11).",
         {"supply_air_c": {"type": "number", "minimum": 15.0, "maximum": 21.0}},
         ["supply_air_c"], "high", "ahu", {},
         {"system": "AHU-01"}, {"supply_air_c": {"$arg": "supply_air_c"}},
         ["twin://plant/AHU-01"]),
        ("set_tank_setpoint",
         "Change the buffer-tank temperature setpoint within 35..60 degC (IL-04, IL-11).",
         {"tank_c": {"type": "number", "minimum": 35.0, "maximum": 60.0}},
         ["tank_c"], "high", "tank", {},
         {"system": "TANK-01"}, {"tank_c": {"$arg": "tank_c"}},
         ["twin://plant/TANK-01"]),
        ("set_heat_pump_limit",
         "Change the heat-pump compressor demand-response cap within 0..1 (IL-09, IL-11).",
         {"max_fraction": {"type": "number", "minimum": 0.0, "maximum": 1.0}},
         ["max_fraction"], "high", "heat_pump", {},
         {"system": "HP-01"}, {"max_fraction": {"$arg": "max_fraction"}},
         ["twin://plant/HP-01"]),
        ("set_ahu_enabled",
         "Start or stop the AHU when unoccupied and IAQ allows (IL-06).",
         {"enabled": {"type": "boolean"}},
         ["enabled"], "high", "ahu", {},
         {"system": "AHU-01"}, {"enabled": {"$arg": "enabled"}},
         ["twin://plant/AHU-01"]),
        ("set_heating_cable_mode",
         "Change a heating-cable circuit mode unless icing hazard requires heat (IL-10).",
         {"circuit": {"type": "string", "enum": circuits},
          "mode": {"type": "string", "enum": cable_modes}},
         ["circuit", "mode"], "high", "heating_cable", {"circuit": "target_resource"},
         {"circuit": {"$arg": "circuit"}}, {"mode": {"$arg": "mode"}},
         [f"twin://cable/{c}" for c in circuits]),
    ]
    for tid, *rest in plant_defs:
        if tid in tools and tid in digests:
            out.append(_plant_write(tid, *rest))
    return out


def measured_digests() -> dict[str, str]:
    """The closure digests REMORA measures for the callables ot_registry registers.

    ``register_tools`` registers bound methods of ``OtTools``. A closure digest
    depends only on the defining module and what it imports, never on the
    instance, so measuring the class attributes gives the value REMORA
    computes at registration without building the actuator client here: the
    signer never needs the actuator credential.
    """
    from remora.toolcall.callable_digest import closure_digest

    import ot_registry
    from adapter.contracts import ALL_WRITE_TOOLS, READ_TOOLS

    return {name: closure_digest(getattr(ot_registry.OtTools, name))
            for name in (*READ_TOOLS, *ALL_WRITE_TOOLS)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sign_deployment")
    p.add_argument("--toolspec", type=Path, default=Path("/app/contracts/ot-toolspec-v1.json"))
    p.add_argument("--limits", type=Path, default=Path("/app/ot_limits.policy.json"))
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)

    from remora.crypto import SignatureDomain, SigningKey
    from remora.enforcement.cumulative_limits import LimitPolicy, sign_policy
    from remora.toolcall.toolspec_sign import sign_with_seed

    toolspec_seed = os.environ.get("OT_TOOLSPEC_SIGNING_SEED", "").strip()
    limits_seed = os.environ.get("OT_LIMITS_SIGNING_SEED", "").strip()
    if not toolspec_seed or not limits_seed:
        print("sign_deployment: OT_TOOLSPEC_SIGNING_SEED and OT_LIMITS_SIGNING_SEED are required",
              file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    specs = build_specs(json.loads(a.toolspec.read_text(encoding="utf-8")), measured_digests())
    bundle, toolspec_public, pinned = sign_with_seed(
        {"schema_version": 5, "tool_specs": specs}, toolspec_seed, signed_at=stamp)

    policy = json.loads(a.limits.read_text(encoding="utf-8"))
    LimitPolicy.from_dict(policy)  # refuse to sign what REMORA would refuse to load
    limits_key = SigningKey.from_text(limits_seed, {SignatureDomain.LIMIT_POLICY})
    signed_limits = sign_policy(policy, limits_key, stamp.replace("+00:00", "Z"))

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "toolspec-bundle.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (a.out / "limits.signed.json").write_text(
        json.dumps(signed_limits, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"REMORA_TOOLSPEC_VERIFY_KEYS={toolspec_public}")
    print(f"REMORA_TOOLSPEC_PINNED_DIGEST={pinned}")
    print(f"REMORA_LIMITS_TRUSTED_KEYS={limits_key.verification_key().public_bytes.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
