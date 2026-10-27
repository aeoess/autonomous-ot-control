"""P3, the caretaker: safety overlays, one base profile, an algorithm per setpoint, an independent validator.

The design follows a hybrid hierarchy:

    site policy (hard constraints) -> safety overlays -> one base profile
        -> one algorithm per setpoint -> proposal
        -> independent validator -> deterministic fallback if invalid
        -> decision record (what REMORA receives as evidence with the write)

The parts:
- **Overlays** are constraints, not competing profiles. `frost_protection`,
  `iaq_protection`, `icing_protection` and `telemetry_degraded` can all be
  active at once on top of any base profile. A demand-response hour cannot
  switch off frost protection.
- **Base profiles**, highest priority first: `comfort_priority` (occupied),
  `demand_response` (top-price hour, or power near the month's peak),
  `preconditioning` (an optimal-start recovery, or a high-price block ahead
  of occupancy), `night_setback` (>= 10 h to occupancy), `energy_saving`
  (otherwise). `balanced_default` is what the fallback runs.
- **Algorithms** are small, versioned and explainable. Every one is named in
  the decision record:
  - optimal start with a self-tuning reheat rate;
  - recovery-limited setback;
  - an unoccupied cooling setback;
  - ASHRAE Guideline 36 style Trim & Respond on the supply-air temperature;
  - a summer night purge through the heat-recovery bypass;
  - a weather-reset tank with price-ranked charging;
  - a peak limiter;
  - presence lighting with daylight dimming;
  - ground-sensor cables.
  None of them is an MPC. A model-predictive controller would slot in as
  another algorithm behind the same validator.
- **The validator** checks each proposal against `deploy/ot/site_policy.v1.json`
  and the active overlays. It reads the twin's own occupancy and state, never
  the optimizer's view of it. A failed proposal is replaced by the
  hash-pinned fallback in `deploy/ot/caretaker_fallback.v1.json`. That
  fallback reads no zone sensor, and it is validated too. If it also fails,
  the caretaker holds the current state and escalates.
- **Telemetry checks**: a reading older than the guard's observation age is
  stale. A reading that jumps more than is physically plausible in one step
  is suspect. Either puts the zone on the fallback and escalates once. The
  zone recovers only when a recovery criterion holds (fresh again, or the
  reverse jump) for an hour, and never in under 24 h.
- **Optimizer faults** (`proposal_fault_rate`, seeded): a share of the
  proposals is corrupted before validation, as a defective or compromised
  optimizer would emit them. The validator must catch every one; `P3raw`
  applies them.

`P3raw` is the same proposal engine with the telemetry checks and the
validator removed: the B arm of an A/B/C comparison. P0 is A, P3 is C. It
runs offline only. A real building never receives an unvalidated write.

With REMORA in the loop, reads ACCEPT under the grounded-read rule, and each
submitted write goes as a VERIFY with its decision record as evidence.
`escalate` is the caretaker asking for a human. REMORA's own decision is
separate and is not predicted here.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from runner.demo import STEP_S, ZONES, ReheatLearner, next_step, p1_target
from twin.model import Twin
from twin.plant import weather_compensated_supply_c

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "deploy" / "ot" / "site_policy.v1.json"
FALLBACK_PATH = ROOT / "deploy" / "ot" / "caretaker_fallback.v1.json"
VERSION = "p3/1"
LAB = "ZONE-03"
OVERLAYS = ("frost_protection", "iaq_protection", "icing_protection", "telemetry_degraded")
PROFILES = ("comfort_priority", "demand_response", "preconditioning", "night_setback", "energy_saving",
            "balanced_default")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    raw = json.loads(path.read_text("utf-8"))
    return {k: v["value"] for k, v in raw["constraints"].items()}


@dataclass
class Proposal:
    heating: dict[str, float]
    cooling: dict[str, float]
    supply_air_c: float
    tank_c: float
    hp_max_fraction: float
    ahu_enabled: bool
    cables: str
    lighting_mode: str = "presence"
    daylight_harvesting: bool = True
    co2_ppm: dict[str, float] = field(default_factory=dict)
    algorithms: dict[str, str] = field(default_factory=dict)


@dataclass
class Stats:
    profiles: dict[str, int] = field(default_factory=lambda: dict.fromkeys(PROFILES, 0))
    overlays: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OVERLAYS, 0))
    routes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(("submit", "hold", "fallback", "escalate"), 0))
    violations: dict[str, int] = field(default_factory=dict)
    algorithms: dict[str, int] = field(default_factory=dict)
    zone_fallback_steps: int = 0
    purge_steps: int = 0
    #: FDD scored against the injected faults (the detector never reads them).
    fdd_alarms: int = 0
    fdd_true_alarms: int = 0
    fdd_episodes: int = 0
    fdd_episodes_detected: int = 0
    #: Corrupted proposals injected, and how many of them the validator caught.
    optimizer_faults: dict[str, int] = field(default_factory=dict)
    optimizer_faults_caught: int = 0


class Caretaker:
    """Drive the twin's supervisory layer every 15 simulated minutes."""

    def __init__(self, tw: Twin, *, validated: bool = True, record_days: set[str] | None = None,
                 adaptive_deadband: bool = False, proposal_fault_rate: float = 0.0, seed: int = 0) -> None:
        self.tw, self.validated, self.adaptive_deadband = tw, validated, adaptive_deadband
        self.proposal_fault_rate = proposal_fault_rate
        self._fault_rng = random.Random(f"{seed}:optimizer-faults")
        self.policy = load_policy()
        self.fallback = json.loads(FALLBACK_PATH.read_text("utf-8"))
        self.fallback_sha = _sha(FALLBACK_PATH)
        self.policy_sha = _sha(POLICY_PATH)
        self.learner = ReheatLearner()
        self.stats = Stats()
        self.records: list[dict[str, Any]] = []
        self.record_days = record_days or set()
        self.changes = 0
        self._prev_reading: dict[str, float] = {}
        self._fault: dict[str, dict[str, Any]] = {}
        self._sat = float(tw.setpoints.supply_air_c)
        self._purging = False
        self._day_prices: dict[int, list[float]] = {}
        self._detected: set[tuple[str, str, int]] = set()
        self.alarms: list[dict[str, Any]] = []

    # -- context ----------------------------------------------------------------
    def _prices(self, day: int) -> list[float]:
        if day not in self._day_prices:
            sc = self.tw.scenario
            self._day_prices[day] = [sc.price_per_kwh(min(day * 86400 + h * 3600, sc.duration_seconds - 1))
                                     for h in range(24)]
        return self._day_prices[day]

    def _price_rank(self, t: int) -> tuple[float, float, list[int], list[int]]:
        """(percentile of this hour in its day, the day's spread, cheapest 4 hours, top 3 hours)."""
        ps = self._prices(t // 86400)
        hour = (t // 3600) % 24
        order = sorted(range(24), key=lambda h: ps[h])
        pct = order.index(hour) / 23
        spread = max(ps) - min(ps)
        return pct, spread, order[:4], order[-3:]

    def _next_occupancy(self, t: int, horizon: int = 3 * 86400) -> int | None:
        sc = self.tw.scenario
        for u in range(t, min(t + horizon, sc.duration_seconds), STEP_S):
            if sc.occupied(u):
                return u
        return None

    def _telemetry(self, t: int) -> dict[str, dict[str, Any]]:
        """Each zone's reading and whether the caretaker trusts it."""
        out = {}
        for z in ZONES:
            r = self.tw.read_zone(z)
            prev = self._prev_reading.get(z)
            stale = r.age_seconds > self.policy["max_observation_age_s"]
            jump = prev is not None and abs(r.measured_temp_c - prev) > self.policy["max_reading_jump_k_per_step"]
            wrong_unit = r.unit != "degC"
            delta = 0.0 if prev is None else r.measured_temp_c - prev
            newly = False
            fault = self._fault.get(z)
            if self.validated and fault is None and (stale or jump or wrong_unit):
                rule = "stale" if stale else "unit" if wrong_unit else "jump"
                self._fault[z] = {"rule": rule, "sign": (delta > 0) - (delta < 0), "since": t, "recovered_at": None}
                newly = True
                self._alarm(t, z, r.measured_temp_c, prev, r.age_seconds, rule)
            elif fault is not None:
                # Recovery criteria: a stale sensor is fresh again; a jumped one has
                # jumped back by as much in the other direction; a wrong unit is
                # right again. Then it must hold for recovery_hold_s, and the zone
                # stays distrusted for at least suspect_latch_s in all.
                rule = fault["rule"]
                if rule == "stale":
                    ok = not stale
                elif rule == "unit":
                    ok = not wrong_unit
                else:
                    ok = fault["recovered_at"] is not None or (jump and (delta > 0) - (delta < 0) == -fault["sign"])
                if not ok:
                    fault["recovered_at"] = None
                elif fault["recovered_at"] is None:
                    fault["recovered_at"] = t
                if (fault["recovered_at"] is not None
                        and t - fault["recovered_at"] >= self.policy["recovery_hold_s"]
                        and t - fault["since"] >= self.policy["suspect_latch_s"]):
                    del self._fault[z]
            self._prev_reading[z] = r.measured_temp_c
            suspect = self.validated and z in self._fault
            out[z] = {"temp_c": r.measured_temp_c, "age_s": r.age_seconds, "stale": stale, "jump": jump,
                      "suspect": suspect, "newly_suspect": newly}
        return out

    def _alarm(self, t: int, zone: str, reading: float, prev: float | None, age: int, rule: str) -> None:
        """Record an FDD alarm with its evidence, then score it against the injected faults."""
        truth = [d for d in self.tw.scenario.disturbances
                 if d.zone_id == zone and d.from_s - STEP_S <= t <= d.to_s + STEP_S]
        self.stats.fdd_alarms += 1
        self.stats.fdd_true_alarms += bool(truth)
        for d in truth:
            self._detected.add((d.kind, d.zone_id, d.from_s))
        self.alarms.append({"t_s": t, "utc": self.tw.clock.utc_at(t).isoformat().replace("+00:00", "Z"),
                            "zone": zone, "rule": rule, "reading_c": round(reading, 2),
                            "previous_c": None if prev is None else round(prev, 2), "age_s": age,
                            "threshold": {"max_observation_age_s": self.policy["max_observation_age_s"],
                                          "max_reading_jump_k_per_step": self.policy["max_reading_jump_k_per_step"]},
                            "action": "zone on the fallback until the recovery criterion holds; escalated",
                            "recovery": {"stale": "fresh for recovery_hold_s", "jump": "the reverse jump, then recovery_hold_s",
                                         "unit": "degC again for recovery_hold_s"}[rule],
                            "ground_truth": [d.kind for d in truth] or ["none (false alarm)"]})

    def finish(self) -> None:
        """Close the run: the FDD recall over the fault episodes that started in it."""
        dur = self.tw.scenario.duration_seconds
        eps = {(d.kind, d.zone_id, d.from_s) for d in self.tw.scenario.disturbances if d.from_s < dur}
        self.stats.fdd_episodes = len(eps)
        self.stats.fdd_episodes_detected = len(eps & self._detected)

    # -- one control step --------------------------------------------------------
    def step(self, t: int) -> dict[str, Any]:
        tw, sc, pol = self.tw, self.tw.scenario, self.policy
        tel = self._telemetry(t)
        occupied = sc.occupied(t)
        nxt = self._next_occupancy(t)
        to_occ = None if nxt is None else nxt - t
        out_c, mean24 = sc.outdoor_c(t), sc.mean_outdoor_24h(t)
        pct, spread, cheap, top = self._price_rank(t)
        if spread < 0.40:   # not worth the storage loss on a flat day
            cheap = []
        hour = (t // 3600) % 24
        pts = tw.points
        co2 = {z: pts.get(f"{z}.CO2", 420.0) for z in ZONES}
        wet = bool(pts.get("OUT-01.SurfaceWet", 0.0))
        lo_ice, hi_ice = pol["icing_band_c"]
        heating_season = bool(pts.get("HYD-01.HeatingSeason", 1.0))
        month = tw.clock.utc_at(t).strftime("%Y-%m")
        peak_kw = getattr(tw, "monthly_peak_kw", {}).get(month, 0.0)
        power_kw = tw.power_w / 1000
        tank = tw.plant_state.tank_c

        # -- overlays (constraints on top of any profile) --
        overlays = []
        if (any(tw.zones[z].temp_c < pol["frost_overlay_zone_c"] for z in ZONES)
                or mean24 < pol["frost_overlay_outdoor_mean_c"]):
            overlays.append("frost_protection")
        if any(c > pol["iaq_overlay_co2_ppm"] for c in co2.values()):
            overlays.append("iaq_protection")
        if wet and lo_ice <= out_c <= hi_ice:
            overlays.append("icing_protection")
        if any(v["suspect"] for v in tel.values()):
            overlays.append("telemetry_degraded")

        # -- one base profile --
        high_price = spread >= 0.40 and hour in top
        dr_block_soon = spread >= 0.40 and any(((hour + k) % 24) in top for k in (1, 2))
        near_peak = peak_kw > 0 and power_kw > 0.9 * peak_kw
        recovering = any(self.learner.preheating(z) for z in ZONES)
        if occupied:
            profile = "comfort_priority"
        elif high_price or near_peak:
            profile = "demand_response"
        elif recovering or (heating_season and dr_block_soon and to_occ is not None and to_occ <= 6 * 3600):
            profile = "preconditioning"
        elif to_occ is None or to_occ >= 10 * 3600:
            profile = "night_setback"
        else:
            profile = "energy_saving"

        prop = self._propose(t, profile, overlays, tel, occupied, to_occ, out_c, mean24, cheap, top, hour,
                             heating_season, tank, near_peak, high_price)
        corrupted = None
        if self.proposal_fault_rate and self._fault_rng.random() < self.proposal_fault_rate:
            corrupted = self._fault_rng.choice(OPTIMIZER_FAULTS)
            prop = corrupt(prop, corrupted)
            self.stats.optimizer_faults[corrupted] = self.stats.optimizer_faults.get(corrupted, 0) + 1
        violations = self.validate(prop, t, overlays) if self.validated else []
        if corrupted and violations:
            self.stats.optimizer_faults_caught += 1
        route = "submit"
        used_fallback = False
        if violations:
            fb = self._fallback(t, overlays)
            fb_viol = self.validate(fb, t, overlays)
            if fb_viol:
                route = "escalate"
                prop = None
            else:
                route, prop, used_fallback = "fallback", fb, True
            for v in violations:
                self.stats.violations[v] = self.stats.violations.get(v, 0) + 1
        if any(v["newly_suspect"] for v in tel.values()):
            route = "escalate" if route == "submit" else route
        changed = self._apply(t, prop) if prop is not None else []
        if route == "submit" and not changed:
            route = "hold"

        st = self.stats
        st.profiles[profile] += 1
        for o in overlays:
            st.overlays[o] += 1
        st.routes[route] += 1
        if prop is not None:
            for a in set(prop.algorithms.values()):
                st.algorithms[a] = st.algorithms.get(a, 0) + 1
        st.zone_fallback_steps += sum(1 for v in tel.values() if v["suspect"])
        st.purge_steps += self._purging

        rec = None
        day = tw.clock.utc_at(t).date().isoformat()
        if day in self.record_days:
            ctx = {"price_nok_per_kwh": round(sc.price_per_kwh(t), 4), "price_percentile_day": round(pct, 3),
                   "price_spread_day": round(spread, 3), "outdoor_c": round(out_c, 2), "outdoor_mean_24h_c": round(mean24, 2),
                   "occupied": occupied, "next_occupancy_h": None if to_occ is None else round(to_occ / 3600, 2),
                   "heating_season": heating_season, "surface_wet": wet, "tank_c": round(tank, 2),
                   "power_kw": round(power_kw, 2), "month_peak_kw": round(peak_kw, 2),
                   "co2_ppm": {z: round(c) for z, c in co2.items()},
                   "telemetry": {z: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}
                                 for z, r in tel.items()}}
            rec = {"schema": "ot-caretaker-decision/v1", "engine": VERSION, "validated": self.validated,
                   "t_s": t, "utc": tw.clock.utc_at(t).isoformat().replace("+00:00", "Z"),
                   "context": ctx,
                   "context_sha256": hashlib.sha256(json.dumps(ctx, sort_keys=True).encode()).hexdigest(),
                   "site_policy_sha256": self.policy_sha, "overlays": overlays, "base_profile": profile,
                   "algorithms": prop.algorithms if prop else {}, "proposal": _plain(prop) if prop else None,
                   "validation": {"ok": not violations, "violations": violations},
                   "fallback": {"used": used_fallback, "sha256": self.fallback_sha} if used_fallback else None,
                   "changes": changed, "route": route}
            self.records.append(rec)
        return rec or {"route": route}

    # -- proposal ---------------------------------------------------------------------
    def _propose(self, t: int, profile: str, overlays: list[str], tel: dict[str, dict[str, Any]],
                 occupied: bool, to_occ: int | None, out_c: float, mean24: float, cheap: list[int],
                 top: list[int], hour: int, heating_season: bool, tank: float, near_peak: bool,
                 high_price: bool) -> Proposal:
        tw, sc, pol = self.tw, self.tw.scenario, self.policy
        algos: dict[str, str] = {}
        heating: dict[str, float] = {}
        frost = "frost_protection" in overlays
        for z in ZONES:
            reading = tel[z]["temp_c"]
            if tel[z]["suspect"]:
                heating[z] = self._fallback_heating(t, z)
                algos[f"{z}.heating"] = "fallback_schedule"
                continue
            tgt, why = p1_target(sc, t, z, reading, self.learner, max_lead_h=12.0)
            self.learner.observe(z, t, reading, why.startswith("optimal start"))
            algo = "occupied_schedule" if why == "occupied" else (
                "constant_laboratory" if z == LAB else "optimal_start" if why.startswith("optimal") else "setback")
            if z != LAB and tgt < 21.0 and not self.learner.preheating(z):
                if to_occ is None or to_occ >= 10 * 3600:
                    tgt = pol["setback_floor_c"] if mean24 >= 0 else 17.0 if mean24 >= -5 else 18.0
                    algo = "recovery_setback"
                if profile == "demand_response" and high_price:
                    tgt = max(pol["setback_floor_c"], tgt - 1.0)
                    algo = "dr_offset"
            if (profile == "preconditioning" and z != LAB and to_occ is not None and to_occ <= 3 * 3600
                    and any(((hour + k) % 24) in top for k in (1, 2))):
                tgt = min(21.5, max(tgt, 21.0) + 0.5)
                algo = "dr_precondition"
            if self.adaptive_deadband and algo == "occupied_schedule" and z != LAB:
                # Adaptive deadband: in an expensive hour the occupied heating
                # setpoint gives way by up to 0.5 K, never below the comfort
                # floor plus a 0.5 K margin (delta = min(0.5, 0.5 * z_p),
                # z_p = the price's excess over the day's median, relative).
                # Off by default: measured on NO1 2025 it saved 9 kWh and 31 NOK
                # spot but cost 268 NOK more power charge, because the room
                # reheats after the expensive hour (ADR-009, negative result).
                ps = sorted(self._prices(t // 86400))
                med = (ps[11] + ps[12]) / 2
                zp = max(0.0, (sc.price_per_kwh(t) - med) / med) if med > 0 else 0.0
                delta = min(0.5, 0.5 * zp)
                if delta >= 0.1:
                    tgt = max(pol["occupied_heating_min_c"] + 0.5, tgt - round(delta, 1))
                    algo = "adaptive_deadband"
            if frost:
                tgt = max(tgt, pol["frost_overlay_heating_min_c"])
                algo += "+frost"
            heating[z] = tgt
            algos[f"{z}.heating"] = algo

        # Cooling: in band while occupied (and 1 h before), set back when empty.
        cooling = {}
        soon = occupied or (to_occ is not None and to_occ <= 3600)
        for z in ZONES:
            c = 23.5 if (soon or z == LAB) else pol["cooling_max_c"]
            cooling[z] = c
        algos["cooling"] = "occupied_cooling" if soon else "unoccupied_cooling_setback"

        # Summer night purge through the HRV bypass.
        temps = [tel[z]["temp_c"] for z in ZONES if not tel[z]["suspect"]] or [tw.zones[z].temp_c for z in ZONES]
        zmean = sum(temps) / len(temps)
        night = hour >= 20 or hour < 4
        if self._purging:
            self._purging = (not heating_season and not occupied and night and zmean > 21.0 and out_c < zmean - 2.0)
        else:
            self._purging = (not heating_season and not occupied and night and zmean > 22.5
                             and 10.0 <= out_c < zmean - 3.0)

        # Supply air: Trim & Respond outside the heating season, a fixed reset in it.
        if self._purging:
            target_sat = pol["supply_air_min_c"]
            algos["supply_air"] = "night_purge"
        elif heating_season:
            target_sat = 19.0
            algos["supply_air"] = "fixed_reset"
        else:
            requests = sum(1 for z in ZONES if not tel[z]["suspect"] and tel[z]["temp_c"] > cooling[z] - 0.5)
            if requests > 0:   # respond: colder air, at most 1 K per step
                target_sat = self._sat - min(0.5 * requests, 1.0)
            else:              # trim: warmer air, less coil cooling
                target_sat = self._sat + 0.5
            algos["supply_air"] = "trim_respond"
        sat = max(pol["supply_air_min_c"], min(pol["supply_air_max_c"], target_sat))
        sat = self._sat + max(-pol["supply_air_max_step_k"], min(pol["supply_air_max_step_k"], sat - self._sat))

        ahu = (occupied or (to_occ is not None and to_occ <= 3600) or self._purging
               or "iaq_protection" in overlays)
        algos["ahu"] = "occupancy_schedule" + ("+purge" if self._purging else "")

        # Tank: weather reset, charged in the day's cheapest hours, held before a price block.
        need = weather_compensated_supply_c(out_c) + 5.0
        base = min(55.0, max(45.0, need))
        if hour in cheap and heating_season:
            tgt_tank, algos["tank"] = min(pol["tank_max_c"], base + 5.0), "price_ranked_charge"
        else:
            tgt_tank, algos["tank"] = base, "weather_reset"
        cur = tw.setpoints.tank_c
        tank_sp = cur + max(-pol["tank_max_step_k"], min(pol["tank_max_step_k"], tgt_tank - cur))

        cap = 1.0
        algos["heat_pump"] = "uncapped"
        recovering = any(self.learner.preheating(z) for z in ZONES)
        if not frost and not recovering and (near_peak or (profile == "demand_response" and high_price)) \
                and tank >= tank_sp - 2.0:
            cap, algos["heat_pump"] = 0.6, "peak_limiter"
        algos["lighting"] = "presence_daylight"
        algos["cables"] = "ground_sensor"
        return Proposal(heating=heating, cooling=cooling, supply_air_c=round(sat, 2), tank_c=round(tank_sp, 2),
                        hp_max_fraction=cap, ahu_enabled=ahu, cables="sensor",
                        co2_ppm={z: zs.co2_ppm for z, zs in tw.setpoints.zones.items()}, algorithms=algos)

    # -- fallback ---------------------------------------------------------------------
    def _fallback_heating(self, t: int, z: str) -> float:
        fb = self.fallback["heating"]
        if z == LAB:
            return fb["laboratory_c"]
        sc = self.tw.scenario
        soon = sc.occupied(t) or any(sc.occupied(min(t + k, sc.duration_seconds - 1))
                                     for k in range(0, fb["occupied_or_within_s"] + 1, STEP_S))
        return fb["occupied_c"] if soon else fb["otherwise_c"]

    def _fallback(self, t: int, overlays: list[str]) -> Proposal:
        fb, sc, tw = self.fallback, self.tw.scenario, self.tw
        heating = {z: self._fallback_heating(t, z) for z in ZONES}
        if "frost_protection" in overlays:
            heating = {z: max(v, self.policy["frost_overlay_heating_min_c"]) for z, v in heating.items()}
        within = fb["ahu"]["on_when_occupied_or_within_s"]
        ahu = sc.occupied(t) or any(sc.occupied(min(t + k, sc.duration_seconds - 1))
                                    for k in range(0, within + 1, STEP_S)) or "iaq_protection" in overlays
        tk = fb["tank"]
        tgt = min(tk["max_c"], max(tk["min_c"], weather_compensated_supply_c(sc.outdoor_c(t)) + tk["follow_weather_curve_plus_k"]))
        cur = tw.setpoints.tank_c
        tank_sp = cur + max(-self.policy["tank_max_step_k"], min(self.policy["tank_max_step_k"], tgt - cur))
        sat = self._sat + max(-1.0, min(1.0, fb["supply_air_c"] - self._sat))
        return Proposal(heating=heating, cooling=dict.fromkeys(ZONES, fb["cooling_c"]), supply_air_c=round(sat, 2),
                        tank_c=round(tank_sp, 2), hp_max_fraction=fb["hp_max_fraction"], ahu_enabled=ahu,
                        cables=fb["cables"], lighting_mode=fb["lighting"]["mode"],
                        daylight_harvesting=fb["lighting"]["daylight_harvesting"],
                        co2_ppm={z: zs.co2_ppm for z, zs in tw.setpoints.zones.items()},
                        algorithms=dict.fromkeys(("heating", "cooling", "supply_air", "tank", "ahu"), "fallback"))

    # -- validator (independent of the proposal engine) --------------------------------
    def validate(self, p: Proposal, t: int, overlays: list[str]) -> list[str]:
        """Every hard constraint the proposal breaks, by interlock id. Reads the twin, not the caretaker."""
        tw, sc, pol = self.tw, self.tw.scenario, self.policy
        occupied = sc.occupied(t)
        v: list[str] = []
        for z in ZONES:
            h, c = p.heating[z], p.cooling[z]
            if c < h + pol["heating_cooling_deadband_k"]:
                v.append(f"IL-01:{z}")
            if occupied and h < pol["occupied_heating_min_c"]:
                v.append(f"IL-02:{z}")
            if h < max(pol["frost_floor_c"], pol["setback_floor_c"]):
                v.append(f"IL-03:{z}")
            if "frost_protection" in overlays and h < pol["frost_overlay_heating_min_c"]:
                v.append(f"OVL-frost:{z}")
            if c > pol["cooling_max_c"]:
                v.append(f"cooling_max:{z}")
            if not pol["co2_setpoint_min_ppm"] <= p.co2_ppm.get(z, 900.0) <= pol["co2_setpoint_max_ppm"]:
                v.append(f"IL-07:{z}")
        if not pol["tank_min_c"] <= p.tank_c <= pol["tank_max_c"]:
            v.append("IL-04:range")
        if abs(p.tank_c - tw.setpoints.tank_c) > pol["tank_max_step_k"] + 1e-9:
            v.append("IL-04:step")
        if not pol["supply_air_min_c"] <= p.supply_air_c <= pol["supply_air_max_c"]:
            v.append("IL-05:range")
        if abs(p.supply_air_c - tw.setpoints.supply_air_c) > pol["supply_air_max_step_k"] + 1e-9:
            v.append("IL-11:supply_air_step")
        if occupied and not p.ahu_enabled:
            v.append("IL-06")
        if "iaq_protection" in overlays and not p.ahu_enabled:
            v.append("OVL-iaq")
        if occupied and p.lighting_mode not in ("presence", "schedule"):
            v.append("IL-08")
        if p.hp_max_fraction < 1.0 and tw.plant_state.tank_c < p.tank_c - 2.0:
            v.append("IL-09")
        if p.hp_max_fraction < 1.0 and "frost_protection" in overlays:
            v.append("OVL-frost:hp_cap")
        if p.cables in ("off", "thermostat") and "icing_protection" in overlays:
            v.append("IL-10")
        return v

    # -- apply ------------------------------------------------------------------------
    def _apply(self, t: int, p: Proposal) -> list[str]:
        tw = self.tw
        changed = []
        for z in ZONES:
            nv = next_step(tw.zones[z].setpoint_c, p.heating[z])   # IL-11: <= 1 K per change
            if nv is not None:
                rec, _ = tw.apply_setpoint({"operation_id": f"P3-{t}-{z}", "zone_id": z, "setpoint_c": nv,
                                            "units": "degC", "expected_state_revision": tw.revision})
                if rec.status == "APPLIED":
                    self.changes += 1
                    changed.append(f"{z}.HeatingSetpoint={nv}")
        sp = tw.setpoints
        zones = {}
        for z, zs in sp.zones.items():
            cooling = max(p.cooling[z], tw.zones[z].setpoint_c + 2.5)
            new = replace(zs, cooling_c=cooling, lighting_mode=p.lighting_mode,
                          daylight_harvesting=p.daylight_harvesting)
            if new != zs:
                changed.append(f"{z}.CoolingSetpoint={cooling}" if cooling != zs.cooling_c else f"{z}.Lighting")
            zones[z] = new
        new_sp = replace(sp, zones=zones, supply_air_c=p.supply_air_c, tank_c=p.tank_c,
                         hp_max_fraction=p.hp_max_fraction, ahu_enabled=p.ahu_enabled,
                         cable_modes={"CABLE-RAMP": p.cables, "CABLE-GUTTER": p.cables})
        for k in ("supply_air_c", "tank_c", "hp_max_fraction", "ahu_enabled", "cable_modes"):
            if getattr(new_sp, k) != getattr(sp, k):
                changed.append(f"{k}={getattr(new_sp, k)}")
        tw.setpoints = new_sp
        self._sat = p.supply_air_c
        return changed


OPTIMIZER_FAULTS = ("tank_overtemperature", "supply_air_too_cold", "ahu_off", "cables_off",
                    "heating_too_low", "deadband_inverted")


def corrupt(p: Proposal, kind: str) -> Proposal:
    """A proposal as a defective optimizer would emit it (test injection only)."""
    if kind == "tank_overtemperature":
        return replace(p, tank_c=70.0)
    if kind == "supply_air_too_cold":
        return replace(p, supply_air_c=10.0)
    if kind == "ahu_off":
        return replace(p, ahu_enabled=False)
    if kind == "cables_off":
        return replace(p, cables="off")
    if kind == "heating_too_low":
        return replace(p, heating=dict.fromkeys(p.heating, 12.0))
    if kind == "deadband_inverted":
        return replace(p, cooling={z: h - 1.0 for z, h in p.heating.items()})
    raise ValueError(kind)


def _plain(p: Proposal) -> dict[str, Any]:
    d = asdict(p)
    d.pop("algorithms")
    return d
