"""Exogenous scenario series: weather, occupancy, internal gains, price.

Every series is explicit. A missing or malformed field raises instead of
defaulting to zero (SDD §5).
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from twin.clock import parse_utc
from twin.state import canonical_digest

SCENARIO_SCHEMA = "ot-scenario/v1"
_DISTURBANCE_KINDS = frozenset({"sensor_freeze"})


class ScenarioError(ValueError):
    pass


def _req(d: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in d:
        raise ScenarioError(f"scenario field missing: {key}")
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, kind):
        raise ScenarioError(f"scenario field {key} has wrong type")
    return v


@dataclass(frozen=True)
class Disturbance:
    kind: str
    zone_id: str
    from_s: int
    to_s: int

    def active(self, t_s: int) -> bool:
        return self.from_s <= t_s < self.to_s


@dataclass
class Scenario:
    raw: dict[str, Any]
    scenario_id: str
    seed: int
    start_utc: str
    duration_seconds: int
    disturbances: tuple[Disturbance, ...] = field(default_factory=tuple)
    _noise: list[float] = field(default_factory=list, repr=False)
    _noise_step: int = 60

    @property
    def digest(self) -> str:
        return canonical_digest(self.raw)

    # -- exogenous series ---------------------------------------------------
    def outdoor_c(self, t_s: int) -> float:
        o = self.raw["outdoor"]
        hour = self._hour_utc(t_s)
        phase = 2.0 * math.pi * (hour - float(o["coldest_hour_utc"])) / 24.0
        base = float(o["mean_c"]) - float(o["amplitude_c"]) * math.cos(phase)
        idx = min(t_s // self._noise_step, len(self._noise) - 1)
        return base + self._noise[idx]

    def occupied(self, t_s: int) -> bool:
        start_h, end_h = self.raw["occupancy"]["weekday_hours_utc"]
        dt = parse_utc(self.start_utc).timestamp() + t_s
        d = datetime.fromtimestamp(dt, UTC)
        return d.weekday() < 5 and start_h <= d.hour < end_h

    def gain_w_per_m2(self, t_s: int) -> float:
        occ = self.raw["occupancy"]
        return float(occ["gain_w_per_m2"] if self.occupied(t_s) else occ["unoccupied_gain_w_per_m2"])

    def price_per_kwh(self, t_s: int) -> float:
        p = self.raw["price"]
        hour = self._hour_utc(t_s)
        for start_h, end_h in p["peak_hours_utc"]:
            if start_h <= hour < end_h:
                return float(p["peak_nok_per_kwh"])
        return float(p["base_nok_per_kwh"])

    def comfort_band(self) -> tuple[float, float]:
        c = self.raw["comfort"]
        return float(c["occupied_min_c"]), float(c["occupied_max_c"])

    def sensor_frozen(self, zone_id: str, t_s: int) -> bool:
        return any(d.kind == "sensor_freeze" and d.zone_id == zone_id and d.active(t_s)
                   for d in self.disturbances)

    def _hour_utc(self, t_s: int) -> float:
        start = parse_utc(self.start_utc)
        secs = start.hour * 3600 + start.minute * 60 + start.second + t_s
        return (secs % 86400) / 3600.0


def load_scenario(source: str | Path | dict[str, Any]) -> Scenario:
    raw = json.loads(Path(source).read_text("utf-8")) if not isinstance(source, dict) else source
    if raw.get("schema") != SCENARIO_SCHEMA:
        raise ScenarioError(f"unsupported scenario schema {raw.get('schema')!r}")
    sid = _req(raw, "scenario_id", str)
    seed = _req(raw, "seed", int)
    start = _req(raw, "start_utc", str)
    parse_utc(start)
    duration = _req(raw, "duration_seconds", int)
    if duration <= 0:
        raise ScenarioError("duration_seconds must be positive")
    o = _req(raw, "outdoor", dict)
    for k in ("mean_c", "amplitude_c", "coldest_hour_utc", "noise_std_c"):
        _req(o, k, (int, float))
    occ = _req(raw, "occupancy", dict)
    hours = _req(occ, "weekday_hours_utc", list)
    if len(hours) != 2 or not all(isinstance(h, int) and 0 <= h <= 24 for h in hours) or hours[0] >= hours[1]:
        raise ScenarioError("occupancy.weekday_hours_utc must be [start, end) integer hours")
    _req(occ, "gain_w_per_m2", (int, float))
    _req(occ, "unoccupied_gain_w_per_m2", (int, float))
    p = _req(raw, "price", dict)
    _req(p, "base_nok_per_kwh", (int, float))
    _req(p, "peak_nok_per_kwh", (int, float))
    for pair in _req(p, "peak_hours_utc", list):
        if not (isinstance(pair, list) and len(pair) == 2 and pair[0] < pair[1]):
            raise ScenarioError("price.peak_hours_utc entries must be [start, end)")
    c = _req(raw, "comfort", dict)
    lo, hi = _req(c, "occupied_min_c", (int, float)), _req(c, "occupied_max_c", (int, float))
    if lo >= hi:
        raise ScenarioError("comfort band is empty")
    dist = []
    for d in _req(raw, "disturbances", list):
        if d.get("kind") not in _DISTURBANCE_KINDS:
            raise ScenarioError(f"unknown disturbance kind {d.get('kind')!r}")
        dist.append(Disturbance(kind=d["kind"], zone_id=_req(d, "zone_id", str),
                                from_s=_req(d, "from_s", int), to_s=_req(d, "to_s", int)))

    sc = Scenario(raw=raw, scenario_id=sid, seed=seed, start_utc=start,
                  duration_seconds=duration, disturbances=tuple(dist))
    rng = random.Random(seed)
    n = duration // sc._noise_step + 2
    std = float(o["noise_std_c"])
    sc._noise = [rng.gauss(0.0, std) for _ in range(n)]
    return sc
