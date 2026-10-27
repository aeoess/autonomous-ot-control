"""Seasonal comparison over a year of verified data: P0 against P1, per month to year.

Offline, on the building-services twin (`twin/fixtures/three-zone-hvac-seasonal.json`)
and the verified 2025 Trondheim year (`scenarios/trondheim-2025-hvac-v1.json`):
real NO3 day-ahead prices and ERA5 weather, Norwegian holidays, seeded presence
and equipment variation, and the summer stop.

Both strategies run over the full year, once per seed. Each records per day:
- energy by consumer, from the plant's meters;
- spot cost;
- comfort outside the band while occupied;
- heating-season hours;
- setpoint changes.

The days are then aggregated into months, quarters, halves and the year.
Savings are paired per seed: each seed sees the same weather, prices, occupancy
and load for both strategies.

P1 here is applied directly, as arm A for the same policy. The governed demo
(`runner.demo`) shows that with REMORA in the loop the same approved changes
give byte-identical energy, so a governed year would differ only by changes
REMORA refuses. The figures describe a synthetic, uncalibrated building on
spot cost, with no grid tariff or VAT. They are not a real-building saving.

    python -m runner.seasonal --seeds 177 178 179 --out artifacts/seasonal-2025
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from runner.baseline import target_setpoint
from runner.demo import STEP_S, ZONES, ReheatLearner, next_step, p1_target
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "twin" / "fixtures" / "three-zone-hvac-seasonal.json"
SCENARIO = ROOT / "scenarios" / "trondheim-2025-hvac-v1.json"
METERS = {
    "heat_pump": ("HP-01.ElectricPower",),
    "backup_heater": ("HP-01.BackupHeaterPower",),
    "fans": ("AHU-01.FanPower",),
    "cooling": ("CH-01.ElectricPower",),
    "heating_cables": ("CABLE-RAMP.Power", "CABLE-GUTTER.Power"),
    "lighting": tuple(f"{z}.LightingPower" for z in ZONES),
    "equipment": tuple(f"{z}.EquipmentPower" for z in ZONES),
}


def run_year(strategy: str, seed: int, days: int = 365) -> list[dict[str, Any]]:
    raw = json.loads(SCENARIO.read_text("utf-8"))
    raw["seed"] = seed
    raw["duration_seconds"] = days * 86400
    sc = load_scenario(raw, base_dir=SCENARIO.parent)
    tw = Twin(load_config(CONFIG), sc)
    start = datetime.fromisoformat(sc.start_utc.replace("Z", "+00:00"))
    learner = ReheatLearner()
    lo, hi = sc.comfort_band()
    rows: list[dict[str, Any]] = []
    day = _new_day(start)
    for t in range(0, days * 86400, STEP_S):
        if t and t % 86400 == 0:
            rows.append(day)
            day = _new_day(start + timedelta(seconds=t))
        for zone in ZONES:
            z = tw.zones[zone]
            if strategy == "P0":
                tgt = target_setpoint(tw, t)
                value = tgt if abs(z.setpoint_c - tgt) > 1e-9 else None
            else:
                tgt, why = p1_target(sc, t, zone, z.measured_temp_c, learner)
                learner.observe(zone, t, z.measured_temp_c, why.startswith("optimal start"))
                value = next_step(z.setpoint_c, tgt)
            if value is not None:
                rec, _ = tw.apply_setpoint({"operation_id": f"{strategy}-{t}-{zone}", "zone_id": zone,
                                            "setpoint_c": value, "units": "degC",
                                            "expected_state_revision": tw.revision})
                day["changes"] += rec.status == "APPLIED"
                day["refused"] += rec.status != "APPLIED"
        cost0, kwh0 = tw.cost, tw.energy_kwh
        for _ in range(STEP_S // tw.step_s):
            occupied = sc.occupied(tw.clock.now_s)
            if occupied:
                for zz in tw.zones.values():
                    day["comfort_dh"] += (max(lo - zz.temp_c, 0) + max(zz.temp_c - hi, 0)) * tw.step_s / 3600
            tw._step()
            p = tw.points
            for k, ids in METERS.items():
                day[k] += sum(p.get(i, 0.0) for i in ids) * tw.step_s / 3.6e6
            day["heating_season_h"] += p.get("HYD-01.HeatingSeason", 0.0) * tw.step_s / 3600
        day["kwh"] += tw.energy_kwh - kwh0
        day["cost_nok"] += tw.cost - cost0
    rows.append(day)
    return rows


def _new_day(d: datetime) -> dict[str, Any]:
    return {"date": d.astimezone(UTC).date().isoformat(), "kwh": 0.0, "cost_nok": 0.0,
            "comfort_dh": 0.0, "heating_season_h": 0.0, "changes": 0, "refused": 0,
            **{k: 0.0 for k in METERS}}


def periods(date: str) -> dict[str, str]:
    y, m, _ = date.split("-")
    q = (int(m) - 1) // 3 + 1
    return {"month": f"{y}-{m}", "quarter": f"{y}-Q{q}", "half": f"{y}-H{1 if int(m) <= 6 else 2}",
            "year": y}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {k: defaultdict(lambda: defaultdict(float))
                                                   for k in ("month", "quarter", "half", "year")}
    for r in rows:
        for level, key in periods(r["date"]).items():
            for f, v in r.items():
                if f != "date":
                    out[level][key][f] += v
    return {lvl: {k: dict(v) for k, v in d.items()} for lvl, d in out.items()}


def compare(seeds: list[int], days: int) -> dict[str, Any]:
    per_seed = {}
    for seed in seeds:
        p0, p1 = aggregate(run_year("P0", seed, days)), aggregate(run_year("P1", seed, days))
        per_seed[seed] = {"P0": p0, "P1": p1}
    table: dict[str, dict[str, Any]] = {}
    for level in ("month", "quarter", "half", "year"):
        table[level] = {}
        for key in per_seed[seeds[0]]["P0"][level]:
            sav = [per_seed[s]["P0"][level][key]["cost_nok"] - per_seed[s]["P1"][level][key]["cost_nok"]
                   for s in seeds]
            kwh = [per_seed[s]["P0"][level][key]["kwh"] - per_seed[s]["P1"][level][key]["kwh"] for s in seeds]
            dc = [per_seed[s]["P1"][level][key]["comfort_dh"] - per_seed[s]["P0"][level][key]["comfort_dh"]
                  for s in seeds]
            base = [per_seed[s]["P0"][level][key] for s in seeds]
            table[level][key] = {
                "p0_kwh": statistics.mean(b["kwh"] for b in base),
                "p0_cost_nok": statistics.mean(b["cost_nok"] for b in base),
                "p1_cost_nok": statistics.mean(per_seed[s]["P1"][level][key]["cost_nok"] for s in seeds),
                "savings_nok_mean": statistics.mean(sav), "savings_nok_min": min(sav),
                "savings_nok_max": max(sav),
                "savings_kwh_mean": statistics.mean(kwh),
                "savings_pct": 100 * statistics.mean(sav) / statistics.mean(b["cost_nok"] for b in base),
                "comfort_delta_dh_mean": statistics.mean(dc),
                "heating_season_h": statistics.mean(b["heating_season_h"] for b in base),
                "p0_breakdown_kwh": {k: statistics.mean(b[k] for b in base) for k in METERS},
                "mean_price_nok_per_kwh": statistics.mean(b["cost_nok"] for b in base)
                / max(statistics.mean(b["kwh"] for b in base), 1e-9),
            }
    return {"seeds": seeds, "days": days, "table": table,
            "scope": ("Synthetic, uncalibrated three-zone building-services twin; verified NO3 day-ahead "
                      "spot prices and ERA5 weather for 2025 at a fictitious Trondheim site; spot cost only "
                      "(no grid tariff, VAT or markup); P1 applied directly (arm A of the same policy). "
                      "Not a real-building saving.")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.seasonal")
    p.add_argument("--seeds", type=int, nargs="+", default=[177, 178, 179])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    res = compare(a.seeds, a.days)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "seasonal.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    y = res["table"]["year"]
    for key, v in y.items():
        print(f"{key}: P0 {v['p0_cost_nok']:.0f} NOK, P1 saves {v['savings_nok_mean']:.0f} NOK "
              f"({v['savings_pct']:.1f} %), range {v['savings_nok_min']:.0f}..{v['savings_nok_max']:.0f}, "
              f"comfort {v['comfort_delta_dh_mean']:+.2f} dh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
