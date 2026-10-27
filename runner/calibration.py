"""Building priors from Norwegian energy certificates, and twin fixtures built from them.

    python -m runner.calibration profiles                       # the profile table (category x size)
    python -m runner.calibration build --category Kontorbygg --size medium --out twin/fixtures/<id>.json
    python -m runner.calibration build --building OT-77e73ccee3ec --out twin/fixtures/<id>.json
    python -m runner.calibration check twin/fixtures/<id>.json  # a P0 year against the profile's band

Source: `docs/kalibreringsgrunnlag_ot_anonymisert.json` (536 anonymised 2015
certificates for offices, retail and hotels, with envelope U-values and
areas, ventilation, internal loads, setpoints, heat capacity and the
calculated delivered energy) and the mapping in
`docs/Enova_OT_kalibreringsark_2026.xlsx` (sheet `OT_mapping`). These are
**priors, not a calibration**: calculated delivered energy is not measured
energy, UA excludes ventilation and infiltration, the normalised heat
capacity is not a verified RC capacity, and nothing here is live telemetry.
A fixture built here says so in its `calibration` block, and `check` only
asks whether a P0 year lands inside the profile's P10..P90 band of
calculated delivered energy, which is a plausibility test, not a fit.

What a profile fixes, and how it is mapped (OT_mapping):

| Twin parameter | From the certificates |
|---|---|
| zone.area_m2 | BRA x zone share (4/9, 3/9, 2/9: the demo's 120/90/60 layout) |
| zone.ua_w_per_k | (UA_transmission/BRA + thermal_bridge) x zone area; excludes ventilation and infiltration |
| zone.capacity_j_per_k | normalised heat capacity Wh/(m2K) x 3600 x zone area (1R1C prior) |
| zone.heater_max_w, plant.radiator_nominal_w | installed heating W/m2 x zone area |
| zone.heating_efficiency | annual system efficiency / 100, clipped to 0.5..1.0 |
| plant.lighting_w_per_m2, equipment_w_per_m2, cooling_max_w | the certificate's W/m2 |
| plant.min_airflow_l_s_m2 | ventilation m3/(m2 h) / 3.6 |
| plant.sfp_kw_per_m3_s, hrv_efficiency | SFP in operation; annual heat-recovery efficiency / 100 |
| default_setpoints heating/cooling | the certificate's setpoints (cooling at least 2 K above heating) |
| envelope.inter_zone_conductance_w_per_k | not in a certificate; the demo's 40 W/K scaled by BRA/270 (stated assumption) |
| plant.cables | none by default: certificates carry no heating cables; `--cables-kw` adds a stated rating |
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics as st
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIORS = ROOT / "docs" / "kalibreringsgrunnlag_ot_anonymisert.json"
TEMPLATE = ROOT / "twin" / "fixtures" / "three-zone-hvac-seasonal-v2.json"
SIZES = {"small": (0, 500), "medium": (500, 2500), "large": (2500, 10 ** 9)}
SIZE_LABEL = {"small": "Lite (<500 m²)", "medium": "Mellom (500-2499 m²)", "large": "Stort (>=2500 m²)"}
ZONE_SHARES = {"ZONE-01": 4 / 9, "ZONE-02": 3 / 9, "ZONE-03": 2 / 9}
NUMERIC = ("BRA_m2", "beregnet_levert_kwh_m2_ar", "u_wall", "u_roof", "u_floor", "u_window", "heat_recovery_eff",
           "sfp_operating", "vent_air", "heating_eff", "heater_w_per_m2", "heat_setpoint", "cool_setpoint",
           "thermal_cap", "thermal_bridge", "leakage", "vent_hours", "heat_hours", "lighting_w_per_m2",
           "equipment_w_per_m2", "cooling_w_per_m2", "ua_transmission_per_bra_w_m2k")
#: Certificate heat source -> the plant's. Direct electric heating (panel heaters) has no water-borne
#: equivalent in the plant; the electric boiler is the closest: electricity to heat at ~1, no COP.
HEAT_SOURCE = {"fjernvarme": "district_heating", "varmepumpe": "heat_pump", "elektrisitet": "electric_boiler",
               "el": "electric_boiler", "direkte elektrisk": "electric_boiler"}
#: Ventilation: the certificate's airflow is the operating-time *total*; the plant adds a per-person part
#: on top of a per-m2 base, so the base is the certificate's total minus the per-person part at the
#: assumed occupant density, never below a materials minimum.
PERSON_L_S = 7.0
M2_PER_PERSON = 12.0
MATERIALS_MIN_L_S_M2 = 0.35


def load_priors(path: Path = PRIORS) -> dict[str, Any]:
    d = json.loads(path.read_text("utf-8"))
    if d.get("schema") != "norwegian-energy-certificate-priors/v1":
        raise ValueError(f"{path}: unexpected schema {d.get('schema')!r}")
    return d


def _num(b: dict[str, Any], k: str) -> float | None:
    v = b.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _q(vals: list[float], p: float) -> float:
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))]


def select(buildings: list[dict[str, Any]], category: str, size: str) -> list[dict[str, Any]]:
    lo, hi = SIZES[size]
    return [b for b in buildings if b.get("bygningskategori") == category and _num(b, "BRA_m2") is not None
            and lo <= _num(b, "BRA_m2") < hi]


def profile(buildings: list[dict[str, Any]], category: str, size: str) -> dict[str, Any]:
    """Medians (and the P10..P90 energy band) of one category x size cohort. Deterministic."""
    sel = select(buildings, category, size)
    if len(sel) < 5:
        raise ValueError(f"only {len(sel)} buildings for {category}/{size}")
    med: dict[str, float] = {}
    for k in NUMERIC:
        vals = [v for b in sel if (v := _num(b, k)) is not None]
        if vals:
            med[k] = round(st.median(vals), 4)
    kwh = [v for b in sel if (v := _num(b, "beregnet_levert_kwh_m2_ar")) is not None]
    # Window share of BRA from the cohort's own areas, not a default.
    win = [_num(b, "area_window") / _num(b, "BRA_m2") for b in sel
           if _num(b, "area_window") is not None and _num(b, "BRA_m2")]
    sources = {}
    for b in sel:
        s = str(b.get("varmekilde") or "").split(";")[0].strip().lower()
        sources[s or "unknown"] = sources.get(s or "unknown", 0) + 1
    return {"category": category, "size": size, "size_label": SIZE_LABEL[size], "n": len(sel),
            "kwh_m2_p10": round(_q(kwh, 0.1), 1), "kwh_m2_median": round(st.median(kwh), 1),
            "kwh_m2_p90": round(_q(kwh, 0.9), 1), "medians": med,
            "window_share_of_bra_median": round(st.median(win), 4) if win else None,
            "heat_sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])),
            "byggeaar_median": st.median([b["byggeaar"] for b in sel if b.get("byggeaar")])}


def profiles(priors: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for cat in sorted({b.get("bygningskategori") for b in priors["buildings"] if b.get("bygningskategori")}):
        for size in SIZES:
            try:
                out.append(profile(priors["buildings"], cat, size))
            except ValueError:
                continue
    return out


def _params_from(p: dict[str, float], bra: float, window_share: float | None, heat_source: str) -> dict[str, Any]:
    """The twin's parameters from a set of certificate medians (or one certificate's values)."""
    g = lambda k, d: float(p[k]) if k in p and p[k] is not None else d  # noqa: E731
    heat_c = g("heat_setpoint", 21.0)
    cool_c = max(g("cool_setpoint", 23.5), heat_c + 2.0)
    return {
        "bra_m2": bra,
        "ua_per_m2": g("ua_transmission_per_bra_w_m2k", 0.6) + g("thermal_bridge", 0.06),
        "capacity_j_per_m2k": g("thermal_cap", 60.0) * 3600.0,
        "heater_w_per_m2": g("heater_w_per_m2", 60.0),
        "heating_efficiency": min(1.0, max(0.5, g("heating_eff", 90.0) / 100.0)),
        "lighting_w_per_m2": g("lighting_w_per_m2", 8.0),
        "equipment_w_per_m2": g("equipment_w_per_m2", 11.0),
        "cooling_w_per_m2": g("cooling_w_per_m2", 12.0),
        "operating_airflow_l_s_m2": round(g("vent_air", 7.0) / 3.6, 3),
        "min_airflow_l_s_m2": round(max(MATERIALS_MIN_L_S_M2, g("vent_air", 7.0) / 3.6 - PERSON_L_S / M2_PER_PERSON), 3),
        "sfp_kw_per_m3_s": g("sfp_operating", 2.0),
        "hrv_efficiency": min(0.95, max(0.0, g("heat_recovery_eff", 60.0) / 100.0)),
        "heating_c": heat_c, "cooling_c": cool_c,
        "window_share_of_bra": window_share if window_share is not None else 0.1,
        "heat_source": heat_source,
    }


def build_fixture(params: dict[str, Any], *, building_id: str, template: Path = TEMPLATE,
                  cables_kw: float = 0.0, source: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(json.loads(template.read_text("utf-8")))
    bra = float(params["bra_m2"])
    cfg["building_id"] = building_id
    scale = bra / 270.0
    cfg["envelope"]["inter_zone_conductance_w_per_k"] = round(40.0 * scale, 2)
    total_heat_w = 0.0
    for z in cfg["zones"]:
        share = ZONE_SHARES[z["id"]]
        area = round(bra * share, 1)
        z["area_m2"] = area
        z["ua_w_per_k"] = round(params["ua_per_m2"] * area, 2)
        z["capacity_j_per_k"] = round(params["capacity_j_per_m2k"] * area, 0)
        z["heater_max_w"] = round(params["heater_w_per_m2"] * area, 0)
        z["heating_efficiency"] = round(params["heating_efficiency"], 3)
        z["initial_setpoint_c"] = params["heating_c"]
        z["initial_temp_c"] = params["heating_c"]
        total_heat_w += z["heater_max_w"]
    # The explicit stability condition the twin asserts (step * G / C < 0.5) holds comfortably with
    # certificate capacities; keep the check here so a thin building is refused at build time.
    for z in cfg["zones"]:
        g_sum = z["ua_w_per_k"] + z["heater_gain_w_per_k"] + 2 * cfg["envelope"]["inter_zone_conductance_w_per_k"]
        if cfg["step_seconds"] * g_sum / z["capacity_j_per_k"] >= 0.5:
            raise ValueError(f"{z['id']}: unstable at the twin's step (G {g_sum:.0f} W/K, C {z['capacity_j_per_k']:.0f} J/K)")
    pl = cfg["plant"]
    for pz in pl["zones"]:
        share = ZONE_SHARES[pz["zone_id"]]
        area = round(bra * share, 1)
        pz["area_m2"] = area
        pz["volume_m3"] = round(area * 3.0, 1)
        pz["window_area_m2"] = round(params["window_share_of_bra"] * area, 1)
        pz["radiator_nominal_w"] = round(params["heater_w_per_m2"] * area, 0)
        pz["cooling_max_w"] = round(params["cooling_w_per_m2"] * area, 0)
        pz["equipment_w_per_m2"] = params["equipment_w_per_m2"]
        if "equipment_base_w_per_m2" in params:
            pz["equipment_base_w_per_m2"] = params["equipment_base_w_per_m2"]
        pz["lighting_w_per_m2"] = params["lighting_w_per_m2"]
        pz["min_airflow_l_s_m2"] = params["min_airflow_l_s_m2"]
        pz["airflow_per_person_l_s"] = PERSON_L_S
        pz["max_airflow_l_s"] = round(max(params["operating_airflow_l_s_m2"] * area * 1.5, 60.0), 0)
    pl["heat_source"] = params["heat_source"]
    if params["heat_source"] == "district_heating":
        # The certificate's annual system efficiency covers substation and distribution; the plant has
        # only the substation term, so it carries the whole of it.
        pl["dh_substation_efficiency"] = round(params["heating_efficiency"], 3)
    pl["sfp_kw_per_m3_s"] = params["sfp_kw_per_m3_s"]
    pl["hrv_efficiency"] = params["hrv_efficiency"]
    pl["hp_nominal_heat_w"] = round(total_heat_w, 0)
    pl["backup_heater_w"] = round(0.4 * total_heat_w, 0)
    pl["dh_max_w"] = round(1.5 * total_heat_w, 0)
    pl["boiler_max_w"] = round(1.2 * total_heat_w, 0)
    pl["ahu_coil_max_w"] = round(0.6 * total_heat_w, 0)
    pl["tank_volume_l"] = round(max(500.0, 1500.0 * scale), 0)
    pl["cables"] = [{"circuit": "CABLE-RAMP", "rated_w": round(cables_kw * 1000.0, 0)}] if cables_kw > 0 else []
    for zid, sp in cfg["default_setpoints"]["zones"].items():
        sp["heating_c"] = params["heating_c"]
        sp["cooling_c"] = params["cooling_c"]
    cfg["default_setpoints"]["cable_modes"] = {"CABLE-RAMP": "thermostat"} if cables_kw > 0 else {}
    cfg["design_occupants"] = {z["id"]: max(1, int(round(z["area_m2"] / M2_PER_PERSON))) for z in cfg["zones"]}
    cfg["comment"] = ("Built by runner.calibration from Norwegian energy-certificate priors; see the calibration "
                      "block. Priors, not a calibration: calculated delivered energy, not measured.")
    cfg["calibration"] = {
        "schema": "ot-calibration-provenance/v1",
        "priors_file": PRIORS.relative_to(ROOT).as_posix(),
        "priors_sha256": hashlib.sha256(PRIORS.read_bytes()).hexdigest(),
        "template": template.relative_to(ROOT).as_posix(),
        "source": source or {}, "params": params,
        "assumptions": [
            "zone shares 4/9, 3/9, 2/9 of BRA (the demo layout); a real site's zoning replaces this",
            "inter-zone conductance is the demo's 40 W/K scaled by BRA/270; not in any certificate",
            "UA is transmission plus a thermal-bridge allowance; ventilation and infiltration are the AHU's",
            "heat capacity is the certificate's normalised value, not a measured RC time constant",
            "occupants: one per 12 m2 of zone area, an assumption; schedules are the scenario's",
            "ventilation base = certificate operating airflow minus 7 l/s per person at that density, min 0.35 l/s m2",
            "direct electric heating is modelled as the plant's electric boiler (heat at ~1 per kWh, no COP)",
            "no heating cables unless --cables-kw is given; certificates carry none",
            "calculated delivered energy (the profile band) is not measured energy",
        ],
    }
    return cfg


def dominant_heat_source(p: dict[str, Any]) -> str:
    """The cohort's most common known heat source, mapped to the plant's; heat_pump only if that is it."""
    for src, _ in p["heat_sources"].items():
        if src in HEAT_SOURCE:
            return HEAT_SOURCE[src]
    return "electric_boiler"


def build_from_profile(priors: dict[str, Any], category: str, size: str, *, building_id: str,
                       cables_kw: float = 0.0, heat_source: str | None = None) -> dict[str, Any]:
    p = profile(priors["buildings"], category, size)
    heat_source = heat_source or dominant_heat_source(p)
    params = _params_from(p["medians"], p["medians"]["BRA_m2"], p["window_share_of_bra_median"], heat_source)
    return build_fixture(params, building_id=building_id, cables_kw=cables_kw,
                         source={"kind": "profile", "category": category, "size": size, "n": p["n"],
                                 "kwh_m2_band": [p["kwh_m2_p10"], p["kwh_m2_median"], p["kwh_m2_p90"]],
                                 "heat_sources_in_cohort": p["heat_sources"]})


def build_from_building(priors: dict[str, Any], building: str, *, building_id: str | None = None,
                        cables_kw: float = 0.0) -> dict[str, Any]:
    b = next((x for x in priors["buildings"] if x.get("id") == building), None)
    if b is None:
        raise ValueError(f"no building {building!r} in the priors")
    vals = {k: _num(b, k) for k in NUMERIC}
    src = str(b.get("varmekilde") or "").split(";")[0].strip().lower()
    hs = HEAT_SOURCE.get(src, "heat_pump")
    win = (_num(b, "area_window") / _num(b, "BRA_m2")) if _num(b, "area_window") and _num(b, "BRA_m2") else None
    params = _params_from(vals, _num(b, "BRA_m2"), win, hs)
    return build_fixture(params, building_id=building_id or f"CAL-{building}", cables_kw=cables_kw,
                         source={"kind": "certificate", "id": b["id"], "category": b.get("bygningskategori"),
                                 "energikarakter": b.get("energikarakter"), "byggeaar": b.get("byggeaar"),
                                 "kwh_m2_calculated": _num(b, "beregnet_levert_kwh_m2_ar"), "varmekilde": b.get("varmekilde")})


def p0_year_kwh_m2(fixture: Path, scenario: Path | None = None) -> dict[str, Any]:
    """A P0 (as-found schedule) year on the fixture: delivered kWh per m2 and the composition."""
    from runner.areas import scenario_path
    from runner.baseline import run_baseline
    from runner.scoring import score
    from twin.model import load_config

    cfg = load_config(fixture)
    sc = scenario or scenario_path("NO3")
    tw = run_baseline(cfg, sc)
    s = score(tw)
    bra = sum(z["area_m2"] for z in cfg["zones"])
    pts = getattr(tw, "consumer_kwh", None)
    return {"bra_m2": bra, "kwh": s["energy_kwh"], "kwh_m2": round(s["energy_kwh"] / bra, 1),
            "electric_kwh": round(tw.electric_kwh, 1), "district_heat_kwh": round(tw.district_heat_kwh, 1),
            "comfort_dh": s["comfort_violation_degree_hours"], "scenario": str(sc), "consumers": pts}


def check(fixture: Path) -> dict[str, Any]:
    cfg = json.loads(fixture.read_text("utf-8"))
    cal = cfg.get("calibration") or {}
    src = cal.get("source") or {}
    res = p0_year_kwh_m2(fixture)
    band = src.get("kwh_m2_band")
    if band:
        lo, med, hi = band
    elif src.get("kwh_m2_calculated"):
        med = float(src["kwh_m2_calculated"])
        lo, hi = 0.7 * med, 1.3 * med
    else:
        lo = med = hi = None
    verdict = ("INSIDE_BAND" if lo is not None and lo <= res["kwh_m2"] <= hi else
               "OUTSIDE_BAND" if lo is not None else "NO_BAND")
    return {**res, "band_p10_median_p90": [lo, med, hi], "verdict": verdict,
            "note": "A P0 year on verified NO3 weather and prices against the cohort's calculated delivered energy: "
                    "plausibility, not calibration. The certificate's number is a normal-year calculation of all "
                    "delivered energy; the twin's is simulated electricity and heat for one year's weather."}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.calibration")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("profiles")
    b = sub.add_parser("build")
    b.add_argument("--category", default="Kontorbygg")
    b.add_argument("--size", choices=list(SIZES), default="small")
    b.add_argument("--building", default=None, help="a certificate id instead of a profile")
    b.add_argument("--building-id", default=None)
    b.add_argument("--cables-kw", type=float, default=0.0)
    b.add_argument("--heat-source", default=None, choices=["heat_pump", "district_heating", "electric_boiler"],
                   help="default: the cohort's dominant heat source")
    b.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("fixture")
    a = p.parse_args(argv)
    priors = load_priors()
    if a.cmd == "profiles":
        print(f"{'category':16s} {'size':8s} {'n':>4s} {'kWh/m2 p10':>10s} {'median':>7s} {'p90':>7s} {'BRA':>7s} "
              f"{'UA/BRA':>7s} {'Cap Wh/m2K':>10s} {'heat W/m2':>9s} {'eff%':>5s} {'HRV%':>5s} {'SFP':>5s} {'vent':>5s} {'light':>6s} {'equip':>6s}")
        for pr in profiles(priors):
            m = pr["medians"]
            print(f"{pr['category']:16s} {pr['size']:8s} {pr['n']:4d} {pr['kwh_m2_p10']:10.1f} {pr['kwh_m2_median']:7.1f} "
                  f"{pr['kwh_m2_p90']:7.1f} {m.get('BRA_m2', 0):7.0f} {m.get('ua_transmission_per_bra_w_m2k', 0):7.3f} "
                  f"{m.get('thermal_cap', 0):10.1f} {m.get('heater_w_per_m2', 0):9.1f} {m.get('heating_eff', 0):5.0f} "
                  f"{m.get('heat_recovery_eff', 0):5.0f} {m.get('sfp_operating', 0):5.2f} {m.get('vent_air', 0):5.1f} "
                  f"{m.get('lighting_w_per_m2', 0):6.1f} {m.get('equipment_w_per_m2', 0):6.1f}")
        return 0
    if a.cmd == "build":
        if a.building:
            cfg = build_from_building(priors, a.building, building_id=a.building_id, cables_kw=a.cables_kw)
        else:
            bid = a.building_id or f"CAL-{a.category.lower()}-{a.size}"
            cfg = build_from_profile(priors, a.category, a.size, building_id=bid, cables_kw=a.cables_kw,
                                     heat_source=a.heat_source)
        out = Path(a.out)
        out.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        z = cfg["zones"]
        print(f"{out}: {cfg['building_id']}, BRA {sum(x['area_m2'] for x in z):.0f} m2, UA {sum(x['ua_w_per_k'] for x in z):.0f} W/K, "
              f"C {sum(x['capacity_j_per_k'] for x in z) / 1e6:.1f} MJ/K, heat {sum(x['heater_max_w'] for x in z) / 1000:.1f} kW, "
              f"{cfg['plant']['heat_source']}, cables {cfg['plant']['cables'] or 'none'}")
        return 0
    res = check(Path(a.fixture))
    print(json.dumps(res, indent=2, default=str))
    return 0 if res["verdict"] != "OUTSIDE_BAND" else 1


if __name__ == "__main__":
    sys.exit(main())
