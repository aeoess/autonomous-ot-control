"""Deployment-owned signed ToolSpec: loading, canonicalization and attestation.

The ToolSpec (``contracts/ot-toolspec-v1.json``) is the deployment-owned tool
contract that REMORA binds and verifies (INV-03). This module:

* loads and structurally validates it,
* exposes the single WRITE tool's declared bounds,
* canonicalizes agent-proposed arguments to the immutable form that is hashed
  and dispatched (no caller-supplied extras survive), and
* computes the canonical spec digest and a deployment-owner HMAC attestation.

The HMAC here is the *deployment owner's* attestation over the canonical spec,
produced at deploy time with a key that is never committed. It is deliberately
distinct from REMORA's own authoritative binding of the signed ToolSpec, which
is performed inside the REMORA control plane and is not reimplemented here.
"""
from __future__ import annotations

import hmac
import math
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from twin.state import canonical_digest, canonical_json

TOOLSPEC_SCHEMA = "ot-toolspec/v1"
WRITE_TOOL = "set_zone_setpoint"
PLANT_WRITE_TOOLS = (
    "set_zone_cooling_setpoint",
    "set_zone_co2_setpoint",
    "set_zone_lighting_setpoint",
    "set_zone_lighting_mode",
    "set_supply_air_setpoint",
    "set_tank_setpoint",
    "set_heat_pump_limit",
    "set_ahu_enabled",
    "set_heating_cable_mode",
)
ALL_WRITE_TOOLS = (WRITE_TOOL, *PLANT_WRITE_TOOLS)
READ_TOOLS = ("read_zone_state", "read_building_energy")
CIRCUITS = ("CABLE-RAMP", "CABLE-GUTTER")
CABLE_MODES = ("auto", "thermostat", "sensor", "on", "off")
LIGHTING_MODES = ("presence", "schedule")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOOLSPEC_PATH = REPO_ROOT / "contracts" / "ot-toolspec-v1.json"
_SIGNATURE_KEY = "signature"


class ToolSpecError(ValueError):
    """The ToolSpec is structurally invalid or an argument is out of contract."""


@dataclass(frozen=True)
class SetpointLimits:
    max_delta_c: float
    min_interval_seconds: int
    max_changes_per_hour: int
    max_observation_age_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_delta_c": self.max_delta_c,
            "min_interval_seconds": self.min_interval_seconds,
            "max_changes_per_hour": self.max_changes_per_hour,
            "max_observation_age_seconds": self.max_observation_age_seconds,
        }


@dataclass(frozen=True)
class ToolSpec:
    raw: dict[str, Any]
    spec_id: str
    deployment: str
    building_id: str
    policy_version: str
    units: str
    resolution_c: float
    zones: tuple[str, ...]
    min_setpoint_c: float
    max_setpoint_c: float
    limits: SetpointLimits

    # -- loading ------------------------------------------------------------
    @classmethod
    def load(cls, source: str | Path | dict[str, Any] | None = None) -> "ToolSpec":
        if source is None:
            source = DEFAULT_TOOLSPEC_PATH
        raw = source if isinstance(source, dict) else _read_json(source)
        if raw.get("schema") != TOOLSPEC_SCHEMA:
            raise ToolSpecError("unsupported toolspec schema")
        tools = {t.get("tool_name"): t for t in raw.get("tools", [])}
        base_surface = {"read_zone_state", "read_building_energy", WRITE_TOOL}
        full_surface = base_surface | set(PLANT_WRITE_TOOLS)
        if set(tools) not in (base_surface, full_surface):
            raise ToolSpecError("toolspec must declare the v1 or full supervisory tool surface")
        for tname, tdef in tools.items():
            if tname in READ_TOOLS and tdef.get("effect") != "READ":
                raise ToolSpecError(f"{tname} must be a READ tool")
            if tname in ALL_WRITE_TOOLS and tdef.get("effect") != "WRITE":
                raise ToolSpecError(f"{tname} must be a WRITE tool")
        write = tools[WRITE_TOOL]
        if write.get("effect") != "WRITE":
            raise ToolSpecError("set_zone_setpoint must be the WRITE tool")
        args = write.get("arguments", {})
        zone_enum = args.get("zone_id", {}).get("enum")
        sp = args.get("setpoint_c", {})
        if not zone_enum or "minimum" not in sp or "maximum" not in sp:
            raise ToolSpecError("write tool missing zone/setpoint bounds")
        lim = write.get("limits")
        if not isinstance(lim, dict):
            raise ToolSpecError("write tool missing limits")
        try:
            limits = SetpointLimits(
                max_delta_c=float(lim["max_delta_c"]),
                min_interval_seconds=int(lim["min_interval_seconds"]),
                max_changes_per_hour=int(lim["max_changes_per_hour"]),
                max_observation_age_seconds=int(lim["max_observation_age_seconds"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ToolSpecError(f"invalid limits: {e}") from None
        lo, hi = float(sp["minimum"]), float(sp["maximum"])
        if not lo < hi:
            raise ToolSpecError("setpoint minimum must be below maximum")
        return cls(
            raw=raw, spec_id=str(raw["spec_id"]), deployment=str(raw["deployment"]),
            building_id=str(raw["building_id"]), policy_version=str(raw["policy_version"]),
            units=str(raw["units"]), resolution_c=float(raw["resolution_c"]),
            zones=tuple(zone_enum), min_setpoint_c=lo, max_setpoint_c=hi, limits=limits,
        )

    # -- identity -----------------------------------------------------------
    @property
    def canonical(self) -> dict[str, Any]:
        """The spec without its signature block: the bytes that are attested."""
        return {k: v for k, v in self.raw.items() if k != _SIGNATURE_KEY}

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical)

    def attest(self, key: str | bytes) -> str:
        """Deployment-owner HMAC over the canonical spec (hex)."""
        k = key.encode() if isinstance(key, str) else key
        if not k:
            raise ToolSpecError("empty signing key")
        return hmac.new(k, canonical_json(self.canonical), sha256).hexdigest()

    def verify_attestation(self, key: str | bytes, value: str) -> bool:
        return hmac.compare_digest(self.attest(key), value or "")

    # -- argument contract --------------------------------------------------
    def canonical_setpoint_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate and canonicalize ``set_zone_setpoint`` arguments.

        Only ``zone_id`` and ``setpoint_c`` survive; anything else fails closed
        (INV-05). The returned dict is the immutable argument form bound into the
        dispatched call. This checks the *contract*, not live plant state —
        freshness, revision and rate limits are enforced by REMORA policy and by
        the twin's interlock at dispatch time.
        """
        if not isinstance(arguments, dict):
            raise ToolSpecError("arguments must be an object")
        extra = set(arguments) - {"zone_id", "setpoint_c"}
        if extra:
            raise ToolSpecError(f"unexpected argument(s): {sorted(extra)}")
        zone_id = arguments.get("zone_id")
        if zone_id not in self.zones:
            raise ToolSpecError("zone_id not in signed tool contract")
        sp = arguments.get("setpoint_c")
        if isinstance(sp, bool) or not isinstance(sp, (int, float)) or not math.isfinite(sp):
            raise ToolSpecError("setpoint_c must be a finite number")
        sp = float(sp)
        if not (self.min_setpoint_c <= sp <= self.max_setpoint_c):
            raise ToolSpecError("setpoint_c outside signed bounds")
        steps = sp / self.resolution_c
        if abs(steps - round(steps)) > 1e-6:
            raise ToolSpecError("setpoint_c violates resolution")
        # Normalize to the declared resolution so the canonical form and its
        # digest are stable regardless of tiny float formatting differences.
        sp = round(round(steps) * self.resolution_c, 6)
        return {"zone_id": zone_id, "setpoint_c": sp}

    def canonical_tool_arguments(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate and canonicalize arguments for any governed WRITE tool."""
        if tool_name == WRITE_TOOL:
            return self.canonical_setpoint_arguments(arguments)
        if not isinstance(arguments, dict):
            raise ToolSpecError("arguments must be an object")

        def _req_keys(expected: set[str]) -> None:
            extra = set(arguments) - expected
            if extra:
                raise ToolSpecError(f"unexpected argument(s): {sorted(extra)}")
            missing = expected - set(arguments)
            if missing:
                raise ToolSpecError(f"missing argument(s): {sorted(missing)}")

        def _zone() -> str:
            z = arguments.get("zone_id")
            if z not in self.zones:
                raise ToolSpecError("zone_id not in signed tool contract")
            return str(z)

        def _num(name: str, lo: float, hi: float, res: float) -> float:
            v = arguments.get(name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ToolSpecError(f"{name} must be a finite number")
            vf = float(v)
            if not (lo <= vf <= hi):
                raise ToolSpecError(f"{name} outside signed bounds")
            steps = vf / res
            if abs(steps - round(steps)) > 1e-6:
                raise ToolSpecError(f"{name} violates resolution")
            return round(round(steps) * res, 6)

        def _int_arg(name: str, lo: int, hi: int, res: int) -> int:
            v = arguments.get(name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise ToolSpecError(f"{name} must be an integer")
            if not (lo <= v <= hi):
                raise ToolSpecError(f"{name} outside signed bounds")
            if v % res != 0:
                raise ToolSpecError(f"{name} violates resolution")
            return int(v)

        if tool_name == "set_zone_cooling_setpoint":
            _req_keys({"zone_id", "cooling_c"})
            return {"zone_id": _zone(), "cooling_c": _num("cooling_c", 20.0, 28.0, 0.1)}
        if tool_name == "set_zone_co2_setpoint":
            _req_keys({"zone_id", "co2_ppm"})
            return {"zone_id": _zone(), "co2_ppm": _int_arg("co2_ppm", 600, 1000, 10)}
        if tool_name == "set_zone_lighting_setpoint":
            _req_keys({"zone_id", "lux"})
            return {"zone_id": _zone(), "lux": _int_arg("lux", 0, 750, 10)}
        if tool_name == "set_zone_lighting_mode":
            _req_keys({"zone_id", "mode", "daylight_harvesting"})
            zid = _zone()
            mode = arguments.get("mode")
            dh = arguments.get("daylight_harvesting")
            if mode not in LIGHTING_MODES:
                raise ToolSpecError("mode not in signed tool contract")
            if not isinstance(dh, bool):
                raise ToolSpecError("daylight_harvesting must be a boolean")
            return {"zone_id": zid, "mode": str(mode), "daylight_harvesting": dh}
        if tool_name == "set_supply_air_setpoint":
            _req_keys({"supply_air_c"})
            return {"supply_air_c": _num("supply_air_c", 15.0, 21.0, 0.5)}
        if tool_name == "set_tank_setpoint":
            _req_keys({"tank_c"})
            return {"tank_c": _num("tank_c", 35.0, 60.0, 0.5)}
        if tool_name == "set_heat_pump_limit":
            _req_keys({"max_fraction"})
            return {"max_fraction": _num("max_fraction", 0.0, 1.0, 0.05)}
        if tool_name == "set_ahu_enabled":
            _req_keys({"enabled"})
            en = arguments.get("enabled")
            if not isinstance(en, bool):
                raise ToolSpecError("enabled must be a boolean")
            return {"enabled": en}
        if tool_name == "set_heating_cable_mode":
            _req_keys({"circuit", "mode"})
            circuit = arguments.get("circuit")
            mode = arguments.get("mode")
            if circuit not in CIRCUITS:
                raise ToolSpecError("circuit not in signed tool contract")
            if mode not in CABLE_MODES:
                raise ToolSpecError("mode not in signed tool contract")
            return {"circuit": str(circuit), "mode": str(mode)}
        raise ToolSpecError(f"unknown write tool {tool_name!r}")


def _read_json(path: str | Path) -> dict[str, Any]:
    import json
    return json.loads(Path(path).read_text("utf-8"))
