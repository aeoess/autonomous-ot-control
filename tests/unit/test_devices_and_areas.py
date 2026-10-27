"""New devices and the five price areas behave as specified.

The specification is docs/datapoints.md and ADR-007 (seasons and devices).
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from runner import exogenous as ex
from runner.areas import AREAS, CONFIG, scenario_path
from runner.demo import STEP_S, ZONES
from runner.strategies import Controller
from twin import plant as P
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
CFG = json.loads(CONFIG.read_text("utf-8"))


def _plant():
    return P.PlantConfig.from_dict(CFG["plant"])


def _sp(**over):
    d = CFG["default_setpoints"]
    sp = P.Setpoints(zones={z: P.ZoneSetpoints(**v) for z, v in d["zones"].items()},
                     supply_air_c=d["supply_air_c"], tank_c=d["tank_c"])
    return replace(sp, **over)


def _exo(**over):
    base = dict(outdoor_c=-2.0, solar_w_m2=0.0, outdoor_rh_pct=80, occupants={z: 0 for z in ZONES})
    return P.Exogenous(**{**base, **over})


STATE = P.PlantState(tank_c=45.0, co2_ppm={z: 420.0 for z in ZONES})
TEMPS = {z: 21.0 for z in ZONES}


@pytest.mark.parametrize("area", list(AREAS))
def test_every_area_site_lies_in_its_declared_box(area):
    site = json.loads(scenario_path(area).read_text("utf-8"))["site_ref"]
    s = ex.load_site(scenario_path(area).parent / site)
    assert s["price_area"]["code"] == area and s["fictitious"] is True


@pytest.mark.parametrize("area", list(AREAS))
def test_every_area_scenario_loads_its_own_verified_prices(area):
    sc = load_scenario(scenario_path(area))
    assert sc.price_series.provenance["source"]["area"] == area
    assert len(sc.price_series.values) == 8760 and len(sc.outdoor_series.values) == 8760


def test_sensor_cables_never_leave_a_freezing_wet_surface_uncovered():
    sp = _sp(cable_modes={"CABLE-RAMP": "sensor", "CABLE-GUTTER": "sensor"})
    wet = P.step(_plant(), STATE, TEMPS, sp, _exo(surface_wet=True), 60.0).points
    dry = P.step(_plant(), STATE, TEMPS, sp, _exo(surface_wet=False), 60.0).points
    assert wet["CABLE-RAMP.Power"] > 0 and wet["CABLE-RAMP.IcingHazardUncovered"] == 0.0
    assert dry["CABLE-RAMP.Power"] == 0.0


def test_thermostat_cables_run_whenever_it_is_cold_and_miss_deep_cold():
    sp = _sp(cable_modes={"CABLE-RAMP": "thermostat", "CABLE-GUTTER": "thermostat"})
    dry_cold = P.step(_plant(), STATE, TEMPS, sp, _exo(surface_wet=False, outdoor_c=0.0), 60.0).points
    wet_deep = P.step(_plant(), STATE, TEMPS, sp, _exo(surface_wet=True, outdoor_c=-9.0), 60.0).points
    assert dry_cold["CABLE-RAMP.Power"] > 0                      # heats a dry ramp
    assert wet_deep["CABLE-RAMP.IcingHazardUncovered"] == 1.0    # and misses ice below -8


def test_lighting_modes():
    occ = {"ZONE-01": 5, "ZONE-02": 0, "ZONE-03": 0}
    zones = lambda **kw: {z: replace(v, **kw) for z, v in _sp().zones.items()}  # noqa: E731
    schedule = P.step(_plant(), STATE, TEMPS, _sp(zones=zones(lighting_mode="schedule", daylight_harvesting=False)),
                      _exo(occupants=occ, scheduled_open=True, solar_w_m2=200), 60.0).points
    presence = P.step(_plant(), STATE, TEMPS, _sp(zones=zones(lighting_mode="presence", daylight_harvesting=True)),
                      _exo(occupants=occ, scheduled_open=True, solar_w_m2=200), 60.0).points
    assert schedule["ZONE-02.LightingPower"] > 0 == presence["ZONE-02.LightingPower"]  # empty room
    assert presence["ZONE-01.LightingPower"] < schedule["ZONE-01.LightingPower"]       # daylight


def test_surface_dries_only_after_melting_in_the_cold():
    raw = json.loads(scenario_path("NO1").read_text("utf-8"))
    raw["duration_seconds"] = 20 * 86400
    sc = load_scenario(raw, base_dir=scenario_path("NO1").parent)
    tw = Twin(load_config(CONFIG), sc)
    tw.setpoints = replace(tw.setpoints, cable_modes={"CABLE-RAMP": "off", "CABLE-GUTTER": "off"})
    wet_seen = False
    for _ in range(20 * 24 * 60):
        tw._step()
        if tw.points.get("OUT-01.SurfaceWet") and sc.outdoor_c(tw.clock.now_s) <= 2.0:
            wet_seen = True
            break
    assert wet_seen, "January in Oslo should wet the ramp at least once"
    # With the cable off and no thaw the surface stays wet for the next hour.
    t0 = tw.clock.now_s
    stays = all(tw._surface_wet(t0 + k * 60, 60.0) for k in range(60)
                if sc.outdoor_c(t0 + k * 60) <= 2.0)
    assert stays


def test_grid_power_charge_uses_the_monthly_hourly_peak():
    raw = json.loads(scenario_path("NO3").read_text("utf-8"))
    raw["duration_seconds"] = 2 * 86400
    tw = Twin(load_config(CONFIG), load_scenario(raw, base_dir=scenario_path("NO3").parent))
    tw.advance(2 * 86400 - 60)
    peak = max(tw.monthly_peak_kw.values()) if tw.monthly_peak_kw else tw._hour_kwh
    assert peak > 0
    assert tw.grid_power_cost() == pytest.approx(max(peak, tw._hour_kwh) * 110.0, rel=1e-6)
    assert tw.grid_energy_cost == pytest.approx(tw.electric_kwh * 0.06, rel=1e-6)


@pytest.mark.parametrize("name", ["P0", "P1", "P2"])
def test_strategies_respect_the_interlock_rules(name):
    raw = json.loads(scenario_path("NO4").read_text("utf-8"))
    raw["duration_seconds"] = 7 * 86400
    sc = load_scenario(raw, base_dir=scenario_path("NO4").parent)
    tw = Twin(load_config(CONFIG), sc)
    ctl = Controller(name, tw)
    for t in range(0, 7 * 86400, STEP_S):
        ctl.step(t)
        sp = tw.setpoints
        for zid, zs in sp.zones.items():
            assert zs.cooling_c >= tw.zones[zid].setpoint_c + 2.0          # IL-01
        if sc.occupied(t):
            assert sp.ahu_enabled                                          # IL-06
        if sp.hp_max_fraction < 1.0:
            assert tw.plant_state.tank_c >= sp.tank_c - 2.0                # IL-09
        tw.advance(STEP_S)
        if name == "P2":
            assert tw.points.get("CABLE-RAMP.IcingHazardUncovered", 0.0) == 0.0  # IL-10
