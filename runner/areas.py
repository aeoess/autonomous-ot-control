"""One run over all five Norwegian price areas: P0, P1 and P2 for the 2025 year.

    python -m runner.areas --seeds 177 178 --out artifacts/areas-2025

Each area has a fictitious building at a real coordinate
(`twin/fixtures/site-*.json`) and a year scenario
(`scenarios/<area>-2025-hvac-v1.json`). The scenario carries that area's
verified day-ahead prices and the local ERA5 weather. Every strategy
(`runner/strategies.py`) runs over the full year per seed, in parallel
processes, on `twin/fixtures/three-zone-hvac-seasonal-v2.json`.

Per day it records:
- energy by consumer;
- spot cost and the grid tariff's energy part;
- comfort while occupied;
- icing-hazard hours (ground sensor wet at -10..+1 degC with the cable off);
- heating-season hours.

The power part of the tariff is charged per month on that month's highest
hourly mean kW. Results aggregate to month, quarter, half and year, and P1 and
P2 are paired with P0 per seed.

Everything is a synthetic, uncalibrated building. The cost is spot plus a
declared reference grid tariff (ASSUMPTION, taxes excluded), not a real
saving.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "twin" / "fixtures" / "three-zone-hvac-seasonal-v2.json"
AREAS = {"NO1": "Oslo", "NO2": "Kristiansand", "NO3": "Trondheim", "NO4": "Tromsø", "NO5": "Bergen"}
METERS = {
    "heat_pump": ("HP-01.ElectricPower",), "backup_heater": ("HP-01.BackupHeaterPower",),
    "fans": ("AHU-01.FanPower",), "cooling": ("CH-01.ElectricPower",),
    "heating_cables": ("CABLE-RAMP.Power", "CABLE-GUTTER.Power"),
    "lighting": tuple(f"ZONE-0{i}.LightingPower" for i in (1, 2, 3)),
    "equipment": tuple(f"ZONE-0{i}.EquipmentPower" for i in (1, 2, 3)),
}


def scenario_path(area: str) -> Path:
    return ROOT / "scenarios" / f"{area.lower()}-2025-hvac-v1.json"


def run_one(args: tuple[str, str, int, int]) -> dict[str, Any]:
    area, strategy, seed, days = args
    from runner.demo import STEP_S
    from runner.strategies import Controller
    from twin.model import Twin, load_config
    from twin.scenarios import load_scenario

    sp = scenario_path(area)
    raw = json.loads(sp.read_text("utf-8"))
    raw["seed"], raw["duration_seconds"] = seed, days * 86400
    sc = load_scenario(raw, base_dir=sp.parent)
    tw = Twin(load_config(CONFIG), sc)
    ctl = Controller(strategy, tw)
    start = datetime.fromisoformat(sc.start_utc.replace("Z", "+00:00"))
    lo, hi = sc.comfort_band()
    days_out: list[dict[str, Any]] = []
    day = _new_day(start)
    grid0 = 0.0
    for t in range(0, days * 86400, STEP_S):
        if t and t % 86400 == 0:
            days_out.append(day)
            day = _new_day(start + timedelta(seconds=t))
        ctl.step(t)
        cost0, kwh0 = tw.cost, tw.energy_kwh
        for _ in range(STEP_S // tw.step_s):
            if sc.occupied(tw.clock.now_s):
                for z in tw.zones.values():
                    day["comfort_dh"] += (max(lo - z.temp_c, 0) + max(z.temp_c - hi, 0)) * tw.step_s / 3600
            tw._step()
            p = tw.points
            for k, ids in METERS.items():
                day[k] += sum(p.get(i, 0.0) for i in ids) * tw.step_s / 3.6e6
            day["hazard_h"] += max(p.get("CABLE-RAMP.IcingHazardUncovered", 0.0),
                                   p.get("CABLE-GUTTER.IcingHazardUncovered", 0.0)) * tw.step_s / 3600
            day["heating_season_h"] += p.get("HYD-01.HeatingSeason", 0.0) * tw.step_s / 3600
        day["kwh"] += tw.energy_kwh - kwh0
        day["spot_nok"] += tw.cost - cost0
        g = getattr(tw, "grid_energy_cost", 0.0)
        day["grid_energy_nok"] += g - grid0
        grid0 = g
    days_out.append(day)
    tariff = tw.config["plant"]["grid_tariff"]
    winter = set(tariff["winter_months"])
    peaks = dict(getattr(tw, "monthly_peak_kw", {}))
    if getattr(tw, "_hour_key", None):
        m = tw._hour_key[:7]
        peaks[m] = max(peaks.get(m, 0.0), tw._hour_kwh)
    power = {m: kw * float(tariff["power_nok_per_kw_month"]["winter" if int(m[5:]) in winter else "summer"])
             for m, kw in peaks.items()}
    return {"area": area, "strategy": strategy, "seed": seed, "days": days_out,
            "monthly_peak_kw": peaks, "monthly_power_nok": power, "setpoint_changes": ctl.changes}


def _new_day(d: datetime) -> dict[str, Any]:
    return {"date": d.astimezone(UTC).date().isoformat(), "kwh": 0.0, "spot_nok": 0.0, "grid_energy_nok": 0.0,
            "comfort_dh": 0.0, "hazard_h": 0.0, "heating_season_h": 0.0, **{k: 0.0 for k in METERS}}


def _period_keys(date: str) -> dict[str, str]:
    y, m, _ = date.split("-")
    return {"month": f"{y}-{m}", "quarter": f"{y}-Q{(int(m) - 1) // 3 + 1}",
            "half": f"{y}-H{1 if int(m) <= 6 else 2}", "year": y}


def aggregate(run: dict[str, Any]) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {lv: defaultdict(lambda: defaultdict(float))
                                                   for lv in ("month", "quarter", "half", "year")}
    for d in run["days"]:
        for lv, key in _period_keys(d["date"]).items():
            for f, v in d.items():
                if f != "date":
                    out[lv][key][f] += v
    for month, nok in run["monthly_power_nok"].items():
        for lv, key in _period_keys(month + "-01").items():
            out[lv][key]["grid_power_nok"] += nok
            if lv == "month":
                out[lv][key]["peak_kw"] = run["monthly_peak_kw"][month]
    for lv in out.values():
        for v in lv.values():
            v["total_nok"] = v["spot_nok"] + v["grid_energy_nok"] + v.get("grid_power_nok", 0.0)
    return {lv: {k: dict(v) for k, v in d.items()} for lv, d in out.items()}


def summarize(runs: list[dict[str, Any]], seeds: list[int]) -> dict[str, Any]:
    by = {(r["area"], r["strategy"], r["seed"]): aggregate(r) for r in runs}
    table: dict[str, Any] = {}
    for area in AREAS:
        table[area] = {}
        for lv in ("year", "half", "quarter", "month"):
            table[area][lv] = {}
            for key in by[(area, "P0", seeds[0])][lv]:
                row: dict[str, Any] = {}
                for s in ("P0", "P1", "P2"):
                    vals = [by[(area, s, sd)][lv][key] for sd in seeds]
                    row[s] = {f: statistics.mean(v[f] for v in vals) for f in vals[0]}
                for s in ("P1", "P2"):
                    sav = [by[(area, "P0", sd)][lv][key]["total_nok"] - by[(area, s, sd)][lv][key]["total_nok"]
                           for sd in seeds]
                    row[f"{s}_saving_nok"] = {"mean": statistics.mean(sav), "min": min(sav), "max": max(sav)}
                    row[f"{s}_saving_pct"] = 100 * statistics.mean(sav) / row["P0"]["total_nok"]
                table[area][lv][key] = row
    return {"seeds": seeds, "areas": AREAS, "table": table,
            "scope": ("Synthetic, uncalibrated building-services twin (three-zone-hvac-seasonal-v2) at fictitious "
                      "sites in each Norwegian price area. Verified 2025 day-ahead prices per area and local ERA5 "
                      "weather. Cost = spot + a declared reference grid tariff (ASSUMPTION; taxes excluded). "
                      "Strategies applied directly (offline); not a real-building saving.")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.areas")
    p.add_argument("--seeds", type=int, nargs="+", default=[177, 178])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=6)
    a = p.parse_args(argv)
    jobs = [(area, s, sd, a.days) for area in AREAS for s in ("P0", "P1", "P2") for sd in a.seeds]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        runs = list(ex.map(run_one, jobs))
    res = summarize(runs, a.seeds)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "areas.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    for area, t in res["table"].items():
        y = next(iter(t["year"].values()))
        print(f"{area} {AREAS[area]:12} P0 {y['P0']['total_nok']:8.0f} NOK ({y['P0']['kwh']:6.0f} kWh)  "
              f"P1 saves {y['P1_saving_nok']['mean']:6.0f} ({y['P1_saving_pct']:4.1f} %)  "
              f"P2 saves {y['P2_saving_nok']['mean']:6.0f} ({y['P2_saving_pct']:4.1f} %)  "
              f"hazard P0 {y['P0']['hazard_h']:.0f} h P2 {y['P2']['hazard_h']:.0f} h  "
              f"comfort P0 {y['P0']['comfort_dh']:.0f} P2 {y['P2']['comfort_dh']:.0f} dh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
