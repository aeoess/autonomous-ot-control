"""What-if: how much each building-services lever could save in the simulator (2025).

    python -m runner.levers --seed 177 --out artifacts/levers-2025

Offline, ungoverned, one seed. Each lever is applied directly to the plant's
supervisory setpoints, on top of P1 (setback with optimal start). It answers
"is the saving there to be had?", not "REMORA achieved it": the tools these
levers need are SPECIFIED in docs/datapoints.md and are not yet governed.
Every lever reports its cost and comfort effect, and the cable lever also
reports the hazard it adds (icing-risk hours with the circuit off).

| Lever | What changes |
|---|---|
| ahu_night_stop | AHU off outside occupancy (on 1 h before), AHU-01.Enabled (PR-02) |
| deep_setback | 16 degC on weekends and holidays instead of 19 (needs the D-15 bounds) |
| cable_moisture | cables on only while it precipitates and the hour after, -6..+1 degC (a ground-moisture controller) instead of the 3 h window either side |
| tank_price_shift | tank to 55 degC in the cheapest 6 hours of each day, 40 degC in the dearest 6 |
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from runner.demo import STEP_S, ZONES, ReheatLearner, next_step, p1_target
from runner.seasonal import CONFIG, METERS, SCENARIO
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

LEVERS = ("ahu_night_stop", "deep_setback", "cable_moisture", "tank_price_shift")
DEEP_C = 16.0


def run(levers: tuple[str, ...], seed: int, days: int) -> dict[str, Any]:
    raw = json.loads(SCENARIO.read_text("utf-8"))
    raw["seed"], raw["duration_seconds"] = seed, days * 86400
    sc = load_scenario(raw, base_dir=SCENARIO.parent)
    cfg = load_config(CONFIG)
    if "deep_setback" in levers:
        for z in cfg["zones"]:
            z["min_setpoint_c"] = 8.0
    tw = Twin(cfg, sc)
    learner = ReheatLearner()
    lo, hi = sc.comfort_band()
    acc = {k: 0.0 for k in METERS} | {"comfort_dh": 0.0, "hazard_h": 0.0}
    hours = {}
    if "tank_price_shift" in levers:
        for d in range(days):
            prices = sorted((sc.price_per_kwh(d * 86400 + h * 3600), h) for h in range(24))
            hours[d] = ({h for _, h in prices[:6]}, {h for _, h in prices[-6:]})
    for t in range(0, days * 86400, STEP_S):
        day, hour = divmod(t // 3600, 24)
        weekday_open = any(sc.occupied(day * 86400 + h * 3600) for h in range(24))
        for zone in ZONES:
            z = tw.zones[zone]
            tgt, why = p1_target(sc, t, zone, z.measured_temp_c, learner)
            learner.observe(zone, t, z.measured_temp_c, why.startswith("optimal start"))
            if "deep_setback" in levers and zone in ("ZONE-01", "ZONE-02") and tgt < 21.0 \
                    and not weekday_open and not learner.preheating(zone):
                start = next((u for u in range(t, t + 3 * 86400, STEP_S) if sc.occupied(u)), None)
                if start is None or start - t > 10 * 3600:
                    tgt = DEEP_C
            v = next_step(z.setpoint_c, tgt)
            if v is not None:
                tw.apply_setpoint({"operation_id": f"l-{t}-{zone}", "zone_id": zone, "setpoint_c": v,
                                   "units": "degC", "expected_state_revision": tw.revision})
        sp = tw.setpoints
        if "ahu_night_stop" in levers:
            soon = sc.occupied(t) or sc.occupied(t + 3600)
            sp = replace(sp, ahu_enabled=soon)
        if "cable_moisture" in levers:
            wet = sc.precipitation_mm_h(t) > 0 or sc.precipitation_mm_h(max(0, t - 3600)) > 0
            mode = "auto" if (wet and -6.0 <= sc.outdoor_c(t) <= 1.0) else "off"
            sp = replace(sp, cable_modes={"CABLE-RAMP": mode, "CABLE-GUTTER": mode})
        if "tank_price_shift" in levers:
            cheap, dear = hours[day]
            sp = replace(sp, tank_c=55.0 if hour in cheap else 40.0 if hour in dear else 45.0)
        tw.setpoints = sp
        for _ in range(STEP_S // tw.step_s):
            if sc.occupied(tw.clock.now_s):
                for zz in tw.zones.values():
                    acc["comfort_dh"] += (max(lo - zz.temp_c, 0) + max(zz.temp_c - hi, 0)) * tw.step_s / 3600
            tw._step()
            p = tw.points
            for k, ids in METERS.items():
                acc[k] += sum(p.get(i, 0.0) for i in ids) * tw.step_s / 3.6e6
            acc["hazard_h"] += max(p.get("CABLE-RAMP.IcingRiskUncovered", 0), p.get("CABLE-GUTTER.IcingRiskUncovered", 0)) \
                * tw.step_s / 3600
    return {"levers": list(levers), "kwh": tw.energy_kwh, "cost_nok": tw.cost, **acc}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.levers")
    p.add_argument("--seed", type=int, default=177)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    base = run((), a.seed, a.days)
    rows = [{"case": "P1 only", **base}]
    for lever in LEVERS:
        rows.append({"case": lever, **run((lever,), a.seed, a.days)})
    rows.append({"case": "all four", **run(LEVERS, a.seed, a.days)})
    for r in rows:
        r["saved_vs_p1_nok"] = base["cost_nok"] - r["cost_nok"]
        r["saved_vs_p1_kwh"] = base["kwh"] - r["kwh"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "levers.json").write_text(json.dumps({"seed": a.seed, "days": a.days, "rows": rows}, indent=2), "utf-8")
    for r in rows:
        print(f"{r['case']:18} {r['kwh']:8.0f} kWh {r['cost_nok']:8.0f} NOK  saved {r['saved_vs_p1_nok']:7.0f} NOK "
              f"{r['saved_vs_p1_kwh']:7.0f} kWh  comfort {r['comfort_dh']:6.1f} dh  hazard {r['hazard_h']:5.1f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
