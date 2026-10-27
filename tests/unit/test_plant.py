"""Building services physics (twin/plant.py): each component behaves as its
physics says, and the plant conserves energy.

These pin the model's shape, not calibrated values: who reads a saving must
be able to trust that a heat pump gets worse in the cold, a fan costs SFP x
airflow, CO2 settles where the mass balance says, and no heat appears from
nowhere.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from twin import plant as P

ROOT = Path(__file__).resolve().parents[2]
CFG = json.loads((ROOT / "twin" / "fixtures" / "three-zone-hvac-v1.json").read_text("utf-8"))
ZONES = ("ZONE-01", "ZONE-02", "ZONE-03")


def plant(**over):
    return P.PlantConfig.from_dict({**CFG["plant"], **over})


def setpoints(**over) -> P.Setpoints:
    d = CFG["default_setpoints"]
    zones = {z: P.ZoneSetpoints(**v) for z, v in d["zones"].items()}
    sp = P.Setpoints(zones=zones, supply_air_c=d["supply_air_c"], tank_c=d["tank_c"])
    return replace(sp, **over)


def state(tank=45.0, co2=420.0, **over) -> P.PlantState:
    return replace(P.PlantState(tank_c=tank, co2_ppm={z: co2 for z in ZONES}), **over)


def exo(t_out=-5.0, solar=0.0, rh=60.0, occ=None, icing=False) -> P.Exogenous:
    return P.Exogenous(outdoor_c=t_out, solar_w_m2=solar, outdoor_rh_pct=rh,
                       occupants=occ if occ is not None else {z: 0 for z in ZONES},
                       icing_risk=icing)


TEMPS = {z: 21.0 for z in ZONES}


class TestHeatPump:
    def test_cop_falls_as_the_lift_grows(self):
        warm = P.heat_pump_cop(7.0, 35.0, 0.45)
        cold = P.heat_pump_cop(-15.0, 35.0, 0.45)
        hot_water = P.heat_pump_cop(7.0, 55.0, 0.45)
        assert 3.0 < warm <= 6.0 and cold < warm and hot_water < warm
        assert P.heat_pump_cop(-30.0, 60.0, 0.45) >= 1.0  # clamped, never below resistive

    def test_capacity_falls_in_the_cold(self):
        assert P.heat_pump_capacity_w(24000, -15) < P.heat_pump_capacity_w(24000, 7) == 24000

    def test_compressor_respects_min_stop_and_counts_starts(self):
        cfg = plant()
        st = state(tank=30.0, hp_on=False, hp_state_since_s=0.0)   # just stopped
        r = P.step(cfg, st, TEMPS, setpoints(), exo(), 60.0)
        assert r.points["HP-01.Running"] == 0.0                    # min stop not served
        st = state(tank=30.0, hp_on=False, hp_state_since_s=600.0)
        r = P.step(cfg, st, TEMPS, setpoints(), exo(), 60.0)
        assert r.points["HP-01.Running"] == 1.0 and r.state.hp_starts == 1

    def test_compressor_respects_min_run(self):
        cfg = plant()
        st = state(tank=60.0, hp_on=True, hp_state_since_s=60.0)    # hot tank, just started
        assert P.step(cfg, st, TEMPS, setpoints(), exo(), 60.0).points["HP-01.Running"] == 1.0
        st = state(tank=60.0, hp_on=True, hp_state_since_s=600.0)
        assert P.step(cfg, st, TEMPS, setpoints(), exo(), 60.0).points["HP-01.Running"] == 0.0

    def test_high_pressure_cutout_overrides_the_setpoint(self):
        r = P.step(plant(), state(tank=66.0, hp_on=True, hp_state_since_s=60.0), TEMPS,
                   setpoints(tank_c=70.0), exo(), 60.0)
        assert r.points["HP-01.HighPressureCutout"] == 1.0 and r.points["HP-01.Running"] == 0.0

    def test_below_its_operating_limit_the_backup_heater_carries_the_load(self):
        r = P.step(plant(), state(tank=35.0, hp_state_since_s=1e9), TEMPS, setpoints(),
                   exo(t_out=-25.0), 60.0)
        assert r.points["HP-01.Running"] == 0.0 and r.points["HP-01.BackupHeaterPower"] > 0

    def test_defrost_costs_capacity_and_cop_in_humid_near_zero_air(self):
        st = state(tank=30.0, hp_state_since_s=1e9)
        dry = P.step(plant(), st, TEMPS, setpoints(), exo(t_out=0.0, rh=50.0), 60.0).points
        wet = P.step(plant(), st, TEMPS, setpoints(), exo(t_out=0.0, rh=90.0), 60.0).points
        assert wet["HP-01.Defrost"] == 1.0 and dry["HP-01.Defrost"] == 0.0
        assert wet["HP-01.COP"] < dry["HP-01.COP"] and wet["HP-01.HeatOutput"] < dry["HP-01.HeatOutput"]

    def test_demand_response_cap_scales_output(self):
        st = state(tank=30.0, hp_state_since_s=1e9)
        full = P.step(plant(), st, TEMPS, setpoints(), exo(), 60.0).points["HP-01.HeatOutput"]
        half = P.step(plant(), st, TEMPS, setpoints(hp_max_fraction=0.5), exo(), 60.0
                      ).points["HP-01.HeatOutput"]
        assert half == pytest.approx(full / 2)


class TestEnergyBalance:
    @pytest.mark.parametrize("source", ["heat_pump", "district_heating", "electric_boiler"])
    def test_tank_energy_is_conserved(self, source):
        cfg = plant(heat_source=source)
        st = state(tank=40.0, hp_state_since_s=1e9)
        r = P.step(cfg, st, {z: 19.0 for z in ZONES}, setpoints(), exo(), 60.0)
        p = r.points
        source_w = (p.get("HP-01.HeatOutput", 0) + p.get("HP-01.BackupHeaterPower", 0)
                    + p.get("DH-01.HeatPower", 0) * cfg.dh_substation_efficiency
                    + p.get("EB-01.ElectricPower", 0))
        stored = (r.state.tank_c - st.tank_c) * cfg.tank_volume_l * P.CP_WATER_J_PER_KG_K / 60.0
        assert stored == pytest.approx(source_w - p["HYD-01.HeatDemand"] - p["TANK-01.Loss"], rel=1e-9)

    def test_electric_meter_is_the_sum_of_its_consumers(self):
        r = P.step(plant(), state(tank=30.0, hp_state_since_s=1e9), TEMPS, setpoints(),
                   exo(occ={"ZONE-01": 10, "ZONE-02": 6, "ZONE-03": 4}, icing=True), 60.0)
        p = r.points
        parts = (p["AHU-01.FanPower"] + p["CH-01.ElectricPower"] + p["HP-01.ElectricPower"]
                 + p["HP-01.BackupHeaterPower"] + p["CABLE-RAMP.Power"] + p["CABLE-GUTTER.Power"]
                 + sum(p[f"{z}.LightingPower"] + p[f"{z}.EquipmentPower"] for z in ZONES))
        assert p["EM-01.ElectricPower"] == pytest.approx(parts)

    def test_the_tank_cannot_heat_radiators_hotter_than_itself(self):
        r = P.step(plant(), state(tank=25.0), {z: 18.0 for z in ZONES}, setpoints(),
                   exo(t_out=-20.0), 60.0)
        assert r.points["HYD-01.SupplyTemp"] == 25.0


class TestVentilation:
    def test_fan_power_is_sfp_times_airflow(self):
        p = P.step(plant(), state(), TEMPS, setpoints(), exo(), 60.0).points
        assert p["AHU-01.FanPower"] == pytest.approx(1.5 * p["AHU-01.Airflow"])  # kW/(m3/s) x l/s = W

    def test_unoccupied_airflow_is_the_material_minimum(self):
        p = P.step(plant(), state(), TEMPS, setpoints(), exo(), 60.0).points
        assert p["ZONE-01.Airflow"] == pytest.approx(0.35 * 120.0)

    def test_dcv_raises_airflow_above_the_co2_setpoint(self):
        occ = {"ZONE-01": 10, "ZONE-02": 0, "ZONE-03": 0}
        low = P.step(plant(), state(co2=600.0), TEMPS, setpoints(), exo(occ=occ), 60.0).points
        high = P.step(plant(), state(co2=1050.0), TEMPS, setpoints(), exo(occ=occ), 60.0).points
        assert high["ZONE-01.Airflow"] > low["ZONE-01.Airflow"] >= 0.35 * 120 + 7 * 10

    def test_co2_settles_at_the_mass_balance(self):
        occ = {"ZONE-01": 10, "ZONE-02": 0, "ZONE-03": 0}
        st = state()
        sp = setpoints(zones={z: replace(v, co2_ppm=5000.0) for z, v in setpoints().zones.items()})
        for _ in range(24 * 60):
            st = P.step(plant(), st, TEMPS, sp, exo(occ=occ), 60.0).state
        q = (0.35 * 120 + 70) / 1000.0
        expected = 420.0 + 10 * P.CO2_PER_PERSON_M3_S * 1e6 / q
        assert st.co2_ppm["ZONE-01"] == pytest.approx(expected, rel=0.01)

    def test_heat_recovery_warms_the_intake_and_frost_protection_reduces_it(self):
        mild = P.step(plant(), state(), TEMPS, setpoints(), exo(t_out=-5.0), 60.0).points
        cold = P.step(plant(), state(), TEMPS, setpoints(), exo(t_out=-20.0), 60.0).points
        assert mild["AHU-01.HRV_AfterTemp"] == pytest.approx(-5 + 0.8 * 26)
        assert cold["AHU-01.HRV_Efficiency"] == pytest.approx(0.6)

    def test_ahu_off_means_no_fan_and_no_airflow(self):
        p = P.step(plant(), state(), TEMPS, setpoints(ahu_enabled=False), exo(), 60.0).points
        assert p["AHU-01.Airflow"] == 0.0 and p["AHU-01.FanPower"] == 0.0


class TestLightingCoolingCables:
    def test_daylight_dims_the_electric_light(self):
        occ = {"ZONE-01": 5, "ZONE-02": 0, "ZONE-03": 0}
        dark = P.step(plant(), state(), TEMPS, setpoints(), exo(solar=0.0, occ=occ), 60.0).points
        sunny = P.step(plant(), state(), TEMPS, setpoints(), exo(solar=200.0, occ=occ), 60.0).points
        assert sunny["ZONE-01.LightingPower"] < dark["ZONE-01.LightingPower"]
        assert dark["ZONE-02.LightingPower"] == 0.0  # empty room: lights off

    def test_the_lab_needs_cooling_in_winter_and_the_chiller_pays_for_it(self):
        p = P.step(plant(), state(), {**TEMPS, "ZONE-03": 24.0}, setpoints(), exo(), 60.0).points
        assert p["ZONE-03.CoolingPower"] > 0 and p["CH-01.ElectricPower"] > 0
        assert p["CH-01.ElectricPower"] == pytest.approx(p["CH-01.CoolingLoad"] / p["CH-01.EER"])

    def test_cable_off_during_icing_risk_is_reported_as_a_hazard(self):
        sp = setpoints(cable_modes={"CABLE-RAMP": "off"})
        p = P.step(plant(), state(), TEMPS, sp, exo(icing=True), 60.0).points
        assert p["CABLE-RAMP.IcingRiskUncovered"] == 1.0 and p["CABLE-RAMP.Power"] == 0.0
        assert p["CABLE-GUTTER.Power"] == 3000.0  # auto follows the icing risk


def test_radiator_output_follows_en442():
    assert P.radiator_output_w(1000, 70, 55, 20) == pytest.approx(1000)
    assert P.radiator_output_w(1000, 45, 35, 20) < 0.5 * 1000
    assert P.radiator_output_w(1000, 20, 15, 21) == 0.0


def test_a_bad_config_is_refused():
    with pytest.raises(P.PlantError):
        P.PlantConfig.from_dict({**CFG["plant"], "heat_source": "coal"})
    with pytest.raises(P.PlantError):
        P.PlantConfig.from_dict({**CFG["plant"], "hrv_efficiency": 1.5})
