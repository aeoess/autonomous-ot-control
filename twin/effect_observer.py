"""Independent read-only observation of an actuation's effect.

"Independent" means a separate credential and read path from the dispatcher
(vantage ``same_test_deployment``), not an independently operated party. The
observer cannot command the plant and never edits an authorized-call record.

Status vocabulary mirrors the SDD projection (§12):
  EFFECT_VERIFIED       operation APPLIED and the zone now reports the target
  EFFECT_MISMATCH       the plant state contradicts the expected effect
  EFFECT_UNOBSERVABLE   no record, stale reading, or plant unreachable
  EFFECT_VERIFIER_FAILED  the observer itself errored
None of these is a retry permit.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from twin.state import QUALITY_GOOD, canonical_digest

SOURCE = "simulated-twin-read-only-observer/v1"
VANTAGE = "same_test_deployment"

EFFECT_VERIFIED = "EFFECT_VERIFIED"
EFFECT_MISMATCH = "EFFECT_MISMATCH"
EFFECT_UNOBSERVABLE = "EFFECT_UNOBSERVABLE"
EFFECT_VERIFIER_FAILED = "EFFECT_VERIFIER_FAILED"


class EffectObserver:
    def __init__(self, base_url: str, read_token: str, *, tenant_id: str, run_id: str,
                 client: httpx.Client | None = None, timeout_s: float = 5.0) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._headers = {"Authorization": f"Bearer {read_token}"}
        self.tenant_id = tenant_id
        self.run_id = run_id

    def _get(self, path: str) -> httpx.Response:
        return self._client.get(path, headers=self._headers)

    def observe(self, operation_id: str, *, zone_id: str, expected_setpoint_c: float) -> dict[str, Any]:
        """Return an ``ot-observation/v1`` envelope with an effect status."""
        observed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        base: dict[str, Any] = {
            "schema": "ot-observation/v1", "tenant_id": self.tenant_id, "run_id": self.run_id,
            "operation_id": operation_id, "observed_at": observed_at, "zone_id": zone_id,
            "expected_setpoint_c": expected_setpoint_c, "source": SOURCE, "vantage": VANTAGE,
        }
        try:
            status = self._get("/v1/status")
            op = self._get(f"/v1/operations/{operation_id}")
            zone = self._get(f"/v1/zones/{zone_id}")
        except httpx.HTTPError as e:
            return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                               "detail": f"plant_unreachable:{type(e).__name__}"})
        try:
            if status.status_code == 401 or op.status_code == 401 or zone.status_code == 401:
                return self._seal({**base, "effect_status": EFFECT_VERIFIER_FAILED,
                                   "detail": "observer_unauthorized"})
            st = status.json()
            base["building_id"] = st["building_id"]
            if zone.status_code == 404:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE, "detail": "unknown_zone"})
            z = zone.json()
            base.update({"state_revision": z["state_revision"], "observed_setpoint_c": z["setpoint_c"],
                         "measurement_quality": z["quality"], "sim_time_s": z["sim_time_s"],
                         "sim_utc": z["sim_utc"]})
            if op.status_code == 404:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                                   "detail": "no_operation_record"})
            rec = op.json()
            base["operation_status"] = rec["status"]
            # The record is proof only for its own tool and target. A record of another
            # tool that happens to carry this zone and value is not this effect.
            if rec.get("tool_name", "set_zone_setpoint") != "set_zone_setpoint":
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": "operation_tool_mismatch"})
            if rec["zone_id"] != zone_id:
                return self._seal({**base, "effect_status": EFFECT_MISMATCH, "detail": "zone_mismatch"})
            if rec["status"] != "APPLIED":
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": f"operation_rejected:{rec.get('reason')}"})
            if abs(float(rec["new_setpoint_c"]) - expected_setpoint_c) > 1e-6:
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": "recorded_setpoint_differs"})
            if z["quality"] != QUALITY_GOOD:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                                   "detail": "stale_measurement"})
            if abs(float(z["setpoint_c"]) - expected_setpoint_c) > 1e-6:
                # Applied once, but a later operation moved it. Report, do not guess.
                later = z["state_revision"] > rec["state_revision_after"]
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": "superseded_by_later_operation" if later
                                   else "plant_setpoint_differs"})
            return self._seal({**base, "effect_status": EFFECT_VERIFIED, "detail": None})
        except (ValueError, KeyError, TypeError) as e:
            return self._seal({**base, "effect_status": EFFECT_VERIFIER_FAILED,
                               "detail": f"malformed_response:{type(e).__name__}"})

    def observe_tool(self, operation_id: str, *, tool_name: str,
                     arguments: dict[str, Any]) -> dict[str, Any]:
        """Observe the effect of any governed WRITE tool via the read-only listener."""
        if tool_name == "set_zone_setpoint":
            return self.observe(operation_id, zone_id=str(arguments["zone_id"]),
                                expected_setpoint_c=float(arguments["setpoint_c"]))
        observed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        target = str(arguments.get("zone_id") or arguments.get("circuit") or tool_name)
        base: dict[str, Any] = {
            "schema": "ot-observation/v1", "tenant_id": self.tenant_id, "run_id": self.run_id,
            "operation_id": operation_id, "observed_at": observed_at, "zone_id": target,
            "expected_setpoint_c": 0.0, "source": SOURCE, "vantage": VANTAGE,
            "tool_name": tool_name, "expected_arguments": dict(arguments),
        }
        try:
            status = self._get("/v1/status")
            op = self._get(f"/v1/operations/{operation_id}")
            plant = self._get("/v1/plant")
        except httpx.HTTPError as e:
            return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                               "detail": f"plant_unreachable:{type(e).__name__}"})
        try:
            if status.status_code == 401 or op.status_code == 401 or plant.status_code == 401:
                return self._seal({**base, "effect_status": EFFECT_VERIFIER_FAILED,
                                   "detail": "observer_unauthorized"})
            st = status.json()
            base["building_id"] = st["building_id"]
            p = plant.json()
            base.update({"state_revision": p["state_revision"], "sim_time_s": p["sim_time_s"],
                         "sim_utc": p["sim_utc"], "measurement_quality": QUALITY_GOOD})
            if not p.get("plant_configured"):
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                                   "detail": "plant_not_configured"})
            if op.status_code == 404:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                                   "detail": "no_operation_record"})
            rec = op.json()
            base["operation_status"] = rec["status"]
            if rec["status"] != "APPLIED":
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": f"operation_rejected:{rec.get('reason')}"})
            # Bind the record to the claim before looking at the plant: the plant
            # showing the desired state proves nothing about *this* operation unless
            # the record names the same tool, the same target and the same value.
            binding = self._binding_mismatch(rec, tool_name, arguments)
            if binding is not None:
                return self._seal({**base, "effect_status": EFFECT_MISMATCH, "detail": binding})
            sp = p["setpoints"]
            obs_fields = self._extract_plant_fields(tool_name, arguments, sp)
            if obs_fields is None:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                                   "detail": "unknown_target"})
            base["observed_fields"] = obs_fields
            matched = all(
                (abs(float(obs_fields[k]) - float(v)) <= 1e-6
                 if isinstance(v, (int, float)) and not isinstance(v, bool)
                 else obs_fields.get(k) == v)
                for k, v in arguments.items() if k in obs_fields
            )
            if not matched:
                later = p["state_revision"] > rec["state_revision_after"]
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": "superseded_by_later_operation" if later
                                   else "plant_state_differs"})
            return self._seal({**base, "effect_status": EFFECT_VERIFIED, "detail": None})
        except (ValueError, KeyError, TypeError) as e:
            return self._seal({**base, "effect_status": EFFECT_VERIFIER_FAILED,
                               "detail": f"malformed_response:{type(e).__name__}"})

    #: For every governed plant tool: how its target is named in the operation
    #: record, and which argument carries the value the record must have applied.
    _TOOL_BINDING: dict[str, tuple[str | None, str | tuple[str, ...]]] = {
        "set_zone_cooling_setpoint": ("zone_id", "cooling_c"),
        "set_zone_co2_setpoint": ("zone_id", "co2_ppm"),
        "set_zone_lighting_setpoint": ("zone_id", "lux"),
        "set_zone_lighting_mode": ("zone_id", ("mode", "daylight_harvesting")),
        "set_supply_air_setpoint": (None, "supply_air_c"),
        "set_tank_setpoint": (None, "tank_c"),
        "set_heat_pump_limit": (None, "max_fraction"),
        "set_ahu_enabled": (None, "enabled"),
        "set_heating_cable_mode": ("circuit", "mode"),
    }
    _SYSTEM_TARGET = {"set_supply_air_setpoint": "AHU-01", "set_tank_setpoint": "TANK-01",
                      "set_heat_pump_limit": "HP-01", "set_ahu_enabled": "AHU-01"}

    @classmethod
    def _binding_mismatch(cls, rec: dict[str, Any], tool_name: str, args: dict[str, Any]) -> str | None:
        """Why this record is not evidence for this claim, or None when it is bound to it."""
        if rec.get("tool_name") != tool_name:
            return "operation_tool_mismatch"
        spec = cls._TOOL_BINDING.get(tool_name)
        if spec is None:
            return "unknown_target"
        target_arg, value_arg = spec
        expected_target = cls._SYSTEM_TARGET.get(tool_name) if target_arg is None else str(args.get(target_arg))
        if str(rec.get("target") or rec.get("zone_id")) != expected_target:
            return "operation_target_mismatch"
        applied = rec.get("new_value", rec.get("new_setpoint_c"))
        if isinstance(value_arg, tuple):
            expected: Any = {k: args.get(k) for k in value_arg}
            if not isinstance(applied, dict) or any(applied.get(k) != v for k, v in expected.items()):
                return "recorded_value_differs"
            return None
        expected = args.get(value_arg)
        if isinstance(expected, bool) or isinstance(applied, bool):
            return None if bool(applied) == bool(expected) else "recorded_value_differs"
        if isinstance(expected, (int, float)) and isinstance(applied, (int, float)):
            return None if abs(float(applied) - float(expected)) <= 1e-6 else "recorded_value_differs"
        return None if applied == expected else "recorded_value_differs"

    @staticmethod
    def _extract_plant_fields(tool_name: str, args: dict[str, Any],
                              sp: dict[str, Any]) -> dict[str, Any] | None:
        if tool_name in ("set_zone_cooling_setpoint", "set_zone_co2_setpoint",
                         "set_zone_lighting_setpoint", "set_zone_lighting_mode"):
            zid = args.get("zone_id")
            z = sp.get("zones", {}).get(zid)
            if z is None:
                return None
            if tool_name == "set_zone_cooling_setpoint":
                return {"zone_id": zid, "cooling_c": float(z["cooling_c"])}
            if tool_name == "set_zone_co2_setpoint":
                return {"zone_id": zid, "co2_ppm": int(round(float(z["co2_ppm"])))}
            if tool_name == "set_zone_lighting_setpoint":
                return {"zone_id": zid, "lux": int(round(float(z["lux"])))}
            return {"zone_id": zid, "mode": z["lighting_mode"],
                    "daylight_harvesting": bool(z["daylight_harvesting"])}
        if tool_name == "set_supply_air_setpoint":
            return {"system": "AHU-01", "supply_air_c": float(sp["supply_air_c"])}
        if tool_name == "set_tank_setpoint":
            return {"system": "TANK-01", "tank_c": float(sp["tank_c"])}
        if tool_name == "set_heat_pump_limit":
            return {"system": "HP-01", "max_fraction": float(sp["max_fraction"] if "max_fraction" in sp
                                                             else sp["hp_max_fraction"])}
        if tool_name == "set_ahu_enabled":
            return {"system": "AHU-01", "enabled": bool(sp["ahu_enabled"])}
        if tool_name == "set_heating_cable_mode":
            c = args.get("circuit")
            modes = sp.get("cable_modes", {})
            return {"circuit": c, "mode": modes.get(c, "auto")}
        return None

    @staticmethod
    def _seal(env: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in env.items() if k != "observation_digest"}
        return {**body, "observation_digest": canonical_digest(body)}
