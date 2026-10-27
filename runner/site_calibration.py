"""Calibration targets from one anonymised building's hourly meters (schema ot hourly meter v2).

    python -m runner.site_calibration --data <extracted dir> --weather scenarios/data/<site>/ --out <dir>

Input is the anonymised export (`manifest.json`, `meters/<id>.json`,
`data_quality_audit.json`): one site, identified only as its `site_id`. No
address, owner or meter-point identifier is read, written or needed, and the
raw data stays outside the repository; only this code and, if the owner agrees,
aggregate targets are committed.

What it does, in order:

1. **Quality gate.** Rows with any quality flag are excluded, as are the audit's
   named windows: the simultaneous doubling (DQ03), the cooling catch-up spike
   (DQ02, and any row above 20 times the meter's own P99), and the partial last
   hour (DQ06). Absent rows stay absent: an unobserved hour is never zero.
   Joins to weather are by absolute UTC hour (DQ05).
2. **Targets**, each with its coverage, so a reader can see what it rests on:
   - electricity load shape: hourly median kW by weekday and weekend for the
     top-level meters, and the night base load (00-05 local);
   - heating energy signature: daily thermal heating kWh against daily mean
     outdoor temperature, fitted as max(0, a·(Tb − T)) with the balance
     temperature Tb found by grid search; a in kWh/day per K, also as W/K;
   - cooling signature, the same above a balance temperature;
   - heating-cable energy against hours in the icing band in its own window.
3. A report and a targets file. The twin's own signature for a candidate fixture
   is computed the same way (`twin_signature`) so the two can be compared.

What it does not do (the audit's `not_yet_supported`, kept): causal savings,
COP, thermal parameter estimation beyond the steady-state signature, cost
models. The meter hierarchy is unknown (DQ08): totals across meters may double
count, so every target names the meters it sums.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
#: Audit windows excluded from fitting (UTC, inclusive start, exclusive end).
DQ03_WINDOW = (datetime(2026, 3, 9, 12, tzinfo=UTC), datetime(2026, 3, 13, 10, tzinfo=UTC))
SPIKE_FACTOR = 20.0
#: Meters assumed top level for the load shape, stated (DQ08: hierarchy unknown).
TOP_LEVEL_ELECTRICITY = ("E01", "E02")
HEATING_METERS = ("H01", "H02")
COOLING_METERS = ("C01", "C02")
CABLE_METERS = ("E16", "E17")
ICING_BAND_C = (-10.0, 1.0)


def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(UTC)


def load_meters(data: Path) -> dict[str, dict[str, Any]]:
    manifest = json.loads((data / "manifest.json").read_text("utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for m in manifest["meters"]:
        d = json.loads((data / "meters" / f"{m['meter_id']}.json").read_text("utf-8"))
        cols = d["hourly_columns"]
        rows = [dict(zip(cols, r)) for r in d["readings"]]
        out[m["meter_id"]] = {"category": d["energy_category"], "role": m.get("meter_role_sanitized"), "rows": rows}
    return out


def gate(meters: dict[str, dict[str, Any]], last_hour_utc: datetime | None = None) -> dict[str, Any]:
    """Return {meter_id: {utc_hour: kWh}} for rows that pass, and the counts of what was excluded and why."""
    clean: dict[str, dict[datetime, float]] = {}
    excluded: dict[str, dict[str, int]] = {}
    for mid, m in meters.items():
        vals = sorted(r["energy_kwh"] for r in m["rows"] if not r["quality_flags"])
        p99 = vals[int(0.99 * (len(vals) - 1))] if vals else 0.0
        keep: dict[datetime, float] = {}
        ex = {"flagged": 0, "dq03_window": 0, "spike": 0, "partial_last_hour": 0}
        for r in m["rows"]:
            t = _utc(r["time_start_local_with_utc_offset"])
            if r["quality_flags"]:
                ex["flagged"] += 1
                continue
            if DQ03_WINDOW[0] <= t < DQ03_WINDOW[1]:
                ex["dq03_window"] += 1
                continue
            if p99 > 0 and r["energy_kwh"] > SPIKE_FACTOR * p99:
                ex["spike"] += 1
                continue
            if last_hour_utc is not None and t >= last_hour_utc:
                ex["partial_last_hour"] += 1
                continue
            keep[t] = float(r["energy_kwh"])
        clean[mid] = keep
        excluded[mid] = ex
    return {"series": clean, "excluded": excluded}


def load_weather(path: Path) -> dict[datetime, float]:
    s = json.loads(path.read_text("utf-8"))
    t0 = datetime.fromisoformat(s["start_utc"].replace("Z", "+00:00"))
    return {t0 + timedelta(seconds=i * s["step_seconds"]): float(v) for i, v in enumerate(s["values"]) if v is not None}


def _local(t: datetime) -> datetime:
    """Europe/Oslo local time without tz data: +2 h in the EU summer-time window, +1 h otherwise."""
    y = t.year
    last_sun = lambda month: max(datetime(y, month, d, 1, tzinfo=UTC) for d in range(25, 32)  # noqa: E731
                                 if datetime(y, month, d).weekday() == 6)
    return t + timedelta(hours=2 if last_sun(3) <= t < last_sun(10) else 1)


def load_shape(series: dict[str, dict[datetime, float]], meters: tuple[str, ...]) -> dict[str, Any]:
    """Hourly median kW (kWh in one hour) by weekday/weekend for the sum of meters, on hours all of them observed."""
    common = set.intersection(*(set(series[m]) for m in meters if m in series)) if meters else set()
    by: dict[tuple[str, int], list[float]] = {}
    for t in common:
        lt = _local(t)
        kind = "weekend" if lt.weekday() >= 5 else "weekday"
        by.setdefault((kind, lt.hour), []).append(sum(series[m][t] for m in meters))
    shape = {k: [round(st.median(by.get((k, h), [0.0])), 2) for h in range(24)] for k in ("weekday", "weekend")}
    night = [v for (k, h), vs in by.items() if k == "weekday" and h < 5 for v in vs]
    day = [v for (k, h), vs in by.items() if k == "weekday" and 8 <= h < 16 for v in vs]
    return {"meters": list(meters), "hours_used": len(common), "median_kw_by_hour": shape,
            "night_base_kw": round(st.median(night), 2) if night else None,
            "weekday_day_kw": round(st.median(day), 2) if day else None,
            "night_to_day_ratio": round(st.median(night) / st.median(day), 3) if night and day else None,
            "weekend_to_weekday_ratio": round(sum(shape["weekend"]) / sum(shape["weekday"]), 3) if sum(shape["weekday"]) else None}


def daily(series: dict[str, dict[datetime, float]], meters: tuple[str, ...], weather: dict[datetime, float],
          min_hours: int = 22) -> list[tuple[str, float, float]]:
    """(local date, kWh summed over meters, mean outdoor °C) for days with at least min_hours on every meter."""
    days: dict[str, dict[str, list[float]]] = {}
    temps: dict[str, list[float]] = {}
    for m in meters:
        for t, v in series.get(m, {}).items():
            d = _local(t).date().isoformat()
            days.setdefault(d, {}).setdefault(m, []).append(v)
    for t, v in weather.items():
        temps.setdefault(_local(t).date().isoformat(), []).append(v)
    out = []
    for d, per in sorted(days.items()):
        if all(len(per.get(m, [])) >= min_hours for m in meters) and len(temps.get(d, [])) >= 20:
            # Scale to 24 h where a few hours are missing; never fill a missing meter.
            kwh = sum(sum(v) * 24.0 / len(v) for v in per.values())
            out.append((d, kwh, st.fmean(temps[d])))
    return out


def fit_signature(points: list[tuple[str, float, float]], *, heating: bool = True) -> dict[str, Any] | None:
    """Fit kWh/day = b + a·max(0, Tb − T) (heating) or b + a·max(0, T − Tb) (cooling).

    The constant b is the weather-independent part (domestic hot water, process cooling, losses):
    without it a base load is forced into the slope and the balance temperature drifts up. Grid
    search on Tb inside the observed range, ordinary least squares on (b, a); b and a must be >= 0."""
    if len(points) < 10:
        return None
    ts = [t for _, _, t in points]
    ys = [k for _, k, _ in points]
    best = None
    for tb10 in range(int(min(ts) * 10) + 10, int(max(ts) * 10) - 9, 5):
        tb = tb10 / 10.0
        xs = [max(0.0, (tb - t) if heating else (t - tb)) for t in ts]
        n, sx, sy = len(xs), sum(xs), sum(ys)
        sxx, sxy = sum(x * x for x in xs), sum(x * y for x, y in zip(xs, ys))
        den = n * sxx - sx * sx
        if den <= 0:
            continue
        a = (n * sxy - sx * sy) / den
        b = (sy - a * sx) / n
        if a < 0 or b < 0:
            continue
        res = sum((y - b - a * x) ** 2 for x, y in zip(xs, ys))
        if best is None or res < best[3]:
            best = (tb, a, b, res)
    if best is None:
        return None
    tb, a, b, res = best
    mean = st.fmean(ys)
    tot = sum((y - mean) ** 2 for y in ys)
    below = sum(1 for x in ts if (x < tb) == heating)
    # Identified only if the kink beats a straight line through all days (no balance point in range)
    # by a clear margin, and has days on both sides; otherwise b and Tb trade off and only the slope holds.
    xl = [-x if heating else x for x in ts]
    n, sx, sy = len(xl), sum(xl), sum(ys)
    sxx, sxy = sum(x * x for x in xl), sum(x * y for x, y in zip(xl, ys))
    al = (n * sxy - sx * sy) / (n * sxx - sx * sx) if n * sxx - sx * sx > 0 else 0.0
    bl = (sy - al * sx) / n
    res_line = sum((y - bl - al * x) ** 2 for x, y in zip(xl, ys))
    identified = min(below, len(ts) - below) >= 5 and res < 0.9 * res_line
    return {"days": len(points), "days_on_weather_side": below, "days_on_base_side": len(ts) - below,
            "balance_identified": identified,
            "balance_temp_c": tb, "kwh_per_day_per_k": round(a, 2),
            "w_per_k": round(a * 1000.0 / 24.0, 1), "base_kwh_per_day": round(b, 1),
            "r2": round(1 - res / tot, 3) if tot else None,
            "outdoor_range_c": [round(min(ts), 1), round(max(ts), 1)]}


def cable_use(series: dict[str, dict[datetime, float]], weather: dict[datetime, float]) -> dict[str, Any]:
    out = {}
    for m in CABLE_METERS:
        s = series.get(m) or {}
        if not s:
            continue
        lo, hi = min(s), max(s)
        icing_h = sum(1 for t, v in weather.items() if lo <= t <= hi and ICING_BAND_C[0] <= v <= ICING_BAND_C[1])
        out[m] = {"window_utc": [lo.isoformat(), hi.isoformat()], "observed_hours": len(s),
                  "kwh": round(sum(s.values()), 1), "hours_in_icing_band_in_window": icing_h,
                  "max_hour_kwh": round(max(s.values()), 3)}
    return out


def targets(data: Path, weather_path: Path) -> dict[str, Any]:
    manifest = json.loads((data / "manifest.json").read_text("utf-8"))
    last = datetime.fromisoformat(manifest["period"]["last_hour_utc"])
    g = gate(load_meters(data), last_hour_utc=last)
    w = load_weather(weather_path)
    s = g["series"]
    heat = daily(s, HEATING_METERS, w)
    cool = daily(s, COOLING_METERS, w)
    return {
        "schema": "ot-site-calibration-targets/v1",
        "site_id": manifest.get("site_id"), "source_sha256": manifest.get("source_sha256"),
        "period_utc": [manifest["period"]["first_hour_utc"], manifest["period"]["last_hour_utc"]],
        "weather": weather_path.name,
        "excluded_rows": g["excluded"],
        "electricity_load_shape": load_shape(s, TOP_LEVEL_ELECTRICITY),
        "electricity_technical_shape": load_shape(s, ("E02",)),
        "heating_signature": fit_signature(heat, heating=True),
        "cooling_signature": fit_signature(cool, heating=False),
        "heating_cables": cable_use(s, w),
        "assumptions": [
            "E01 and E02 are taken as the top-level electricity meters; the hierarchy is unknown (DQ08)",
            "thermal heating = H01 + H02, thermal cooling = C01 + C02; these are thermal kWh, not grid electricity",
            "days need at least 22 observed hours on every summed meter; a few missing hours are scaled to 24, "
            "a missing meter is never filled",
            "local time is Europe/Oslo by the EU summer-time rule; joins are by UTC hour",
        ],
        "not_established": ["causal savings", "COP", "floor area, so no kWh/m2", "meter topology",
                            "dynamic thermal parameters beyond the steady-state signature"],
    }


def twin_targets(fixture: Path, area: str = "NO1", days: int = 365, strategy: str = "P0",
                 scenario: Path | None = None) -> dict[str, Any]:
    """The same targets, computed on a year of the twin under a strategy (P0: as found).

    Thermal heating is read cleanly only from a district-heated fixture (heat bought x substation
    efficiency = heat delivered); for another heat source it is reported as not comparable. The
    electricity shape is the twin's whole electricity (equipment, lighting, fans, cooling, and the
    heat source when it is electric), so compare its ratios, not its level, unless the sources match."""
    from runner.areas import scenario_path
    from runner.demo import STEP_S
    from runner.strategies import Controller
    from twin.model import Twin, load_config
    from twin.scenarios import load_scenario

    cfg = load_config(fixture)
    sp = scenario or scenario_path(area)
    raw = json.loads(sp.read_text("utf-8"))
    raw["duration_seconds"] = min(days * 86400, int(raw["duration_seconds"]))
    days = raw["duration_seconds"] // 86400
    sc = load_scenario(raw, base_dir=sp.parent)
    tw = Twin(cfg, sc)
    ctl = Controller(strategy, tw)
    start = datetime.fromisoformat(sc.start_utc.replace("Z", "+00:00"))
    eff = float(cfg["plant"].get("dh_substation_efficiency", 1.0))
    dh_ok = cfg["plant"].get("heat_source") == "district_heating"
    elec: dict[str, dict[datetime, float]] = {"TWIN": {}}
    heat: dict[str, dict[datetime, float]] = {"TWIN_HEAT": {}}
    weather: dict[datetime, float] = {}
    e0, d0 = tw.electric_kwh, tw.district_heat_kwh
    for t in range(0, days * 86400, STEP_S):
        ctl.step(t)
        tw.advance(STEP_S)
        if (t + STEP_S) % 3600 == 0:
            hour = start + timedelta(seconds=t + STEP_S - 3600)
            elec["TWIN"][hour] = tw.electric_kwh - e0
            heat["TWIN_HEAT"][hour] = (tw.district_heat_kwh - d0) * eff
            weather[hour] = sc.outdoor_c(t + STEP_S - 1800)
            e0, d0 = tw.electric_kwh, tw.district_heat_kwh
    out = {"fixture": fixture.name, "area": area, "scenario": sp.name, "strategy": strategy, "days": days,
           "hourly_electricity_kwh": {h.isoformat(): round(v, 4) for h, v in elec["TWIN"].items()},
           "bra_m2": sum(z["area_m2"] for z in cfg["zones"]),
           "electricity_load_shape": load_shape(elec, ("TWIN",)),
           "heating_signature": fit_signature(daily(heat, ("TWIN_HEAT",), weather), heating=True) if dh_ok else None,
           "heating_comparable": dh_ok}
    return out


def site_hourly(data: Path, meters: tuple[str, ...] = TOP_LEVEL_ELECTRICITY) -> dict[datetime, float]:
    """The gated hourly sum over meters, on hours every meter observed (never filled)."""
    manifest = json.loads((data / "manifest.json").read_text("utf-8"))
    g = gate(load_meters(data), last_hour_utc=datetime.fromisoformat(manifest["period"]["last_hour_utc"]))
    s = g["series"]
    common = set.intersection(*(set(s[m]) for m in meters))
    return {t: sum(s[m][t] for m in meters) for t in sorted(common)}


def validate_hourly(measured: dict[datetime, float], simulated: dict[datetime, float]) -> dict[str, Any]:
    """ASHRAE Guideline 14 style statistics on the hours both have: CV(RMSE) and NMBE, in %.
    Guideline 14 calls an hourly model calibrated at CV(RMSE) <= 30 % and |NMBE| <= 10 %. Here the
    model was fitted to aggregates of the same period, so this is an in-sample check, not a hold-out."""
    hours = sorted(set(measured) & set(simulated))
    if len(hours) < 48:
        return {"hours": len(hours), "cv_rmse_pct": None, "nmbe_pct": None}
    m = [measured[h] for h in hours]
    s_ = [simulated[h] for h in hours]
    mean = st.fmean(m)
    n = len(hours)
    rmse = (sum((a - b) ** 2 for a, b in zip(m, s_)) / (n - 1)) ** 0.5
    nmbe = sum(a - b for a, b in zip(m, s_)) / ((n - 1) * mean)
    cv = 100 * rmse / mean
    # Monthly: sums over the hours both have in each calendar month (Guideline 14: CV <= 15 %, |NMBE| <= 5 %).
    mm: dict[str, list[float]] = {}
    for h, a, b in zip(hours, m, s_):
        k = h.strftime("%Y-%m")
        mm.setdefault(k, [0.0, 0.0])
        mm[k][0] += a
        mm[k][1] += b
    months = sorted(mm)
    mon = {}
    if len(months) >= 3:
        ma, mb = [mm[k][0] for k in months], [mm[k][1] for k in months]
        mmean, nm = st.fmean(ma), len(months)
        mcv = 100 * (sum((a - b) ** 2 for a, b in zip(ma, mb)) / (nm - 1)) ** 0.5 / mmean
        mnmbe = 100 * sum(a - b for a, b in zip(ma, mb)) / ((nm - 1) * mmean)
        mon = {"months": nm, "monthly_cv_rmse_pct": round(mcv, 1), "monthly_nmbe_pct": round(mnmbe, 1),
               "guideline14_monthly": mcv <= 15.0 and abs(mnmbe) <= 5.0,
               "by_month_kwh": {k: [round(mm[k][0], 0), round(mm[k][1], 0)] for k in months}}
    return {"hours": n, "cv_rmse_pct": round(cv, 1), "nmbe_pct": round(100 * nmbe, 1),
            "guideline14_hourly": cv <= 30.0 and abs(100 * nmbe) <= 10.0,
            "measured_kwh": round(sum(m), 1), "simulated_kwh": round(sum(s_), 1), **mon}


def render_md(t: dict[str, Any]) -> str:
    ls, hs, cs = t["electricity_load_shape"], t["heating_signature"], t["cooling_signature"]
    L = [f"# Calibration targets, {t['site_id']}", "",
         f"Period {t['period_utc'][0][:10]} to {t['period_utc'][1][:10]}, weather `{t['weather']}`. Aggregates only; "
         "the raw meters stay outside the repository.", "",
         "## Electricity load shape (E01 + E02, assumed top level)", "",
         f"Night base load {ls['night_base_kw']} kW, weekday daytime {ls['weekday_day_kw']} kW, night/day "
         f"{ls['night_to_day_ratio']}, weekend/weekday {ls['weekend_to_weekday_ratio']} ({ls['hours_used']} hours).", "",
         "| Hour | " + " | ".join(str(h) for h in range(0, 24, 2)) + " |", "|---|" + "---:|" * 12,
         "| weekday kW | " + " | ".join(f"{ls['median_kw_by_hour']['weekday'][h]:.0f}" for h in range(0, 24, 2)) + " |",
         "| weekend kW | " + " | ".join(f"{ls['median_kw_by_hour']['weekend'][h]:.0f}" for h in range(0, 24, 2)) + " |", ""]
    for name, sig in (("Heating", hs), ("Cooling", cs)):
        if sig:
            note = ("" if sig["balance_identified"] else
                    f" **Balance temperature and base not identified** ({sig['days_on_base_side']} days on the base side): "
                    "only the slope is a target.")
            L += [f"## {name} energy signature (thermal)", "",
                  f"{sig['base_kwh_per_day']} kWh/day weather-independent plus {sig['kwh_per_day_per_k']} kWh/day per K ({sig['w_per_k']} W/K) beyond a balance temperature of "
                  f"{sig['balance_temp_c']} °C, R² {sig['r2']}, {sig['days']} days, outdoor {sig['outdoor_range_c']} °C." + note, ""]
        else:
            L += [f"## {name} energy signature", "", "Not enough complete days.", ""]
    L += ["## Heating cables", ""]
    for m, c in t["heating_cables"].items():
        L.append(f"- {m}: {c['kwh']} kWh over {c['observed_hours']} observed hours "
                 f"({c['window_utc'][0][:10]} to {c['window_utc'][1][:10]}), {c['hours_in_icing_band_in_window']} hours in the icing band.")
    L += ["", "## Excluded by the quality gate", "", "| Meter | flagged | DQ03 window | spike | partial last hour |", "|---|---:|---:|---:|---:|"]
    L += [f"| {m} | {e['flagged']} | {e['dq03_window']} | {e['spike']} | {e['partial_last_hour']} |" for m, e in t["excluded_rows"].items()]
    L += ["", "Assumptions:", ""] + [f"- {a}" for a in t["assumptions"]] + ["", "Not established: " + ", ".join(t["not_established"]) + "."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.site_calibration")
    p.add_argument("--data", required=True, help="the extracted anonymised export (manifest.json, meters/)")
    p.add_argument("--weather", required=True, help="an ot-exogenous-snapshot outdoor file covering the period")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    t = targets(Path(a.data), Path(a.weather))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "targets.json").write_text(json.dumps(t, indent=2) + "\n", "utf-8")
    (out / "targets.md").write_text(render_md(t), "utf-8")
    print(render_md(t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
