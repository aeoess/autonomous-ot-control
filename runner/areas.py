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


RECORD_DAYS = {"2025-01-15", "2025-07-15"}


def fault_disturbances(area: str, seed: int, days: int) -> list[dict[str, Any]]:
    """Seeded sensor faults, the same for every strategy of one area and seed.

    Per zone and year: two frozen sensors (6..48 h; the reading stops updating,
    so it turns stale) and one drifted sensor (+-3 K for 3..10 days, with fresh
    timestamps). The local radiator loop in the twin regulates on the true
    temperature; the faults reach the supervisory layer only.
    """
    import random

    rng = random.Random(f"{seed}:{area}:sensor-faults")
    horizon = days * 86400
    out: list[dict[str, Any]] = []
    for z in ("ZONE-01", "ZONE-02", "ZONE-03"):
        for _ in range(2):
            t0 = rng.randrange(0, max(1, horizon - 2 * 86400), 300)
            out.append({"kind": "sensor_freeze", "zone_id": z, "from_s": t0,
                        "to_s": min(horizon, t0 + rng.randrange(6, 49) * 3600)})
        t0 = rng.randrange(0, max(1, horizon - 11 * 86400), 300)
        out.append({"kind": "sensor_offset", "zone_id": z, "from_s": t0,
                    "to_s": min(horizon, t0 + rng.randrange(3, 11) * 86400),
                    "offset_c": rng.choice((3.0, -3.0))})
    return out


OPTIMIZER_FAULT_RATE = 0.01


def run_one(args: tuple[str, str, int, int, bool]) -> dict[str, Any]:
    """One (area, strategy, seed) year. With faults: seeded sensor faults in every
    strategy, and for P3/P3raw 1 % of the proposals corrupted before validation."""
    area, strategy, seed, days, faults = args
    from runner.caretaker import Caretaker
    from runner.demo import STEP_S
    from runner.strategies import Controller
    from twin.model import Twin, load_config
    from twin.scenarios import load_scenario

    sp = scenario_path(area)
    raw = json.loads(sp.read_text("utf-8"))
    raw["seed"], raw["duration_seconds"] = seed, days * 86400
    if faults:
        raw["disturbances"] = list(raw.get("disturbances", [])) + fault_disturbances(area, seed, days)
    sc = load_scenario(raw, base_dir=sp.parent)
    tw = Twin(load_config(CONFIG), sc)
    ctl: Any
    if strategy in ("P3", "P3raw"):
        ctl = Caretaker(tw, validated=strategy == "P3", record_days=RECORD_DAYS,
                        proposal_fault_rate=OPTIMIZER_FAULT_RATE if faults else 0.0, seed=seed)
    else:
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
            if sc.occupied(tw.clock.now_s):   # IAQ: CO2 above the IL-07 ceiling while occupied
                day["co2_excess_ppmh"] += sum(max(0.0, p.get(f"ZONE-0{i}.CO2", 0.0) - 1000.0)
                                              for i in (1, 2, 3)) * tw.step_s / 3600
            day["tank_over_60_h"] += (tw.plant_state.tank_c > 60.0) * tw.step_s / 3600
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
    out = {"area": area, "strategy": strategy, "seed": seed, "days": days_out, "faults": faults,
           "monthly_peak_kw": peaks, "monthly_power_nok": power, "setpoint_changes": ctl.changes}
    if isinstance(ctl, Caretaker):
        from dataclasses import asdict
        ctl.finish()
        out["caretaker"] = asdict(ctl.stats)
        out["records"] = ctl.records
        out["alarms"] = ctl.alarms
    return out


def _new_day(d: datetime) -> dict[str, Any]:
    return {"date": d.astimezone(UTC).date().isoformat(), "kwh": 0.0, "spot_nok": 0.0, "grid_energy_nok": 0.0,
            "comfort_dh": 0.0, "hazard_h": 0.0, "heating_season_h": 0.0, "co2_excess_ppmh": 0.0,
            "tank_over_60_h": 0.0, **{k: 0.0 for k in METERS}}


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


def _mean_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in items[0].items():
        if isinstance(v, dict):
            keys = sorted({kk for it in items for kk in it[k]})
            out[k] = {kk: statistics.mean(it[k].get(kk, 0) for it in items) for kk in keys}
        else:
            out[k] = statistics.mean(it[k] for it in items)
    return out


def summarize(runs: list[dict[str, Any]], seeds: list[int], strategies: list[str] | None = None,
              faults: bool = False) -> dict[str, Any]:
    strategies = strategies or ["P0", "P1", "P2"]
    by = {(r["area"], r["strategy"], r["seed"]): aggregate(r) for r in runs}
    table: dict[str, Any] = {}
    for area in AREAS:
        table[area] = {}
        for lv in ("year", "half", "quarter", "month"):
            table[area][lv] = {}
            for key in by[(area, strategies[0], seeds[0])][lv]:
                row: dict[str, Any] = {}
                for s in strategies:
                    vals = [by[(area, s, sd)][lv][key] for sd in seeds]
                    row[s] = {f: statistics.mean(v[f] for v in vals) for f in vals[0]}
                for s in strategies[1:]:
                    sav = [by[(area, strategies[0], sd)][lv][key]["total_nok"]
                           - by[(area, s, sd)][lv][key]["total_nok"] for sd in seeds]
                    row[f"{s}_saving_nok"] = {"mean": statistics.mean(sav), "min": min(sav), "max": max(sav)}
                    row[f"{s}_saving_pct"] = 100 * statistics.mean(sav) / row[strategies[0]]["total_nok"]
                table[area][lv][key] = row
    caretaker = {area: {s: _mean_stats([r["caretaker"] for r in runs if r["area"] == area and r["strategy"] == s])
                        for s in strategies if s in ("P3", "P3raw")} for area in AREAS}
    res: dict[str, Any] = {"seeds": seeds, "areas": AREAS, "strategies": strategies, "faults": faults,
                           "table": table}
    if any(caretaker.values()):
        res["caretaker"] = caretaker
    return {**res,
            "scope": ("Synthetic, uncalibrated building-services twin (three-zone-hvac-seasonal-v2) at fictitious "
                      "sites in each Norwegian price area. Verified 2025 day-ahead prices per area and local ERA5 "
                      "weather. Cost = spot + a declared reference grid tariff (ASSUMPTION; taxes excluded). "
                      "Strategies applied directly (offline); not a real-building saving."
                      + (" Seeded sensor faults (frozen and drifted zone sensors) in every strategy." if faults else ""))}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.areas")
    p.add_argument("--seeds", type=int, nargs="+", default=[177, 178])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--strategies", nargs="+", default=["P0", "P1", "P2", "P3"],
                   help="the first is the baseline every other is paired with")
    p.add_argument("--faults", action="store_true", help="seeded sensor faults in every strategy")
    a = p.parse_args(argv)
    jobs = [(area, s, sd, a.days, a.faults) for area in AREAS for s in a.strategies for sd in a.seeds]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        runs = list(ex.map(run_one, jobs))
    res = summarize(runs, a.seeds, a.strategies, a.faults)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "areas.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    with (out / "decision-records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in runs:
            if r["seed"] == a.seeds[0]:
                for rec in r.get("records", []):
                    f.write(json.dumps({"area": r["area"], "strategy": r["strategy"], "seed": r["seed"], **rec},
                                       sort_keys=True) + "\n")
    with (out / "fdd-alarms.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in runs:
            for al in r.get("alarms", []):
                f.write(json.dumps({"area": r["area"], "strategy": r["strategy"], "seed": r["seed"], **al},
                                   sort_keys=True) + "\n")
    base = a.strategies[0]
    for area, t in res["table"].items():
        y = next(iter(t["year"].values()))
        line = f"{area} {AREAS[area]:12} {base} {y[base]['total_nok']:8.0f} NOK ({y[base]['kwh']:6.0f} kWh)"
        for s in a.strategies[1:]:
            line += (f"  {s} {y[f'{s}_saving_pct']:5.1f} % (comfort {y[s]['comfort_dh']:.0f} dh, "
                     f"hazard {y[s]['hazard_h']:.0f} h)")
        print(line + f"  [{base} comfort {y[base]['comfort_dh']:.0f} dh, hazard {y[base]['hazard_h']:.0f} h]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
