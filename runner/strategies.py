"""Supervisory strategies for the building-services twin: P0 as found, P1, P2.

Each strategy sets the twin's supervisory layer every 15 simulated minutes,
the same points a governed tool would write (docs/datapoints.md). These
offline runs apply the values directly. With REMORA in the loop the same
values pass through assess, review and dispatch (runner.demo), and the energy
is identical when every change is approved.

| Strategy | Heating | AHU | Lighting | Cables | Tank / heat pump |
|---|---|---|---|---|---|
| **P0** as found | 21 occupied (1 h preheat), 20 otherwise | weekdays 05..19 UTC on the fixed schedule, holidays included | schedule mode, no daylight dimming | thermostat (-8..+3 degC) | 50 degC fixed, no cap |
| **P1** | P0 plus night setback to 19 and self-tuning optimal start | as P0 | as P0 | as P0 | as P0 |
| **P2** caretaker | P1 plus a weekend and holiday setback of 16, 17 or 18 degC by the 24 h outdoor mean (>= 10 h to occupancy), and an optimal-start lead of up to 12 h | occupancy-aware: on 1 h before people arrive, off when they leave, holidays respected | presence mode with daylight dimming | ground-sensor (snow-melt) control | following the radiator curve (curve + 5 K, 45..55 degC), +5 K in the day's cheapest 4 hours when the spread is >= 0.40 NOK/kWh; a peak limiter caps the compressor at 60 % while building power is near the month's peak and the tank holds (IL-09) |

Interlock rules are respected:
- IL-01: cooling stays 2.5 K above heating.
- IL-06: no AHU stop while occupied.
- IL-09: no cap with a short tank.
- IL-10: the sensor mode always heats a freezing wet surface.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from runner.baseline import target_setpoint
from runner.demo import STEP_S, ZONES, ReheatLearner, next_step, p1_target
from twin.model import Twin
from twin.plant import weather_compensated_supply_c

STRATEGIES = ("P0", "P1", "P2")
DEEP_C = 16.0
PEAK_FRACTION = 0.9
HP_CAP = 0.6


class Controller:
    def __init__(self, name: str, tw: Twin) -> None:
        if name not in STRATEGIES:
            raise ValueError(name)
        self.name, self.tw = name, tw
        self.learner = ReheatLearner()
        self.cheap: dict[int, set[int]] = {}
        self.changes = 0

    # -- helpers ------------------------------------------------------------------------
    def _cheapest_hours(self, day: int) -> set[int]:
        if day not in self.cheap:
            sc = self.tw.scenario
            ps = [(sc.price_per_kwh(min(day * 86400 + h * 3600, sc.duration_seconds - 1)), h) for h in range(24)]
            spread = max(p for p, _ in ps) - min(p for p, _ in ps)
            self.cheap[day] = {h for _, h in sorted(ps)[:4]} if spread >= 0.40 else set()
        return self.cheap[day]

    def _next_occupancy(self, t: int, horizon: int = 3 * 86400) -> int | None:
        sc = self.tw.scenario
        for u in range(t, min(t + horizon, sc.duration_seconds), STEP_S):
            if sc.occupied(u):
                return u
        return None

    # -- one control step -----------------------------------------------------------------
    def step(self, t: int) -> None:
        tw, sc = self.tw, self.tw.scenario
        hour = (t // 3600) % 24
        for zone in ZONES:
            z = tw.zones[zone]
            if self.name == "P0":
                tgt = target_setpoint(tw, t)
            else:
                tgt, why = p1_target(sc, t, zone, z.measured_temp_c, self.learner,
                                     max_lead_h=12.0 if self.name == "P2" else 8.0)
                self.learner.observe(zone, t, z.measured_temp_c, why.startswith("optimal start"))
                if self.name == "P2" and zone != "ZONE-03" and tgt < 21.0 and not self.learner.preheating(zone):
                    nxt = self._next_occupancy(t)
                    if nxt is None or nxt - t >= 10 * 3600:
                        # Deeper only when it can be recovered: 16 degC at a mild
                        # 24 h mean, 17 below 0 degC, 18 below -5 degC.
                        mean = sc.mean_outdoor_24h(t)
                        tgt = DEEP_C if mean >= 0 else 17.0 if mean >= -5 else 18.0
            v = next_step(z.setpoint_c, tgt)
            if v is not None:
                rec, _ = tw.apply_setpoint({"operation_id": f"{self.name}-{t}-{zone}", "zone_id": zone,
                                            "setpoint_c": v, "units": "degC",
                                            "expected_state_revision": tw.revision})
                self.changes += rec.status == "APPLIED"
        sp = tw.setpoints
        zones = {}
        for zid, zs in sp.zones.items():
            heating = tw.zones[zid].setpoint_c
            cooling = max(zs.cooling_c, heating + 2.5)          # IL-01 deadband
            if self.name in ("P0", "P1"):
                zones[zid] = replace(zs, cooling_c=cooling, lighting_mode="schedule", daylight_harvesting=False)
            else:
                zones[zid] = replace(zs, cooling_c=cooling, lighting_mode="presence", daylight_harvesting=True)
        if self.name in ("P0", "P1"):
            ahu = sc.scheduled_open(t) or sc.scheduled_open(t + 2 * 3600) or sc.scheduled_open(t - 2 * 3600)
            cables, tank, cap = "thermostat", 50.0, 1.0
        else:
            ahu = sc.occupied(t) or sc.occupied(t + 3600)       # IL-06: never off while occupied
            cables = "sensor"
            # The tank follows the radiator curve: never below what the radiators
            # need at this outdoor temperature (+5 K), never above 55 degC. A
            # fixed low tank starves the radiators in the cold (found in NO4).
            need = weather_compensated_supply_c(sc.outdoor_c(t)) + 5.0
            base = min(55.0, max(45.0, need))
            tank = min(55.0, base + 5.0) if hour in self._cheapest_hours(t // 86400) else base
            cap = 1.0
            peaks = getattr(tw, "monthly_peak_kw", {})
            month = tw.clock.utc_at(t).strftime("%Y-%m")
            target_kw = peaks.get(month, 0.0)
            recovering = any(self.learner.preheating(z) for z in ZONES)
            if (target_kw > 0 and not recovering and tw.power_w / 1000 > PEAK_FRACTION * target_kw
                    and tw.plant_state.tank_c >= tank - 2.0):  # IL-09; comfort before peak

                cap = HP_CAP
        tw.setpoints = replace(sp, zones=zones, ahu_enabled=ahu, tank_c=tank, hp_max_fraction=cap,
                               cable_modes={"CABLE-RAMP": cables, "CABLE-GUTTER": cables})


def describe() -> list[dict[str, Any]]:
    return [{"strategy": "P0", "summary": "as found: fixed schedules, scheduled lighting, thermostat cables"},
            {"strategy": "P1", "summary": "P0 plus night setback and self-tuning optimal start"},
            {"strategy": "P2", "summary": "caretaker: P1 plus deep weekend setback, occupancy-aware AHU, "
                                          "presence lighting with daylight, sensor-controlled cables, "
                                          "price-aware tank and a peak limiter"}]
