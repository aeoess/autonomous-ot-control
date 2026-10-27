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
import os
import tempfile
import threading
from dataclasses import replace
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
SNAPSHOT_SCHEMA = "ot-twin-snapshot/v1"


class TwinError(RuntimeError):
    pass


class OperationConflict(TwinError):
    """An operation ID was reused with a different request."""


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
    def __init__(self, config: dict[str, Any], scenario: Scenario, *,
                 snapshot_path: str | Path | None = None, record_trace: bool = False) -> None:
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
        self.operations: dict[str, OperationRecord] = {}
        self.events: list[dict[str, Any]] = []
        self.record_trace = record_trace
        self.trace: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
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
            equipment_factor={zid: sc.equipment_factor(zid, t) for zid in self.zones})
        res = plant_mod.step(self.plant, self.plant_state, temps, self.setpoints, exo, dt)
        self.plant_state = res.state
        self.points = res.points
        return res.zone_heat_w, res.electric_w, res.district_heat_w

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
                    z.measured_temp_c = z.temp_c
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
            reason = guard_mod.check(self, cmd)
            zone_id = cmd.get("zone_id") if isinstance(cmd.get("zone_id"), str) else ""
            zone = self.zones.get(zone_id)
            stamp = self.clock.stamp()
            sp = cmd.get("setpoint_c")
            req_sp = float(sp) if isinstance(sp, (int, float)) and not isinstance(sp, bool) \
                and sp == sp and abs(sp) != float("inf") else None
            before = self.revision
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
            self._persist()
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

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"schema": SNAPSHOT_SCHEMA, "config_digest": self.config_digest,
                    "scenario_digest": self.scenario.digest, "now_s": self.clock.now_s,
                    "revision": self.revision, "energy_kwh": self.energy_kwh, "cost": self.cost,
                    "comfort_violation_dh": self.comfort_violation_dh, "power_w": self.power_w,
                    "stop_switch": self.stop_switch,
                    "zones": {k: v.to_dict() for k, v in self.zones.items()},
                    "operations": {k: v.to_dict() for k, v in self.operations.items()},
                    "events": list(self.events)}

    def _restore(self, snap: dict[str, Any]) -> None:
        if snap.get("schema") != SNAPSHOT_SCHEMA or snap.get("config_digest") != self.config_digest \
                or snap.get("scenario_digest") != self.scenario.digest:
            raise TwinError("snapshot does not match this config/scenario; refusing to resume")
        self.clock.now_s = int(snap["now_s"])
        self.revision = int(snap["revision"])
        self.energy_kwh = float(snap["energy_kwh"])
        self.cost = float(snap["cost"])
        self.comfort_violation_dh = float(snap["comfort_violation_dh"])
        self.power_w = float(snap["power_w"])
        self.stop_switch = bool(snap["stop_switch"])
        self.zones = {k: ZoneState(**v) for k, v in snap["zones"].items()}
        self.operations = {k: OperationRecord(**v) for k, v in snap["operations"].items()}
        self.events = list(snap["events"])

    def _persist(self) -> None:
        if not self._snapshot_path:
            return
        data = json.dumps(self.snapshot(), sort_keys=True).encode("utf-8")
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
