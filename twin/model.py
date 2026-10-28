"""Deterministic three-zone RC building twin.

Explicit-Euler integration at a fixed step. Each zone has an envelope
conductance to outdoors, conductances to adjacent zones, an effective heat
capacity, internal/occupancy gains and a local proportional heater that tracks
the zone setpoint, saturating at its rated power. Energy is accumulated
monotonically from heater power and efficiency. A setpoint change only affects
subsequent modeled heat demand.

This is a synthetic, uncalibrated model. It is not a building safety
controller and supports no real-building savings claim.
"""
from __future__ import annotations

import json
import hmac
import hashlib
import os
import tempfile
import threading
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from twin import guard as guard_mod
from twin.clock import VirtualClock, format_utc, parse_utc
from twin.guard import GuardConfig
from twin import plant as plant_mod
from twin.scenarios import Scenario
from twin.state import (QUALITY_GOOD, QUALITY_STALE, OperationRecord, ZoneReading, ZoneState,
                        canonical_digest, r6)

CONFIG_SCHEMA = "ot-twin-config/v1"
MODEL_ID = "three-zone-rc-v1"
#: The same zones and envelope with building services (twin/plant.py).
HVAC_MODEL_ID = "three-zone-hvac-v1"
MODEL_IDS = (MODEL_ID, HVAC_MODEL_ID)
SNAPSHOT_SCHEMA = "ot-twin-snapshot/v2"
#: v1 carried zones, operations and counters only; see Twin._restore for how it is handled.
LEGACY_SNAPSHOT_SCHEMA = "ot-twin-snapshot/v1"


class TwinError(RuntimeError):
    pass


class StateStoreFailed(Exception):
    """The twin could not persist a decision: nothing was applied, and it accepts no write until restarted."""


class OperationConflict(TwinError):
    """An operation ID was reused with a different request."""


class _JournalFailed(Exception):
    """The snapshot is durable but the journal line was not written (internal to ``Twin._persist``)."""


class OperationLogFull(StateStoreFailed):
    """SR-15 (F-17): the operation log holds ``Twin.MAX_OPERATIONS`` records. Nothing was applied or recorded.

    Records are never evicted, since an evicted operation id could be replayed; the twin refuses new
    operations instead, and a replay of a known one is still answered."""


def load_config(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    cfg = json.loads(Path(source).read_text("utf-8")) if not isinstance(source, dict) else source
    if cfg.get("schema") != CONFIG_SCHEMA or cfg.get("model") not in MODEL_IDS:
        raise TwinError("unsupported twin config")
    step = int(cfg["step_seconds"])
    obs = int(cfg["observation_step_seconds"])
    if step <= 0 or obs % step or int(cfg["controller_step_seconds"]) % step:
        raise TwinError("observation/controller steps must be multiples of step_seconds")
    ids = [z["id"] for z in cfg["zones"]]
    if len(ids) != 3 or len(set(ids)) != 3:
        raise TwinError("v1 twin requires exactly three distinct zones")
    for z in cfg["zones"]:
        if not (z["min_setpoint_c"] <= z["initial_setpoint_c"] <= z["max_setpoint_c"]):
            raise TwinError(f"initial setpoint outside bounds for {z['id']}")
        # Explicit-Euler stability margin for the stiffest term in the zone.
        g_sum = z["ua_w_per_k"] + z["heater_gain_w_per_k"] + 2 * cfg["envelope"]["inter_zone_conductance_w_per_k"]
        if step * g_sum / z["capacity_j_per_k"] >= 0.5:
            raise TwinError(f"step_seconds too large for numerical stability in {z['id']}")
    for a, b in cfg["adjacency"]:
        if a not in ids or b not in ids or a == b:
            raise TwinError("invalid adjacency")
    GuardConfig.from_config(cfg)
    if cfg["model"] == HVAC_MODEL_ID:
        pc = plant_mod.PlantConfig.from_dict(cfg["plant"])
        if {z.zone_id for z in pc.zones} != set(ids):
            raise TwinError("plant zones must be the twin's zones")
        if any(z["heater_gain_w_per_k"] for z in cfg["zones"]):
            raise TwinError("hvac model: heat comes from the plant; heater_gain_w_per_k must be 0")
    return cfg


class Twin:
    #: SR-15 (F-17): the most operation records one twin keeps; set per instance in tests.
    MAX_OPERATIONS = 100_000

    def __init__(self, config: dict[str, Any], scenario: Scenario, *,
                 snapshot_path: str | Path | None = None, record_trace: bool = False,
                 snapshot_key: str | None = None) -> None:
        # SR-14: snapshots are authenticated with a dedicated key (never a bearer token). Set, it is required on
        # restore; unset, a snapshot is still re-validated before it is resumed, and the event says unauthenticated.
        key = snapshot_key if snapshot_key is not None else os.environ.get("TWIN_SNAPSHOT_KEY", "")
        self._snapshot_key = key.encode("utf-8") if key else b""
        self._store_failed: str | None = None
        self.config = config
        self.scenario = scenario
        self.guard = GuardConfig.from_config(config)
        self.step_s = int(config["step_seconds"])
        self.obs_step_s = int(config["observation_step_seconds"])
        self.clock = VirtualClock(start_utc=parse_utc(scenario.start_utc), step_seconds=self.step_s)
        self._zcfg = {z["id"]: z for z in config["zones"]}
        self.zone_limits = {z["id"]: (float(z["min_setpoint_c"]), float(z["max_setpoint_c"]))
                            for z in config["zones"]}
        g = float(config["envelope"]["inter_zone_conductance_w_per_k"])
        self._neighbours: dict[str, list[tuple[str, float]]] = {z: [] for z in self._zcfg}
        for a, b in config["adjacency"]:
            self._neighbours[a].append((b, g))
            self._neighbours[b].append((a, g))
        self.zones: dict[str, ZoneState] = {
            z["id"]: ZoneState(zone_id=z["id"], tfm=z["tfm"], temp_c=float(z["initial_temp_c"]),
                               setpoint_c=float(z["initial_setpoint_c"]),
                               measured_temp_c=float(z["initial_temp_c"]), sample_at_s=0)
            for z in config["zones"]}
        self.revision = 0
        self.energy_kwh = 0.0
        self.cost = 0.0
        self.comfort_violation_dh = 0.0
        self.power_w = 0.0
        self.stop_switch = False
        self.operations: dict[str, OperationRecord] = {}  # bounded by MAX_OPERATIONS (SR-15)
        self.point_history_s: dict[str, list[int]] = {}
        self.events: list[dict[str, Any]] = []
        self.record_trace = record_trace
        self.trace: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
        # SR-15 (F-17): the operation log lives in an append-only journal next to the snapshot, one chained
        # line per operation, so persisting an operation costs one line, not a rewrite of every record.
        self._journal_path = Path(f"{snapshot_path}.journal") if snapshot_path else None
        self._chain: list[str] = []      # chain MAC after each operation, in log order
        self._journal_on_disk = 0        # lines known to be durable in the journal
        self.snapshot_migrated_from: str | None = None
        self.config_digest = canonical_digest(config)
        self.model_id = config["model"]
        self.plant: plant_mod.PlantConfig | None = None
        self.points: dict[str, float] = {}
        self.district_heat_kwh = 0.0
        self.electric_kwh = 0.0
        if self.model_id == HVAC_MODEL_ID:
            if not scenario.has_plant_inputs:
                raise TwinError("hvac model needs scenario solar, humidity and precipitation")
            self.plant = plant_mod.PlantConfig.from_dict(config["plant"])
            init = config["initial_plant"]
            self.plant_state = plant_mod.PlantState(
                tank_c=float(init["tank_c"]),
                co2_ppm={z: float(init["co2_ppm"]) for z in self._zcfg})
            ds = config["default_setpoints"]
            self.setpoints = plant_mod.Setpoints(
                zones={z: plant_mod.ZoneSetpoints(**v) for z, v in ds["zones"].items()},
                supply_air_c=float(ds["supply_air_c"]), tank_c=float(ds["tank_c"]),
                hp_max_fraction=float(ds["hp_max_fraction"]), ahu_enabled=bool(ds["ahu_enabled"]),
                cable_modes=dict(ds["cable_modes"]))
        if self._snapshot_path and self._snapshot_path.exists():
            self._restore(json.loads(self._snapshot_path.read_text("utf-8")))
        elif record_trace:
            self._trace_row()

    # -- physics --------------------------------------------------------------
    def _plant_heat(self, t: int, temps: dict[str, float],
                    dt: float) -> tuple[dict[str, float], float, float]:
        """(heat into each zone, electric W, district heat W) from the plant."""
        assert self.plant is not None
        # The zone heating setpoint is the value the governed actuator writes.
        zones = {zid: replace(self.setpoints.zones[zid], heating_c=z.setpoint_c)
                 for zid, z in self.zones.items()}
        self.setpoints = replace(self.setpoints, zones=zones)
        occupied = self.scenario.occupied(t)
        design = self.config["design_occupants"]
        sc = self.scenario
        exo = plant_mod.Exogenous(
            outdoor_c=sc.outdoor_c(t), solar_w_m2=sc.solar_w_m2(t),
            outdoor_rh_pct=sc.outdoor_rh_pct(t),
            occupants={zid: round(int(design[zid]) * sc.presence(zid, t)) if occupied else 0
                       for zid in self.zones},
            icing_risk=sc.icing_risk(t),
            heating_season=self._heating_season(t),
            equipment_factor={zid: sc.equipment_factor(zid, t) for zid in self.zones},
            surface_wet=self._surface_wet(t, dt),
            scheduled_open=occupied or sc.scheduled_open(t))
        res = plant_mod.step(self.plant, self.plant_state, temps, self.setpoints, exo, dt)
        self.plant_state = res.state
        self.points = res.points
        return res.zone_heat_w, res.electric_w, res.district_heat_w

    def _surface_wet(self, t: int, dt: float) -> bool:
        """Ramp surface model behind the ground moisture sensor.

        Precipitation wets the surface. A wet surface above +2 degC dries in
        2 h; at or below +2 degC it stays wet, snowy or iced until the snow-melt
        cable has run for 1 h without new precipitation, or the air rises
        above +2 degC. Deliberately simple and stated: a sensor reading for the
        controller, not a road-weather model.
        """
        sc = self.scenario
        if not hasattr(self, "_wet_since"):
            self._wet_since, self._melt_s, self._dry_s = None, 0.0, 0.0
        precip = sc.precipitation_mm_h(t) > 0.1
        cold = sc.outdoor_c(t) <= 2.0
        cable_on = any(self.points.get(f"{c}.Power", 0.0) > 0 for c in ("CABLE-RAMP",))
        if precip:
            self._wet_since, self._melt_s, self._dry_s = t, 0.0, 0.0
        elif self._wet_since is not None:
            if cold:
                self._melt_s = self._melt_s + dt if cable_on else self._melt_s
                if self._melt_s >= 3600:
                    self._wet_since = None
            else:
                self._dry_s += dt
                if self._dry_s >= 7200:
                    self._wet_since = None
        return self._wet_since is not None

    def _heating_season(self, t: int) -> bool:
        """Summer stop with hysteresis on the 24 h mean outdoor temperature.

        Config ``plant.heating_limit`` = {"on_below_c", "off_above_c"}; absent
        means heating is always available (the v1 behaviour). Evaluated once per
        simulated hour, as a BMS does.
        """
        lim = self.config["plant"].get("heating_limit")
        if not lim:
            return True
        hour = t // 3600
        if getattr(self, "_season_hour", None) != hour:
            mean = self.scenario.mean_outdoor_24h(t)
            prev = getattr(self, "_season_on", None)
            if prev is None:
                prev = mean < float(lim["off_above_c"])
            if prev and mean > float(lim["off_above_c"]):
                prev = False
            elif not prev and mean < float(lim["on_below_c"]):
                prev = True
            self._season_on, self._season_hour, self.outdoor_mean_24h = prev, hour, mean
        return bool(self._season_on)

    def _grid_tariff(self, t: int, kwh: float) -> None:
        """Declared reference grid tariff (config plant.grid_tariff, ASSUMPTION).

        Energy part per kWh plus a monthly power part on the month's highest
        hourly mean kW. Tracked separately from the spot cost; absent config
        means no grid cost (the v1 behaviour).
        """
        tariff = self.config.get("plant", {}).get("grid_tariff")
        if not tariff:
            return
        if not hasattr(self, "grid_energy_cost"):
            self.grid_energy_cost, self.monthly_peak_kw, self._hour_kwh, self._hour_key = 0.0, {}, 0.0, None
        self.grid_energy_cost += kwh * float(tariff["energy_nok_per_kwh"])
        stamp = self.clock.utc_at(t)
        hour_key = stamp.strftime("%Y-%m-%dT%H")
        if self._hour_key not in (None, hour_key):
            month = self._hour_key[:7]
            self.monthly_peak_kw[month] = max(self.monthly_peak_kw.get(month, 0.0), self._hour_kwh)
            self._hour_kwh = 0.0
        self._hour_key = hour_key
        self._hour_kwh += kwh  # kWh in one hour = mean kW over that hour

    def grid_power_cost(self) -> float:
        tariff = self.config.get("plant", {}).get("grid_tariff")
        if not tariff or not hasattr(self, "monthly_peak_kw"):
            return 0.0
        peaks = dict(self.monthly_peak_kw)
        if self._hour_key:
            m = self._hour_key[:7]
            peaks[m] = max(peaks.get(m, 0.0), self._hour_kwh)
        winter = set(tariff["winter_months"])
        return sum(kw * float(tariff["power_nok_per_kw_month"]["winter" if int(m[5:7]) in winter else "summer"])
                   for m, kw in peaks.items())

    def district_heat_price(self, t: int) -> float:
        """Declared district-heating tariff: a spot-linked energy price (ASSUMPTION)."""
        tariff = self.config["plant"].get("district_heating_tariff", {})
        return (float(tariff.get("spot_factor", 1.0)) * self.scenario.price_per_kwh(t)
                + float(tariff.get("adder_nok_per_kwh", 0.0)))

    def _step(self) -> None:
        t = self.clock.now_s
        dt = float(self.step_s)
        t_out = self.scenario.outdoor_c(t)
        gain_m2 = self.scenario.gain_w_per_m2(t)
        price = self.scenario.price_per_kwh(t)
        occupied = self.scenario.occupied(t)
        lo, hi = self.scenario.comfort_band()
        temps = {zid: z.temp_c for zid, z in self.zones.items()}
        total_w = 0.0
        dh_w = 0.0
        plant_heat: dict[str, float] | None = None
        if self.plant is not None:
            plant_heat, total_w, dh_w = self._plant_heat(t, temps, dt)
        for zid, z in self.zones.items():
            c = self._zcfg[zid]
            if plant_heat is not None:
                # Internal gains (people, equipment, light) and solar are in the plant.
                q_heat = plant_heat[zid]
                q = c["ua_w_per_k"] * (t_out - temps[zid]) + q_heat
            else:
                q_heat = min(max(c["heater_gain_w_per_k"] * (z.setpoint_c - temps[zid]), 0.0),
                             c["heater_max_w"])
                q = c["ua_w_per_k"] * (t_out - temps[zid]) + q_heat + gain_m2 * c["area_m2"]
            q += sum(g * (temps[n] - temps[zid]) for n, g in self._neighbours[zid])
            z.temp_c = temps[zid] + dt * q / c["capacity_j_per_k"]
            z.heater_w = q_heat
            if plant_heat is None:
                total_w += q_heat / c["heating_efficiency"]
            if occupied:
                dev = max(lo - temps[zid], 0.0) + max(temps[zid] - hi, 0.0)
                self.comfort_violation_dh += dev * dt / 3600.0
        kwh = total_w * dt / 3.6e6
        self.electric_kwh += kwh
        self.energy_kwh += kwh
        self.cost += kwh * price
        self._grid_tariff(t, kwh)
        if dh_w:
            dh_kwh = dh_w * dt / 3.6e6
            self.district_heat_kwh += dh_kwh
            self.energy_kwh += dh_kwh
            self.cost += dh_kwh * self.district_heat_price(t)
        self.power_w = total_w
        now = self.clock.tick()
        if now % self.obs_step_s == 0:
            for zid, z in self.zones.items():
                if not self.scenario.sensor_frozen(zid, now):
                    z.measured_temp_c = z.temp_c + self.scenario.sensor_offset_c(zid, now)
                    z.sample_at_s = now
            if self.record_trace:
                self._trace_row()

    def advance(self, seconds: int) -> int:
        if seconds <= 0 or seconds % self.step_s:
            raise TwinError(f"advance must be a positive multiple of {self.step_s}s")
        with self._lock:
            if self.clock.now_s + seconds > self.scenario.duration_seconds:
                raise TwinError("scenario_ended")
            for _ in range(seconds // self.step_s):
                self._step()
            self._persist()
            return self.clock.now_s

    @property
    def ended(self) -> bool:
        return self.clock.now_s >= self.scenario.duration_seconds

    def alarm_active(self) -> bool:
        return any(not (guard_mod.ALARM_LOW_C <= z.temp_c <= guard_mod.ALARM_HIGH_C)
                   for z in self.zones.values())

    # -- read path -------------------------------------------------------------
    def read_zone(self, zone_id: str) -> ZoneReading:
        with self._lock:
            z = self.zones.get(zone_id)
            if z is None:
                raise KeyError(zone_id)
            age = self.clock.now_s - z.sample_at_s
            quality = QUALITY_GOOD if age <= self.guard.max_observation_age_seconds else QUALITY_STALE
            return ZoneReading(zone_id=zone_id, tfm=z.tfm, measured_temp_c=r6(z.measured_temp_c),
                               setpoint_c=r6(z.setpoint_c), unit=guard_mod.UNIT,
                               sample_sim_time_s=z.sample_at_s, age_seconds=age, quality=quality,
                               state_revision=self.revision, **self.clock.stamp())

    def read_energy(self) -> dict[str, Any]:
        with self._lock:
            return {"building_id": self.config["building_id"], "energy_kwh": r6(self.energy_kwh),
                    "power_w": r6(self.power_w), "unit_energy": "kWh", "unit_power": "W",
                    "estimate": True, **self.clock.stamp()}

    def read_plant(self) -> dict[str, Any]:
        with self._lock:
            if self.plant is None:
                return {"building_id": self.config["building_id"], "model": self.model_id,
                        "plant_configured": False, "state_revision": self.revision, **self.clock.stamp()}
            sp = self.setpoints
            return {
                "building_id": self.config["building_id"],
                "model": self.model_id,
                "plant_configured": True,
                "state_revision": self.revision,
                "setpoints": {
                    "zones": {z: {"heating_c": r6(self.zones[z].setpoint_c), "cooling_c": r6(zs.cooling_c),
                                  "co2_ppm": r6(zs.co2_ppm), "lux": r6(zs.lux),
                                  "lighting_mode": zs.lighting_mode,
                                  "daylight_harvesting": zs.daylight_harvesting}
                              for z, zs in sorted(sp.zones.items())},
                    "supply_air_c": r6(sp.supply_air_c),
                    "tank_c": r6(sp.tank_c),
                    "hp_max_fraction": r6(sp.hp_max_fraction),
                    "ahu_enabled": sp.ahu_enabled,
                    "cable_modes": dict(sorted(sp.cable_modes.items())),
                },
                "tank_temp_c": r6(self.plant_state.tank_c),
                "co2_ppm": {z: r6(v) for z, v in sorted(self.plant_state.co2_ppm.items())},
                "points": {k: r6(v) for k, v in sorted(self.points.items())},
                **self.clock.stamp(),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"building_id": self.config["building_id"], "model": self.model_id,
                    "config_digest": self.config_digest, "scenario_id": self.scenario.scenario_id,
                    "scenario_digest": self.scenario.digest, "seed": self.scenario.seed,
                    "state_revision": self.revision, "stop_switch": self.stop_switch,
                    "alarm_active": self.alarm_active(), "ended": self.ended,
                    "duration_seconds": self.scenario.duration_seconds, **self.clock.stamp()}

    def operation(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            return self.operations.get(operation_id)

    # -- write path (reachable only through the protected actuator listener) ---
    @staticmethod
    def request_digest(cmd: dict[str, Any]) -> str:
        keys = ("zone_id", "setpoint_c", "units", "expected_state_revision")
        body = {k: cmd.get(k) for k in keys}
        sp = body["setpoint_c"]
        if isinstance(sp, float) and sp != sp:  # NaN cannot be canonical JSON
            body["setpoint_c"] = "NaN"
        elif isinstance(sp, float) and sp in (float("inf"), float("-inf")):
            body["setpoint_c"] = str(sp)
        return canonical_digest(body)

    def apply_setpoint(self, cmd: dict[str, Any]) -> tuple[OperationRecord, bool]:
        """Apply or refuse one setpoint write. Returns (record, replayed).

        A known operation ID with the same request returns the stored outcome
        and performs nothing. A known ID with a different request raises
        OperationConflict and performs nothing.
        """
        op_id = cmd.get("operation_id")
        if not isinstance(op_id, str) or not op_id:
            raise TwinError("operation_id required")
        digest = self.request_digest(cmd)
        with self._lock:
            prior = self.operations.get(op_id)
            if prior is not None:
                if prior.request_digest != digest:
                    raise OperationConflict(op_id)
                return prior, True
            self._refuse_if_store_failed()
            reason = guard_mod.check(self, cmd)
            zone_id = cmd.get("zone_id")[:64] if isinstance(cmd.get("zone_id"), str) else ""
            zone = self.zones.get(zone_id)
            stamp = self.clock.stamp()
            sp = cmd.get("setpoint_c")
            req_sp = float(sp) if isinstance(sp, (int, float)) and not isinstance(sp, bool) \
                and sp == sp and abs(sp) != float("inf") else None
            before = self.revision
            undo = (zone, zone.setpoint_c, list(zone.change_history_s), self.revision) if zone else None
            if reason is None and zone is not None:
                prev = zone.setpoint_c
                zone.setpoint_c = float(sp)
                zone.change_history_s.append(self.clock.now_s)
                self.revision += 1
                rec = OperationRecord(op_id, digest, "APPLIED", None, zone_id, req_sp, prev,
                                      zone.setpoint_c, before, self.revision,
                                      stamp["sim_time_s"], stamp["sim_utc"])
            else:
                rec = OperationRecord(op_id, digest, "REJECTED", reason, zone_id, req_sp,
                                      zone.setpoint_c if zone else None, None, before, before,
                                      stamp["sim_time_s"], stamp["sim_utc"])
            self.operations[op_id] = rec

            def rollback() -> None:
                if undo is not None:
                    z, sp0, hist0, rev0 = undo
                    z.setpoint_c, z.change_history_s, self.revision = sp0, hist0, rev0
            self._persist_or_rollback(op_id, rollback)
            return rec, False

    @staticmethod
    def plant_request_digest(cmd: dict[str, Any]) -> str:
        def _clean(v: Any) -> Any:
            if isinstance(v, float) and v != v:
                return "NaN"
            if isinstance(v, float) and v in (float("inf"), float("-inf")):
                return str(v)
            if isinstance(v, dict):
                return {k: _clean(val) for k, val in v.items()}
            return v
        body = {k: _clean(cmd.get(k)) for k in ("tool_name", "arguments", "expected_state_revision")}
        return canonical_digest(body)

    def _apply_plant_mutation(self, tool: str, args: dict[str, Any]) -> tuple[str, str, Any, Any, str]:
        """Apply a validated plant write and return (zone_id, target, prev, new, hist_key)."""
        sp = self.setpoints
        if tool == "set_zone_cooling_setpoint":
            zid, val = str(args["zone_id"]), float(args["cooling_c"])
            zs = sp.zones[zid]
            prev = zs.cooling_c
            self.setpoints = replace(sp, zones={**sp.zones, zid: replace(zs, cooling_c=val)})
            return zid, zid, prev, val, f"{zid}.CoolingSetpoint"
        if tool == "set_zone_co2_setpoint":
            zid, val = str(args["zone_id"]), int(args["co2_ppm"])
            zs = sp.zones[zid]
            prev = int(zs.co2_ppm)
            self.setpoints = replace(sp, zones={**sp.zones, zid: replace(zs, co2_ppm=float(val))})
            return zid, zid, prev, val, f"{zid}.CO2Setpoint"
        if tool == "set_zone_lighting_setpoint":
            zid, val = str(args["zone_id"]), int(args["lux"])
            zs = sp.zones[zid]
            prev = int(zs.lux)
            self.setpoints = replace(sp, zones={**sp.zones, zid: replace(zs, lux=float(val))})
            return zid, zid, prev, val, f"{zid}.LuxSetpoint"
        if tool == "set_zone_lighting_mode":
            zid, mode, dh = str(args["zone_id"]), str(args["mode"]), bool(args["daylight_harvesting"])
            zs = sp.zones[zid]
            prev = {"mode": zs.lighting_mode, "daylight_harvesting": zs.daylight_harvesting}
            new = {"mode": mode, "daylight_harvesting": dh}
            self.setpoints = replace(sp, zones={**sp.zones, zid: replace(zs, lighting_mode=mode,
                                                                         daylight_harvesting=dh)})
            return zid, zid, prev, new, f"{zid}.LightingMode"
        if tool == "set_supply_air_setpoint":
            val = float(args["supply_air_c"])
            prev = sp.supply_air_c
            self.setpoints = replace(sp, supply_air_c=val)
            return "AHU-01", "AHU-01", prev, val, "AHU-01.SupplyAirSetpoint"
        if tool == "set_tank_setpoint":
            val = float(args["tank_c"])
            prev = sp.tank_c
            self.setpoints = replace(sp, tank_c=val)
            return "TANK-01", "TANK-01", prev, val, "TANK-01.Setpoint"
        if tool == "set_heat_pump_limit":
            val = float(args["max_fraction"])
            prev = sp.hp_max_fraction
            self.setpoints = replace(sp, hp_max_fraction=val)
            return "HP-01", "HP-01", prev, val, "HP-01.MaxFraction"
        if tool == "set_ahu_enabled":
            val = bool(args["enabled"])
            prev = sp.ahu_enabled
            self.setpoints = replace(sp, ahu_enabled=val)
            return "AHU-01", "AHU-01", prev, val, "AHU-01.Enabled"
        if tool == "set_heating_cable_mode":
            circuit, mode = str(args["circuit"]), str(args["mode"])
            prev = sp.cable_modes.get(circuit, "auto")
            self.setpoints = replace(sp, cable_modes={**sp.cable_modes, circuit: mode})
            return circuit, circuit, prev, mode, f"{circuit}.Mode"
        raise TwinError(f"unsupported plant tool {tool!r}")

    def apply_plant_command(self, cmd: dict[str, Any]) -> tuple[OperationRecord, bool]:
        """Apply or refuse one plant supervisory command. Returns (record, replayed)."""
        op_id = cmd.get("operation_id")
        if not isinstance(op_id, str) or not op_id:
            raise TwinError("operation_id required")
        digest = self.plant_request_digest(cmd)
        with self._lock:
            prior = self.operations.get(op_id)
            if prior is not None:
                if prior.request_digest != digest:
                    raise OperationConflict(op_id)
                return prior, True
            self._refuse_if_store_failed()
            reason = guard_mod.check_plant(self, cmd)
            tool = str(cmd.get("tool_name") or "")
            args = cmd.get("arguments") if isinstance(cmd.get("arguments"), dict) else {}
            stamp = self.clock.stamp()
            before = self.revision
            undo_plant = (self.setpoints, {k: list(v) for k, v in self.point_history_s.items()}, self.revision)
            if reason is None:
                zid, target, prev, new, hist_key = self._apply_plant_mutation(tool, args)
                self.point_history_s.setdefault(hist_key, []).append(self.clock.now_s)
                self.revision += 1
                num_prev = float(prev) if isinstance(prev, (int, float)) and not isinstance(prev, bool) else None
                num_new = float(new) if isinstance(new, (int, float)) and not isinstance(new, bool) else 0.0
                rec = OperationRecord(
                    op_id, digest, "APPLIED", None, zid, num_new, num_prev, num_new,
                    before, self.revision, stamp["sim_time_s"], stamp["sim_utc"],
                    tool_name=tool, target=target, requested_value=new,
                    previous_value=prev, new_value=new)
            else:
                # SR-15 (F-17): a refusal keeps the request digest, not the caller's arguments, and only
                # bounded names, so a stream of refused operations cannot grow the log by what it sends.
                zid = str(args.get("zone_id") or args.get("circuit") or tool)[:64]
                rec = OperationRecord(
                    op_id, digest, "REJECTED", reason, zid, None, None, None,
                    before, before, stamp["sim_time_s"], stamp["sim_utc"],
                    tool_name=tool[:64], target=zid, requested_value=None,
                    previous_value=None, new_value=None)
            self.operations[op_id] = rec

            def rollback() -> None:
                self.setpoints, self.point_history_s, self.revision = undo_plant
            self._persist_or_rollback(op_id, rollback)
            return rec, False

    def set_stop_switch(self, engaged: bool, actor: str) -> dict[str, Any]:
        with self._lock:
            self.stop_switch = bool(engaged)
            ev = {"event": "stop_switch", "engaged": self.stop_switch, "actor": actor, **self.clock.stamp()}
            self.events.append(ev)
            self._persist()
            return ev

    # -- trace & persistence -----------------------------------------------------
    def _trace_row(self) -> None:
        t = self.clock.now_s
        self.trace.append({
            "sim_time_s": t, "sim_utc": format_utc(self.clock.utc_at(t)),
            "outdoor_c": r6(self.scenario.outdoor_c(t)), "occupied": self.scenario.occupied(t),
            "price_per_kwh": r6(self.scenario.price_per_kwh(t)),
            "zones": {zid: {"temp_c": r6(z.temp_c), "setpoint_c": r6(z.setpoint_c),
                            "heater_w": r6(z.heater_w)} for zid, z in sorted(self.zones.items())},
            "energy_kwh": r6(self.energy_kwh), "cost": r6(self.cost),
            "comfort_violation_dh": r6(self.comfort_violation_dh), "state_revision": self.revision,
        })

    #: Optional per-step attributes that hold physics, tariff or season state
    #: between steps. Absent until first used, so they are persisted by name
    #: only when present and restored only when the snapshot carries them.
    _OPTIONAL_STATE = ("_wet_since", "_melt_s", "_dry_s", "_season_on", "_season_hour", "outdoor_mean_24h",
                       "grid_energy_cost", "monthly_peak_kw", "_hour_kwh", "_hour_key")

    def snapshot(self, include_operations: bool = True) -> dict[str, Any]:
        """Everything a restart needs to continue as if it never happened (schema v2).

        v2 adds, to the v1 zones/operations/counters: the HVAC setpoints and
        plant state, the point history behind the min-interval interlock, the
        last plant points, the heat and electricity meters, and the optional
        tariff, season and surface state.
        """
        with self._lock:
            snap: dict[str, Any] = {
                "schema": SNAPSHOT_SCHEMA, "config_digest": self.config_digest,
                "scenario_digest": self.scenario.digest, "model_id": self.model_id, "now_s": self.clock.now_s,
                "revision": self.revision, "energy_kwh": self.energy_kwh, "cost": self.cost,
                "comfort_violation_dh": self.comfort_violation_dh, "power_w": self.power_w,
                "stop_switch": self.stop_switch,
                "zones": {k: v.to_dict() for k, v in self.zones.items()},
                **({"operations": {k: v.to_dict() for k, v in self.operations.items()}}
                   if include_operations else {}),
                "events": list(self.events),
                "point_history_s": {k: list(v) for k, v in self.point_history_s.items()},
                "district_heat_kwh": self.district_heat_kwh, "electric_kwh": self.electric_kwh,
                "points": dict(self.points),
                "optional_state": {k: getattr(self, k) for k in self._OPTIONAL_STATE if hasattr(self, k)},
            }
            if self.plant is not None:
                sp = self.setpoints
                snap["hvac"] = {
                    "setpoints": {"zones": {z: asdict(v) for z, v in sp.zones.items()},
                                  "supply_air_c": sp.supply_air_c, "tank_c": sp.tank_c,
                                  "hp_max_fraction": sp.hp_max_fraction, "ahu_enabled": sp.ahu_enabled,
                                  "cable_modes": dict(sp.cable_modes)},
                    "plant_state": asdict(self.plant_state),
                }
            return snap

    def _restore(self, snap: dict[str, Any]) -> None:
        """Resume from a snapshot: authenticated when a key is set, always re-validated, never silently (SR-14)."""
        integrity = snap.get("integrity")
        authenticated = False
        if self._snapshot_key:
            mac = integrity.get("mac") if isinstance(integrity, dict) else None
            if not isinstance(mac, str) or not hmac.compare_digest(mac, self._mac(snap)):
                raise TwinError("snapshot integrity check failed (HMAC missing or wrong); refusing to resume")
            authenticated = True
        body = {k: v for k, v in snap.items() if k != "integrity"}
        if "operations" not in body and "journal" in body:
            body["operations"] = self._load_journal(body["journal"])
        else:  # the log inline (written before SR-15): the journal is rewritten from it on the next persist
            self._chain, self._journal_on_disk = [], 0
            if self._journal_path is not None and self._journal_path.exists():
                self._journal_path.unlink()
        self._restore_unchecked(body)
        if len(self._chain) != len(self.operations):
            self._chain, self._journal_on_disk = [], 0
        if getattr(self, "_journal_recovered", None):
            self.events.append({"event": "journal_recovered", "operation_id": self._journal_recovered,
                                **self.clock.stamp()})
        problems = self._restored_state_problems()
        if problems:
            raise TwinError("snapshot does not hold a valid plant state; refusing to resume: " + "; ".join(problems))
        self.events.append({"event": "snapshot_restored", "authenticated": authenticated,
                            "snapshot_sha256": hashlib.sha256(json.dumps(
                                {k: v for k, v in snap.items() if k != "integrity"}, sort_keys=True)
                                .encode("utf-8")).hexdigest(), **self.clock.stamp()})

    def _link(self, prev: str, record: dict[str, Any]) -> str:
        """One step of the journal's chain: keyed with the snapshot key when set, else an unkeyed hash chain."""
        return hmac.new(self._snapshot_key, prev.encode("ascii") + json.dumps(record, sort_keys=True).encode("utf-8"),
                        hashlib.sha256).hexdigest()

    def _load_journal(self, meta: Any) -> dict[str, Any]:
        """Rebuild the operation log from the journal and check it against the snapshot that names it.

        The snapshot is written before the journal line, and carries the newest record itself (``tail``), so
        a crash between the two leaves the journal exactly one line short; that line is recovered from the
        snapshot. Any other mismatch (a line changed, removed or added, a chain that does not reach the head
        the snapshot names) refuses the resume. A torn last line, from a crash mid-append, counts as absent.
        """
        if not isinstance(meta, dict) or not isinstance(meta.get("count"), int) or meta["count"] < 0:
            raise TwinError("snapshot journal reference is malformed; refusing to resume")
        count, head, tail = meta["count"], meta.get("head", ""), meta.get("tail")
        lines: list[str] = []
        if self._journal_path is not None and self._journal_path.exists():
            raw = self._journal_path.read_bytes()
            lines = raw.decode("utf-8").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            elif lines:  # no newline after the last line: torn by a crash, never acknowledged
                lines.pop()
                with open(self._journal_path, "r+b") as f:
                    f.truncate(raw.rfind(b"\n") + 1)
        records: list[dict[str, Any]] = []
        chain: list[str] = []
        prev = ""
        for i, line in enumerate(lines):
            try:
                entry = json.loads(line)
                rec, mac = entry["record"], entry["mac"]
            except (ValueError, KeyError, TypeError):
                raise TwinError(f"journal line {i + 1} is malformed; refusing to resume") from None
            prev = self._link(prev, rec)
            if not isinstance(mac, str) or not hmac.compare_digest(mac, prev):
                raise TwinError(f"journal line {i + 1} fails its chain check; refusing to resume")
            records.append(rec)
            chain.append(prev)
        if len(records) == count - 1 and isinstance(tail, dict):
            prev = self._link(prev, tail)
            records.append(tail)
            chain.append(prev)
            recovered = True
        else:
            recovered = False
        if len(records) != count or (count and not hmac.compare_digest(str(head), prev)):
            raise TwinError(f"journal holds {len(lines)} operations but the snapshot names {count} with another "
                            "head; refusing to resume")
        self._chain, self._journal_on_disk = chain, len(lines)
        if recovered:
            self._append_journal(records, chain)
            self._journal_recovered = tail.get("operation_id")
        return {r["operation_id"]: r for r in records}

    def _append_journal(self, records: list[dict[str, Any]], chain: list[str]) -> None:
        """Make every record not yet on disk durable, in order, with one fsync."""
        new = range(self._journal_on_disk, len(records))
        if not new or self._journal_path is None:
            return
        data = "".join(json.dumps({"record": records[i], "mac": chain[i]}, sort_keys=True) + "\n" for i in new)
        with open(self._journal_path, "ab") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        self._journal_on_disk = len(records)

    def _restored_state_problems(self) -> list[str]:
        """What a resumed state must satisfy before any write is accepted from it."""
        out: list[str] = []
        for zid, z in self.zones.items():
            lo, hi = self.zone_limits.get(zid, (None, None))
            if lo is None or not isinstance(z.setpoint_c, (int, float)) or not (lo <= z.setpoint_c <= hi):
                out.append(f"{zid} setpoint {z.setpoint_c} outside {lo}..{hi}")
            if any(t > self.clock.now_s for t in z.change_history_s):
                out.append(f"{zid} change history lies in the future")
        applied = sum(1 for r in self.operations.values() if r.status == "APPLIED")
        if applied != self.revision:
            out.append(f"revision {self.revision} does not equal the {applied} applied operations on record")
        if any(t > self.clock.now_s for v in self.point_history_s.values() for t in v):
            out.append("point history lies in the future")
        if self.plant is not None:
            sp = self.setpoints
            for zid, zs in sp.zones.items():
                if not (20.0 <= zs.cooling_c <= 28.0):
                    out.append(f"{zid} cooling {zs.cooling_c} outside 20..28")
                if zid in self.zones and zs.cooling_c < self.zones[zid].setpoint_c + 2.0 - 1e-9:
                    out.append(f"{zid} breaks the IL-01 deadband")
                if not (600 <= zs.co2_ppm <= 1000) or not (0 <= zs.lux <= 750):
                    out.append(f"{zid} CO2 or lux setpoint out of bounds")
            if not (15.0 <= sp.supply_air_c <= 21.0) or not (35.0 <= sp.tank_c <= 60.0) \
                    or not (0.0 <= sp.hp_max_fraction <= 1.0):
                out.append("supply air, tank or heat pump setpoint out of bounds")
            if any(m not in guard_mod.CABLE_MODES for m in sp.cable_modes.values()):
                out.append("unknown cable mode")
        return out

    def _restore_unchecked(self, snap: dict[str, Any]) -> None:
        schema = snap.get("schema")
        if schema not in (SNAPSHOT_SCHEMA, LEGACY_SNAPSHOT_SCHEMA) \
                or snap.get("config_digest") != self.config_digest \
                or snap.get("scenario_digest") != self.scenario.digest:
            raise TwinError("snapshot does not match this config/scenario; refusing to resume")
        if schema == LEGACY_SNAPSHOT_SCHEMA and self.plant is not None:
            # A v1 snapshot has no HVAC setpoints, plant state or point history.
            # Resuming the hvac model from it would keep the revision and the
            # operation log while silently resetting what those operations did
            # and the interval guard behind them. Refuse; a reset plant is a new
            # run, not a continuation.
            raise TwinError(f"snapshot {LEGACY_SNAPSHOT_SCHEMA} lacks the HVAC setpoints, plant state and "
                            "point history; refusing to resume the hvac model from it. Start a new run, "
                            f"or resume from a {SNAPSHOT_SCHEMA} snapshot")
        self.clock.now_s = int(snap["now_s"])
        self.revision = int(snap["revision"])
        self.energy_kwh = float(snap["energy_kwh"])
        self.cost = float(snap["cost"])
        self.comfort_violation_dh = float(snap["comfort_violation_dh"])
        self.power_w = float(snap["power_w"])
        self.stop_switch = bool(snap["stop_switch"])
        self.zones = {k: ZoneState(**v) for k, v in snap["zones"].items()}
        if not isinstance(snap.get("operations"), dict):
            raise TwinError("snapshot carries neither an operation log nor a journal; refusing to resume")
        self.operations = {k: OperationRecord(**v) for k, v in snap["operations"].items()}
        self.events = list(snap["events"])
        if schema == LEGACY_SNAPSHOT_SCHEMA:
            # Explicit migration for the simple model, which has no plant tools:
            # the only state v1 lacks is the (empty) plant point history. Say so
            # in the event log rather than resuming silently.
            self.point_history_s = {}
            self.snapshot_migrated_from = LEGACY_SNAPSHOT_SCHEMA
            self.events.append({"event": "snapshot_migrated", "from": LEGACY_SNAPSHOT_SCHEMA,
                                "to": SNAPSHOT_SCHEMA, **self.clock.stamp()})
            return
        self.point_history_s = {k: [int(x) for x in v] for k, v in snap.get("point_history_s", {}).items()}
        self.district_heat_kwh = float(snap.get("district_heat_kwh", 0.0))
        self.electric_kwh = float(snap.get("electric_kwh", 0.0))
        self.points = {k: float(v) for k, v in snap.get("points", {}).items()}
        for k, v in snap.get("optional_state", {}).items():
            if k in self._OPTIONAL_STATE:
                setattr(self, k, v)
        if self.plant is not None:
            hv = snap.get("hvac")
            if not hv:
                raise TwinError(f"{SNAPSHOT_SCHEMA} snapshot of the hvac model carries no hvac section; "
                                "refusing to resume")
            sp = hv["setpoints"]
            self.setpoints = plant_mod.Setpoints(
                zones={z: plant_mod.ZoneSetpoints(**v) for z, v in sp["zones"].items()},
                supply_air_c=float(sp["supply_air_c"]), tank_c=float(sp["tank_c"]),
                hp_max_fraction=float(sp["hp_max_fraction"]), ahu_enabled=bool(sp["ahu_enabled"]),
                cable_modes=dict(sp["cable_modes"]))
            ps = hv["plant_state"]
            self.plant_state = plant_mod.PlantState(
                tank_c=float(ps["tank_c"]), co2_ppm={z: float(v) for z, v in ps["co2_ppm"].items()},
                hp_on=bool(ps.get("hp_on", False)), hp_state_since_s=float(ps.get("hp_state_since_s", 1e9)),
                hp_starts=int(ps.get("hp_starts", 0)))

    def _refuse_if_store_failed(self) -> None:
        if self._store_failed:
            raise StateStoreFailed(f"state_store_failed: {self._store_failed}; no write until the twin is restarted")
        if len(self.operations) >= self.MAX_OPERATIONS:
            raise OperationLogFull(f"operation_log_full: {self.MAX_OPERATIONS} operations recorded; "
                                   "this one was not applied")

    def _persist_or_rollback(self, op_id: str, rollback: Any) -> None:
        """SR-14: an effect exists only together with its durable record. If the record cannot be persisted, the
        in-memory effect and the record are undone, the twin refuses every further write until restarted, and the
        caller gets StateStoreFailed: the operation id stays unused, so a restart cannot replay a lost effect."""
        try:
            self._persist()
        except _JournalFailed as e:  # the snapshot holds the operation as its tail: recorded, recovered on restart
            self._store_failed = f"journal append failed: {e}"
        except Exception as e:
            self.operations.pop(op_id, None)
            del self._chain[len(self.operations):]
            rollback()
            self._store_failed = f"{type(e).__name__}: {e}"
            raise StateStoreFailed(f"state_store_failed: the operation was not recorded and has no effect "
                                   f"({type(e).__name__})") from None

    def _mac(self, snap: dict[str, Any]) -> str:
        body = {k: v for k, v in snap.items() if k != "integrity"}
        return hmac.new(self._snapshot_key, json.dumps(body, sort_keys=True).encode("utf-8"), hashlib.sha256).hexdigest()

    def _persist(self) -> None:
        if not self._snapshot_path:
            return
        records = [r.to_dict() for r in list(self.operations.values())[len(self._chain):]]
        prev = self._chain[-1] if self._chain else ""
        for rec in records:
            prev = self._link(prev, rec)
            self._chain.append(prev)
        snap = self.snapshot(include_operations=False)
        last = next(reversed(self.operations.values()), None)
        snap["journal"] = {"count": len(self.operations), "head": self._chain[-1] if self._chain else "",
                           "tail": last.to_dict() if last is not None else None}
        if self._snapshot_key:
            snap["integrity"] = {"alg": "HMAC-SHA256", "mac": self._mac(snap)}
        data = json.dumps(snap, sort_keys=True).encode("utf-8")
        d = self._snapshot_path.parent
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".snap-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._snapshot_path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        if self._journal_on_disk < len(self.operations):
            all_records = [r.to_dict() for r in self.operations.values()] \
                if self._journal_on_disk < len(self.operations) - 1 else None
            try:
                if all_records is None:  # the usual case: exactly one new line
                    self._append_journal_one(last.to_dict())
                else:
                    self._append_journal(all_records, self._chain)
            except OSError as e:
                raise _JournalFailed(f"{type(e).__name__}: {e}") from None

    def _append_journal_one(self, record: dict[str, Any]) -> None:
        line = json.dumps({"record": record, "mac": self._chain[len(self.operations) - 1]}, sort_keys=True) + "\n"
        assert self._journal_path is not None
        with open(self._journal_path, "ab") as f:
            f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        self._journal_on_disk = len(self.operations)
