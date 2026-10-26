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
