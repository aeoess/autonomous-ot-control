"""Local simulator interlock for setpoint writes.

This is an *additional* local check that refuses invalid effects even when a
request reaches the write endpoint (INV-07). It is never the source of
authorization: REMORA alone authorizes. Order matters only for which reason is
reported; any failing check refuses with zero plant change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from twin.model import Twin

UNIT = "degC"
RESOLUTION_C = 0.1
ALARM_LOW_C = 12.0
ALARM_HIGH_C = 30.0


@dataclass(frozen=True)
class GuardConfig:
    max_delta_c: float
    min_interval_seconds: int
    max_changes_per_hour: int
    max_observation_age_seconds: int

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "GuardConfig":
        g = cfg["guard"]
        out = cls(max_delta_c=float(g["max_delta_c"]),
                  min_interval_seconds=int(g["min_interval_seconds"]),
                  max_changes_per_hour=int(g["max_changes_per_hour"]),
                  max_observation_age_seconds=int(g["max_observation_age_seconds"]))
        if out.max_delta_c <= 0 or out.min_interval_seconds < 0 or out.max_changes_per_hour < 1 \
                or out.max_observation_age_seconds <= 0:
            raise ValueError("invalid guard configuration")
        return out


def check(twin: "Twin", cmd: dict[str, Any]) -> str | None:
    """Return a refusal reason, or None when the write is locally acceptable."""
    g = twin.guard
    if twin.stop_switch:
        return "stop_switch_engaged"
    if twin.alarm_active():
        return "alarm_freeze"
    zone_id = cmd.get("zone_id")
    zone = twin.zones.get(zone_id) if isinstance(zone_id, str) else None
    if zone is None:
        return "unknown_zone"
    if cmd.get("units") != UNIT:
        return "unit_mismatch"
    sp = cmd.get("setpoint_c")
    if isinstance(sp, bool) or not isinstance(sp, (int, float)) or not math.isfinite(sp):
        return "invalid_value"
    sp = float(sp)
    if abs(sp / RESOLUTION_C - round(sp / RESOLUTION_C)) > 1e-6:
        return "invalid_resolution"
    lim = twin.zone_limits[zone_id]
    if not (lim[0] <= sp <= lim[1]):
        return "out_of_bounds"
    rev = cmd.get("expected_state_revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev != twin.revision:
        return "revision_mismatch"
    if twin.clock.now_s - zone.sample_at_s > g.max_observation_age_seconds:
        return "stale_observation"
    delta = abs(sp - zone.setpoint_c)
    if delta < 1e-9:
        return "no_change"
    if delta > g.max_delta_c + 1e-9:
        return "delta_exceeded"
    now = twin.clock.now_s
    if zone.change_history_s and now - zone.change_history_s[-1] < g.min_interval_seconds:
        return "min_interval"
    if sum(1 for t in zone.change_history_s if now - t < 3600) >= g.max_changes_per_hour:
        return "rate_limited"
    return None


CIRCUITS = ("CABLE-RAMP", "CABLE-GUTTER")
CABLE_MODES = ("auto", "thermostat", "sensor", "on", "off")
LIGHTING_MODES = ("presence", "schedule")


def _finite_number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _int_value(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def _res_ok(val: float, res: float) -> bool:
    return abs(val / res - round(val / res)) <= 1e-6


def _rate_check(twin: "Twin", key: str, min_interval_s: int, max_per_hour: int = 4) -> str | None:
    now = twin.clock.now_s
    hist = getattr(twin, "point_history_s", {}).get(key, [])
    if hist and now - hist[-1] < min_interval_s:
        return "min_interval"
    if sum(1 for t in hist if now - t < 3600) >= max_per_hour:
        return "rate_limited"
    return None


def check_plant(twin: "Twin", cmd: dict[str, Any]) -> str | None:
    """Return a refusal reason, or None when a plant/supervisory write is locally acceptable.

    Enforces the interlock rules IL-01..IL-11 (docs/datapoints.md).
    """
    if twin.stop_switch:
        return "stop_switch_engaged"
    if twin.alarm_active():
        return "alarm_freeze"
    if twin.plant is None:
        return "plant_not_configured"
    rev = cmd.get("expected_state_revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev != twin.revision:
        return "revision_mismatch"
    tool = cmd.get("tool_name")
    args = cmd.get("arguments")
    if not isinstance(tool, str) or not isinstance(args, dict):
        return "invalid_command"
    now = twin.clock.now_s
    occupied = twin.scenario.occupied(now)
    sp = twin.setpoints

    if tool == "set_zone_cooling_setpoint":
        zid = args.get("zone_id")
        if zid not in twin.zones:
            return "unknown_zone"
        val = _finite_number(args.get("cooling_c"))
        if val is None:
            return "invalid_value"
        if not _res_ok(val, 0.1):
            return "invalid_resolution"
        if not (20.0 <= val <= 28.0):
            return "out_of_bounds"
        cur = sp.zones[zid].cooling_c
        if abs(val - cur) < 1e-9:
            return "no_change"
        if abs(val - cur) > 4.5 + 1e-9:
            return "delta_exceeded"
        if val < twin.zones[zid].setpoint_c + 2.0 - 1e-9:
            return "IL-01:deadband"
        return _rate_check(twin, f"{zid}.CoolingSetpoint", 900)

    if tool == "set_zone_co2_setpoint":
        zid = args.get("zone_id")
        if zid not in twin.zones:
            return "unknown_zone"
        val = _int_value(args.get("co2_ppm"))
        if val is None:
            return "invalid_value"
        if val % 10 != 0:
            return "invalid_resolution"
        if not (600 <= val <= 1000):
            return "IL-07:out_of_bounds"
        cur = sp.zones[zid].co2_ppm
        if abs(val - cur) < 1e-9:
            return "no_change"
        if abs(val - cur) > 100 + 1e-9:
            return "delta_exceeded"
        return _rate_check(twin, f"{zid}.CO2Setpoint", 900)

    if tool == "set_zone_lighting_setpoint":
        zid = args.get("zone_id")
        if zid not in twin.zones:
            return "unknown_zone"
        val = _int_value(args.get("lux"))
        if val is None:
            return "invalid_value"
        if val % 10 != 0:
            return "invalid_resolution"
        if not (0 <= val <= 750):
            return "out_of_bounds"
        if occupied and val < 300:
            return "IL-08:occupied_lux_floor"
        if not occupied and 0 < val < 300:
            return "IL-08:lux_band"
        if abs(val - sp.zones[zid].lux) < 1e-9:
            return "no_change"
        return _rate_check(twin, f"{zid}.LuxSetpoint", 900)

    if tool == "set_zone_lighting_mode":
        zid = args.get("zone_id")
        if zid not in twin.zones:
            return "unknown_zone"
        mode = args.get("mode")
        dh = args.get("daylight_harvesting")
        if mode not in LIGHTING_MODES or not isinstance(dh, bool):
            return "invalid_value"
        zs = sp.zones[zid]
        if zs.lighting_mode == mode and zs.daylight_harvesting is dh:
            return "no_change"
        return _rate_check(twin, f"{zid}.LightingMode", 900)

    if tool == "set_supply_air_setpoint":
        val = _finite_number(args.get("supply_air_c"))
        if val is None:
            return "invalid_value"
        if not _res_ok(val, 0.5):
            return "invalid_resolution"
        if not (15.0 <= val <= 21.0):
            return "IL-05:out_of_bounds"
        if abs(val - sp.supply_air_c) < 1e-9:
            return "no_change"
        if abs(val - sp.supply_air_c) > 1.0 + 1e-9:
            return "IL-11:delta_exceeded"
        return _rate_check(twin, "AHU-01.SupplyAirSetpoint", 900)

    if tool == "set_tank_setpoint":
        val = _finite_number(args.get("tank_c"))
        if val is None:
            return "invalid_value"
        if not _res_ok(val, 0.5):
            return "invalid_resolution"
        if not (35.0 <= val <= 60.0):
            return "IL-04:out_of_bounds"
        if abs(val - sp.tank_c) < 1e-9:
            return "no_change"
        if abs(val - sp.tank_c) > 3.0 + 1e-9:
            return "IL-04:delta_exceeded"
        return _rate_check(twin, "TANK-01.Setpoint", 900)

    if tool == "set_heat_pump_limit":
        val = _finite_number(args.get("max_fraction"))
        if val is None:
            return "invalid_value"
        if not _res_ok(val, 0.05):
            return "invalid_resolution"
        if not (0.0 <= val <= 1.0):
            return "out_of_bounds"
        if abs(val - sp.hp_max_fraction) < 1e-9:
            return "no_change"
        if val < 1.0 and twin.plant_state.tank_c < sp.tank_c - 2.0 - 1e-9:
            return "IL-09:tank_short"
        return _rate_check(twin, "HP-01.MaxFraction", 900)

    if tool == "set_ahu_enabled":
        en = args.get("enabled")
        if not isinstance(en, bool):
            return "invalid_value"
        if en is sp.ahu_enabled:
            return "no_change"
        if not en and occupied:
            return "IL-06:occupied_ventilation"
        return _rate_check(twin, "AHU-01.Enabled", 900)

    if tool == "set_heating_cable_mode":
        circuit = args.get("circuit")
        mode = args.get("mode")
        if circuit not in CIRCUITS or mode not in CABLE_MODES:
            return "invalid_value"
        cur = sp.cable_modes.get(circuit, "auto")
        if mode == cur:
            return "no_change"
        wet = bool(twin.points.get("OUT-01.SurfaceWet", 0.0))
        out_c = twin.scenario.outdoor_c(now)
        if mode == "off" and wet and -10.0 <= out_c <= 1.0:
            return "IL-10:freezing_wet_surface"
        return _rate_check(twin, f"{circuit}.Mode", 900)

    return "unknown_tool"

