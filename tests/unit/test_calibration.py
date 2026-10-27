"""Building priors from energy certificates (runner.calibration).

- the cohort profiles are deterministic and reproduce the calibration sheet's start profiles
  (small office: 88 buildings, median 249.5 kWh/m2, UA/BRA, HRV, SFP, airflow);
- a fixture built from a profile is a valid twin config: consistent zone areas on both the
  thermal and the plant side (the demo fixture has them inconsistent), the stability condition
  holds, certificate values land where OT_mapping says, no cables unless asked, provenance recorded;
- a fixture built from one certificate carries that certificate's heat source and band;
- a short P0 run on a built fixture works end to end and reports delivered energy per m2.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner import calibration as cal
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
PRIORS = cal.load_priors()


def test_profiles_are_deterministic_and_match_the_sheets_start_profiles():
    a, b = cal.profiles(PRIORS), cal.profiles(PRIORS)
    assert a == b
    small = cal.profile(PRIORS["buildings"], "Kontorbygg", "small")
    assert small["n"] == 88 and small["kwh_m2_median"] == 249.5
    m = small["medians"]
    assert m["BRA_m2"] == 296.5 and m["heat_recovery_eff"] == 63 and m["sfp_operating"] == 2.0 and m["vent_air"] == 7.0
    assert round(m["u_wall"], 2) == 0.34 and round(m["u_window"], 2) == 1.88
    large = cal.profile(PRIORS["buildings"], "Kontorbygg", "large")
    assert large["n"] == 95 and large["kwh_m2_median"] == 182.5
    assert small["kwh_m2_p10"] < small["kwh_m2_median"] < small["kwh_m2_p90"]


def test_fixture_from_profile_is_consistent_and_mapped_as_documented(tmp_path):
    cfg = cal.build_from_profile(PRIORS, "Kontorbygg", "small", building_id="T-small")
    zones = {z["id"]: z for z in cfg["zones"]}
    plant = {z["zone_id"]: z for z in cfg["plant"]["zones"]}
    # Thermal and plant sides agree on every zone's area, and they sum to BRA.
    for zid in zones:
        assert zones[zid]["area_m2"] == plant[zid]["area_m2"]
    assert abs(sum(z["area_m2"] for z in cfg["zones"]) - 296.5) < 0.5
    p = cal.profile(PRIORS["buildings"], "Kontorbygg", "small")["medians"]
    z1 = zones["ZONE-01"]
    assert abs(z1["ua_w_per_k"] - (p["ua_transmission_per_bra_w_m2k"] + p["thermal_bridge"]) * z1["area_m2"]) < 0.1
    assert abs(z1["capacity_j_per_k"] - p["thermal_cap"] * 3600 * z1["area_m2"]) < 1.0
    assert abs(z1["heater_max_w"] - p["heater_w_per_m2"] * z1["area_m2"]) < 1.0
    assert plant["ZONE-01"]["lighting_w_per_m2"] == p["lighting_w_per_m2"]
    assert plant["ZONE-01"]["equipment_w_per_m2"] == p["equipment_w_per_m2"]
    # The certificate's airflow is the operating-time total; the plant adds 7 l/s per person on top of the
    # per-m2 base, so the base is the total minus that at one person per 12 m2 (never below 0.35 l/s m2).
    assert abs(plant["ZONE-01"]["min_airflow_l_s_m2"] - max(0.35, p["vent_air"] / 3.6 - 7.0 / 12.0)) < 1e-3
    assert plant["ZONE-01"]["airflow_per_person_l_s"] == 7.0
    # The cohort is mostly direct electric heating, modelled as the plant's electric boiler, not a heat pump.
    assert cfg["plant"]["heat_source"] == "electric_boiler"
    assert cfg["plant"]["hrv_efficiency"] == pytest.approx(p["heat_recovery_eff"] / 100)
    assert cfg["plant"]["sfp_kw_per_m3_s"] == p["sfp_operating"]
    assert cfg["plant"]["cables"] == [] and cfg["default_setpoints"]["cable_modes"] == {}
    assert cfg["default_setpoints"]["zones"]["ZONE-01"]["cooling_c"] >= cfg["default_setpoints"]["zones"]["ZONE-01"]["heating_c"] + 2
    c = cfg["calibration"]
    assert c["source"]["kind"] == "profile" and c["source"]["n"] == 88 and len(c["priors_sha256"]) == 64
    assert any("not measured" in a for a in c["assumptions"])
    # The twin accepts it and the stability condition holds.
    out = tmp_path / "f.json"
    out.write_text(json.dumps(cfg), "utf-8")
    tw = Twin(load_config(out), load_scenario(ROOT / "scenarios" / "baseline-day-hvac-v1.json"))
    assert tw.plant is not None
    with_cables = cal.build_from_profile(PRIORS, "Kontorbygg", "small", building_id="T", cables_kw=3.0)
    assert with_cables["plant"]["cables"] == [{"circuit": "CABLE-RAMP", "rated_w": 3000.0}]


def test_fixture_from_one_certificate_keeps_its_heat_source_and_band():
    cfg = cal.build_from_building(PRIORS, "OT-77e73ccee3ec")
    assert cfg["plant"]["heat_source"] == "district_heating"
    assert cfg["plant"]["dh_substation_efficiency"] == 0.83  # the certificate's annual system efficiency
    s = cfg["calibration"]["source"]
    assert s["kind"] == "certificate" and s["kwh_m2_calculated"] == 220.8 and s["byggeaar"] == 1920
    assert abs(sum(z["area_m2"] for z in cfg["zones"]) - 1500.0) < 0.5
    with pytest.raises(ValueError):
        cal.build_from_building(PRIORS, "OT-does-not-exist")


def test_short_p0_run_reports_delivered_energy_per_m2(tmp_path):
    out = tmp_path / "f.json"
    out.write_text(json.dumps(cal.build_from_profile(PRIORS, "Kontorbygg", "small", building_id="T")), "utf-8")
    res = cal.p0_year_kwh_m2(out, scenario=ROOT / "scenarios" / "baseline-day-hvac-v1.json")
    assert res["bra_m2"] > 0 and res["kwh"] > 0 and res["kwh_m2"] == round(res["kwh"] / res["bra_m2"], 1)


def test_the_committed_calibrated_fixtures_rebuild_byte_for_byte():
    folder = ROOT / "twin" / "fixtures" / "calibrated"
    files = sorted(folder.glob("*.json"))
    assert files, "the calibrated office catalogue is committed"
    for f in files:
        cfg = json.loads(f.read_text("utf-8"))
        src = cfg["calibration"]["source"]
        if src["kind"] == "profile":
            rebuilt = cal.build_from_profile(PRIORS, src["category"], src["size"], building_id=cfg["building_id"],
                                             cables_kw=(cfg["plant"]["cables"][0]["rated_w"] / 1000 if cfg["plant"]["cables"] else 0.0))
        elif src["kind"] == "measured_site":
            # Fitted against a measured site: the fit's parameters are recorded, so the fixture rebuilds from them.
            p = cfg["calibration"]["params"]
            rebuilt = cal.build_fixture(p, building_id=cfg["building_id"], source=src)
            assert {"bra_m2", "equipment_base_w_per_m2", "lighting_w_per_m2", "equipment_w_per_m2"} <= set(p)
            assert cfg["plant"]["cables"] == []
        else:
            rebuilt = cal.build_from_building(PRIORS, src["id"], building_id=cfg["building_id"])
        assert rebuilt == cfg, f.name
