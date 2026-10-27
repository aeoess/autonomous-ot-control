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
    """REMORA ToolSpecs for the frozen v1 surface, from the deployment contract."""
    tools = {t["tool_name"]: t for t in toolspec["tools"]}
    zones = list(tools["set_zone_setpoint"]["arguments"]["zone_id"]["enum"])
    sp = tools["set_zone_setpoint"]["arguments"]["setpoint_c"]
    zone_arg = {"type": "string", "enum": zones}
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
        "downstream_capabilities": [{
            "capability": "twin.actuate.setpoint",
            "resources": [f"twin://zone/{z}" for z in zones],
            "purpose": "apply_one_setpoint",
        }],
    }
    return [read_zone, read_energy, write]


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

    return {name: closure_digest(getattr(ot_registry.OtTools, name))
            for name in ("read_zone_state", "read_building_energy", "set_zone_setpoint")}


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
        {"schema_version": 2, "tool_specs": specs}, toolspec_seed, signed_at=stamp)

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
