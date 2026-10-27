"""Seasonal realism: summer stop, holidays and seeded variation behave as specified.

ADR-007 (seasonal additions) and docs/datapoints.md declare these behaviours:
- space heating stops when the 24 h mean outdoor temperature passes the
  heating limit, and restarts with hysteresis;
- with heating off, neither the heat source nor the radiators heat the zones;
- public holidays are unoccupied;
- presence and equipment variation are deterministic per seed and stay inside
  their declared bounds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from twin import plant as P
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "twin" / "fixtures" / "three-zone-hvac-seasonal.json"
YEAR = ROOT / "scenarios" / "trondheim-2025-hvac-v1.json"


def year(seed: int = 177, days: int = 365):
    raw = json.loads(YEAR.read_text("utf-8"))
    raw["seed"] = seed
    raw["duration_seconds"] = days * 86400
    return load_scenario(raw, base_dir=YEAR.parent)


def test_holidays_are_unoccupied():
    sc = year()
    may17_noon = (31 + 28 + 31 + 30 + 16) * 86400 + 10 * 3600  # 2025-05-17 is a Saturday anyway
    christmas_eve = (365 - 8) * 86400 + 10 * 3600              # 2025-12-24, a Wednesday
    ordinary = 1 * 86400 + 10 * 3600                            # 2025-01-02, a Thursday
    assert not sc.occupied(christmas_eve)
    assert not sc.occupied(may17_noon)
    assert sc.occupied(ordinary)


def test_variation_is_seeded_and_bounded():
    a, b, c = year(177), year(177), year(178)
    xs = [a.presence("ZONE-01", d * 86400) for d in range(60)]
    assert xs == [b.presence("ZONE-01", d * 86400) for d in range(60)]
    assert xs != [c.presence("ZONE-01", d * 86400) for d in range(60)]
    assert all(0.2 <= x <= 1.0 for x in xs)
    eq = [a.equipment_factor("ZONE-03", h * 3600) for h in range(24 * 30)]
    assert all(0.6 <= e <= 1.5 for e in eq) and len(set(eq)) > 100


def test_summer_holiday_reduces_presence():
    sc = year()
    july = [sc.presence("ZONE-01", d * 86400) for d in range(188, 206)]   # 7..24 July
    june = [sc.presence("ZONE-01", d * 86400) for d in range(152, 170)]
    assert max(july) <= 0.3 + 1e-9 < min(june) + 0.5 and sum(july) < sum(june) / 2


def test_summer_stop_switches_with_hysteresis():
    tw = Twin(load_config(CFG), year(days=365))
    seen = []
    for hour in range(0, 365 * 24, 6):
        seen.append(tw._heating_season(hour * 3600))
    # Heating is on in January, off for part of the summer, on again in December.
    assert seen[0] and seen[-1] and not all(seen)
    switches = sum(1 for x, y in zip(seen, seen[1:]) if x != y)
    assert 1 <= switches <= 12, f"{switches} switches: the hysteresis should prevent chattering"


def test_no_space_heating_outside_the_heating_season():
    cfg = P.PlantConfig.from_dict(json.loads(CFG.read_text("utf-8"))["plant"])
    d = json.loads(CFG.read_text("utf-8"))["default_setpoints"]
    sp = P.Setpoints(zones={z: P.ZoneSetpoints(**v) for z, v in d["zones"].items()},
                     supply_air_c=d["supply_air_c"], tank_c=d["tank_c"])
    st = P.PlantState(tank_c=30.0, co2_ppm={z: 420.0 for z in sp.zones}, hp_state_since_s=1e9)
    cold_rooms = {z: 18.0 for z in sp.zones}
    exo = P.Exogenous(outdoor_c=16.0, solar_w_m2=0, outdoor_rh_pct=60,
                      occupants={z: 0 for z in sp.zones}, heating_season=False)
    r = P.step(cfg, st, cold_rooms, sp, exo, 60.0).points
    assert r["HYD-01.HeatingSeason"] == 0.0
    assert r["HP-01.Running"] == 0.0 and r["HP-01.BackupHeaterPower"] == 0.0
    assert all(r[f"{z}.RadiatorPower"] == 0.0 for z in sp.zones)
    assert r["AHU-01.HeatingCoilPower"] == 0.0
    for source in ("district_heating", "electric_boiler"):
        alt = P.PlantConfig.from_dict({**json.loads(CFG.read_text("utf-8"))["plant"], "heat_source": source})
        pts = P.step(alt, st, cold_rooms, sp, exo, 60.0).points
        assert pts.get("DH-01.HeatPower", 0.0) == 0.0 and pts.get("EB-01.ElectricPower", 0.0) == 0.0


@pytest.mark.parametrize("season", [True, False])
def test_equipment_factor_scales_the_equipment_load(season):
    cfg = P.PlantConfig.from_dict(json.loads(CFG.read_text("utf-8"))["plant"])
    d = json.loads(CFG.read_text("utf-8"))["default_setpoints"]
    sp = P.Setpoints(zones={z: P.ZoneSetpoints(**v) for z, v in d["zones"].items()},
                     supply_air_c=d["supply_air_c"], tank_c=d["tank_c"])
    st = P.PlantState(tank_c=45.0, co2_ppm={z: 420.0 for z in sp.zones})
    temps = {z: 21.0 for z in sp.zones}

    def lab(factor):
        exo = P.Exogenous(outdoor_c=5.0, solar_w_m2=0, outdoor_rh_pct=60,
                          occupants={z: 0 for z in sp.zones}, heating_season=season,
                          equipment_factor={"ZONE-03": factor})
        return P.step(cfg, st, temps, sp, exo, 60.0).points["ZONE-03.EquipmentPower"]

    assert lab(1.5) == pytest.approx(1.5 * lab(1.0))
