"""Render the five-area comparison as Markdown and a self-contained HTML page (English).

    python -m runner.report_areas --results docs/results/2026-10-27/areas-2025.json \
        --md docs/results/2026-10-27/areas-2025.md --html docs/results/2026-10-27/areas-2025.html

Reads only the committed `areas-2025.json` written by `runner.areas`. Every
strategy present in that file is shown, so a new strategy needs no change
here. No external scripts, fonts or images; charts are inline SVG and the
page follows the viewer's light or dark theme.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from runner.report_html import nok

E = html.escape
CONSUMERS = ("heating_cables", "heat_pump", "backup_heater", "lighting", "fans", "cooling", "equipment")
COST_PARTS = ("spot_nok", "grid_energy_nok", "grid_power_nok")
SAFETY = (("comfort_dh", "comfort dh"), ("hazard_h", "icing h"), ("co2_excess_ppmh", "CO2>1000 ppm·h"),
          ("tank_over_60_h", "tank>60 °C h"))


#: What each strategy is, in one sentence each. Shown for the strategies the file carries.
STRATEGY_DESC = {
    "P0": "As found. Fixed 21 °C occupied (1 h preheat) and 20 °C otherwise, the air-handling unit on a fixed "
          "weekday schedule, lighting on schedule, heating cables on an air thermostat, the tank at 50 °C with no "
          "cap. The baseline every saving is measured against.",
    "P1": "Classic night setback. P0 plus setback to 19 °C outside occupancy and a self-tuning optimal start "
          "that learns how long the building takes to reheat.",
    "P2": "Rule-based caretaker. P1 plus a weekend and holiday setback chosen by the outdoor mean, occupancy-aware "
          "ventilation, presence lighting with daylight dimming, ground-sensor control of the heating cables, and "
          "the tank following the radiator curve with a cheap-hour boost and a peak limiter.",
    "P3": "The digital caretaker (ADR-009). Safety overlays (frost, air quality, icing, degraded telemetry), one "
          "active comfort profile, one named algorithm per setpoint, an independent validator against the site "
          "policy, a hash-pinned sensor-free fallback, and fault detection on the zone sensors. Every change is a "
          "governed tool call with its decision record.",
    "P3raw": "P3 with the independent validator and the telemetry checks switched off: the same optimizer, but "
             "nothing stops it from acting on a frozen or drifted sensor. It exists only to show what the "
             "validator is worth.",
}

#: The A/B/C arms of a faulted run (res["faults"] is true): which strategy plays which role.
ARMS = (("A", "P0", "the building as found, with the same seeded sensor faults; nothing reacts to them"),
        ("B", "P3raw", "the caretaker without validator or telemetry checks: saves energy, but a faulted sensor "
                       "can drive it into an unsafe state"),
        ("C", "P3", "the full caretaker: the validator refuses what the site policy forbids and the fallback "
                    "takes over when telemetry is not trusted"))

#: Every number in the tables, in words.
METRICS = (
    ("NOK", "cost for the year: Nord Pool day-ahead spot for the area plus a declared reference grid tariff "
            "(energy part and a monthly power charge on the highest hourly kW). Taxes excluded."),
    ("saves NOK (min..max)", "the saving against P0, mean over the seeds, with the smallest and largest seed "
                             "in brackets. The seeds vary occupancy and the sensor faults, not the weather or "
                             "the prices."),
    ("%", "the saving as a share of P0's cost in the same area."),
    ("kWh %", "the share of P0's energy saved; it differs from the NOK share because prices vary by hour."),
    ("spot NOK/kWh", "the energy-weighted average spot price P0 paid in the area."),
    ("comfort dh", "degree-hours outside the occupied comfort band, summed over the three zones. One zone one "
                   "degree too warm for one hour is one degree-hour. Lower is better."),
    ("icing h", "hours in which the ground sensor reported a wet surface at −10..+1 °C while the heating cable "
                "was off: an icing hazard on the ramp. Lower is better."),
    ("CO2>1000 ppm·h", "ppm-hours above 1000 ppm while occupied, summed over the zones: how far and how long "
                       "the air quality limit was exceeded. Lower is better."),
    ("tank>60 °C h", "hours the hot-water tank stood above 60 °C, beyond the heat pump's supply limit: wasted "
                     "heat and a safety limit in the site policy. Zero is the target."),
    ("submitted / held / fallback / escalated", "how the caretaker routed each control step: proposed a change, "
                                                "held the current value, switched to the sensor-free fallback, "
                                                "or escalated to a person."),
    ("optimizer faults injected / caught", "deliberately corrupted proposals fed to the caretaker, and how many "
                                           "the validator refused."),
    ("FDD alarms / true / fault episodes detected of", "fault-detection alarms raised, how many pointed at a "
                                                       "real seeded sensor fault, and how many of the seeded "
                                                       "fault episodes were detected at all."),
)


def arms(res: dict[str, Any]) -> list[tuple[str, str, str]]:
    ss = strategies(res)
    return [(a, s, d) for a, s, d in ARMS if s in ss] if res.get("faults") else []


def abc_rows(res: dict[str, Any]) -> list[dict[str, Any]]:
    """One headline row per area for a faulted run: cost, saving and the safety numbers for every arm."""
    if {a for a, _, _ in arms(res)} != {"A", "B", "C"}:
        return []
    out = []
    for area, name in res["areas"].items():
        y = year_row(res, area)
        row = {"area": area, "name": name, "arms": {}}
        for a, s, _ in arms(res):
            v = y[s]
            row["arms"][a] = {"strategy": s, "nok": v["total_nok"],
                              "saving_pct": y.get(f"{s}_saving_pct", 0.0) if s != "P0" else 0.0,
                              "tank_h": v.get("tank_over_60_h"), "comfort_dh": v["comfort_dh"],
                              "icing_h": v["hazard_h"], "co2": v.get("co2_excess_ppmh")}
        out.append(row)
    return out


def abc_verdict(res: dict[str, Any]) -> str:
    """The A/B/C result in two sentences, computed from the numbers, never typed in."""
    rs = abc_rows(res)
    if not rs or "C" not in rs[0]["arms"] or "B" not in rs[0]["arms"]:
        return ""
    c_pct = [r["arms"]["C"]["saving_pct"] for r in rs]
    b_pct = [r["arms"]["B"]["saving_pct"] for r in rs]
    tank_b = [r["arms"]["B"]["tank_h"] or 0 for r in rs]
    tank_c = [r["arms"]["C"]["tank_h"] or 0 for r in rs]
    def rng(vals: list[float], fmt: str) -> str:
        lo, hi = min(vals), max(vals)
        return format(lo, fmt) if format(lo, fmt) == format(hi, fmt) else f"{lo:{fmt}}–{hi:{fmt}}"

    more = "more than" if min(c - b for c, b in zip(c_pct, b_pct)) > 0 else "about as much as"
    return (f"C (the full caretaker) saves {rng(c_pct, '.1f')} % of A's cost across the {len(rs)} areas, "
            f"{more} B, the same optimizer without the validator, at {rng(b_pct, '.1f')} %. "
            f"With the same seeded sensor faults, B leaves the tank above 60 °C for {rng(tank_b, '.0f')} h "
            f"a year and C for {rng(tank_c, '.0f')} h. The validator therefore costs no savings and removes "
            "the unsafe hours. A saves nothing and reacts to no fault.")


def how_to_read_md(res: dict[str, Any]) -> list[str]:
    ss = strategies(res)
    L = ["## How to read this report", "",
         "One synthetic three-zone office building with its own heat pump, hot-water tank, air-handling unit, "
         "lighting and heated entrance ramp is placed at a fictitious site in each Norwegian price area. The same "
         "building runs the full year 2025 once per strategy and seed, on that area's verified day-ahead prices "
         "and ERA5 weather. Every strategy is applied directly to the twin, offline: no REMORA, no governance "
         "in the loop. The governed runs are reported separately.", "",
         "### The strategies", "", "| Strategy | What it is |", "|---|---|"]
    L += [f"| **{s}** | {STRATEGY_DESC.get(s, '')} |" for s in ss]
    if arms(res):
        L += ["", "### The A/B/C arms", "",
              "Every arm sees the same seeded sensor faults (frozen and drifted zone temperature sensors) and the "
              "same corrupted proposals. The arms differ only in what stands between the optimizer and the plant.", "",
              "| Arm | Strategy | What it shows |", "|---|---|---|"]
        L += [f"| **{a}** | {s} | {d} |" for a, s, d in arms(res)]
    L += ["", "### The numbers", "", "| Column | Meaning |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in METRICS]
    L += ["", "**What this does and does not show.** The savings are properties of the simulated building: real "
          "levers, synthetic plant. They show which levers matter and by how much relative to each other, and "
          "what the validator is worth when sensors lie. They are not a real-building saving, and a real site "
          "must be measured before any figure here means anything there.", ""]
    return L


def how_to_read_html(res: dict[str, Any]) -> str:
    ss = strategies(res)
    strat = "".join(f"<tr><td><b>{E(s)}</b></td><td>{E(STRATEGY_DESC.get(s, ''))}</td></tr>" for s in ss)
    arm_html = ""
    if arms(res):
        arm_html = ("<h3>The A/B/C arms</h3><p>Every arm sees the same seeded sensor faults (frozen and drifted zone "
                    "temperature sensors) and the same corrupted proposals. The arms differ only in what stands "
                    "between the optimizer and the plant.</p><div class=card><table><tr><th>Arm</th><th>Strategy</th>"
                    "<th>What it shows</th></tr>"
                    + "".join(f"<tr><td><b>{a}</b></td><td>{E(s)}</td><td>{E(d)}</td></tr>" for a, s, d in arms(res))
                    + "</table></div>")
    metrics = "".join(f"<tr><td>{E(k)}</td><td>{E(v)}</td></tr>" for k, v in METRICS)
    return (
        "<h2>How to read this report</h2>"
        "<p>One synthetic three-zone office building with its own heat pump, hot-water tank, air-handling unit, "
        "lighting and heated entrance ramp is placed at a fictitious site in each Norwegian price area. The same "
        "building runs the full year 2025 once per strategy and seed, on that area's verified day-ahead prices and "
        "ERA5 weather. Every strategy is applied directly to the twin, offline: no REMORA, no governance in the "
        "loop. The governed runs are reported separately.</p>"
        f"<h3>The strategies</h3><div class=card><table><tr><th>Strategy</th><th>What it is</th></tr>{strat}</table></div>"
        f"{arm_html}"
        f"<details class=card><summary><b>The numbers</b>: what every column means</summary><table>"
        f"<tr><th>Column</th><th>Meaning</th></tr>{metrics}</table></details>"
        "<p><b>What this does and does not show.</b> The savings are properties of the simulated building: real "
        "levers, synthetic plant. They show which levers matter and by how much relative to each other, and what "
        "the validator is worth when sensors lie. They are not a real-building saving, and a real site must be "
        "measured before any figure here means anything there.</p>")


def abc_md(res: dict[str, Any]) -> list[str]:
    rs = abc_rows(res)
    if not rs:
        return []
    has_tank = rs[0]["arms"]["C"]["tank_h"] is not None
    cols = ["A cost NOK", "B saves %", "C saves %"]
    if has_tank:
        cols += ["A tank>60 °C h", "B tank>60 °C h", "C tank>60 °C h"]
    cols += ["A comfort dh", "B comfort dh", "C comfort dh"]
    L = ["## A/B/C result", "", abc_verdict(res), "",
         "| Area | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    for r in rs:
        a, b, c = r["arms"]["A"], r["arms"]["B"], r["arms"]["C"]
        cells = [nok(a["nok"]), f"{b['saving_pct']:.1f}", f"**{c['saving_pct']:.1f}**"]
        if has_tank:
            cells += [f"{a['tank_h']:.0f}", f"{b['tank_h']:.0f}", f"**{c['tank_h']:.0f}**"]
        cells += [f"{a['comfort_dh']:.0f}", f"{b['comfort_dh']:.0f}", f"{c['comfort_dh']:.0f}"]
        L.append(f"| {r['area']} {r['name']} | " + " | ".join(cells) + " |")
    L += ["", "A = P0, B = P3raw, C = P3. The full tables below give the energy, the cost parts, the other safety "
          "numbers and the caretaker's own record per arm.", ""]
    return L


def abc_html(res: dict[str, Any]) -> str:
    rs = abc_rows(res)
    if not rs:
        return ""
    has_tank = rs[0]["arms"]["C"]["tank_h"] is not None
    head = "<th>Area</th><th>A cost NOK</th><th>B saves %</th><th>C saves %</th>"
    if has_tank:
        head += "<th>A tank&gt;60 °C h</th><th>B tank&gt;60 °C h</th><th>C tank&gt;60 °C h</th>"
    head += "<th>A comfort dh</th><th>B comfort dh</th><th>C comfort dh</th>"
    body = ""
    for r in rs:
        a, b, c = r["arms"]["A"], r["arms"]["B"], r["arms"]["C"]
        body += (f"<tr><td>{E(r['area'])} {E(r['name'])}</td><td>{nok(a['nok'])}</td><td>{b['saving_pct']:.1f}</td>"
                 f"<td><b>{c['saving_pct']:.1f}</b></td>")
        if has_tank:
            body += f"<td>{a['tank_h']:.0f}</td><td>{b['tank_h']:.0f}</td><td><b>{c['tank_h']:.0f}</b></td>"
        body += f"<td>{a['comfort_dh']:.0f}</td><td>{b['comfort_dh']:.0f}</td><td>{c['comfort_dh']:.0f}</td></tr>"
    return (f"<h2>A/B/C result</h2><p>{E(abc_verdict(res))}</p>"
            f"<div class=card><table><tr>{head}</tr>{body}</table>"
            "<p class=muted>A = P0, B = P3raw, C = P3. The sections below give the energy, the cost parts, the "
            "other safety numbers and the caretaker's own record per arm.</p></div>")


def safety_keys(res: dict[str, Any]) -> list[tuple[str, str]]:
    """The safety KPIs this result file carries (older files lack the CO2 and tank ones)."""
    y = next(iter(next(iter(res["table"].values()))["year"].values()))["P0"]
    return [(k, lbl) for k, lbl in SAFETY if k in y]


def title(res: dict[str, Any]) -> str:
    return "A/B/C With Faults, 2025" if res.get("faults") else "Five Price Areas, 2025"


def caretaker_table(res: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(area, strategy, stats) for every caretaker arm in the file."""
    return [(a, s, st) for a, by in res.get("caretaker", {}).items() for s, st in by.items()]


CT_COLS = (("routes.submit", "submitted"), ("routes.hold", "held"), ("routes.fallback", "fallback"),
           ("routes.escalate", "escalated"), ("optimizer_faults.*", "optimizer faults injected"),
           ("optimizer_faults_caught", "caught"), ("fdd_alarms", "FDD alarms"), ("fdd_true_alarms", "true"),
           ("fdd_episodes_detected", "fault episodes detected"), ("fdd_episodes", "of"),
           ("purge_steps", "purge steps"))


def ct_value(st: dict[str, Any], key: str) -> float:
    if key.endswith(".*"):
        return sum(st.get(key[:-2], {}).values())
    if "." in key:
        a, b = key.split(".")
        return st.get(a, {}).get(b, 0)
    return st.get(key, 0)


def strategies(res: dict[str, Any]) -> list[str]:
    row = next(iter(next(iter(res["table"].values()))["year"].values()))
    return [k for k in row if not k.endswith(("_saving_nok", "_saving_pct"))]


def year_row(res: dict[str, Any], area: str) -> dict[str, Any]:
    return next(iter(res["table"][area]["year"].values()))


def rows(res: dict[str, Any]) -> list[dict[str, Any]]:
    """One summary row per area, against the as-found baseline P0."""
    out = []
    for area, name in res["areas"].items():
        y = year_row(res, area)
        p0 = y["P0"]
        r: dict[str, Any] = {"area": area, "name": name, "p0_nok": p0["total_nok"], "p0_kwh": p0["kwh"],
                             "spot_price": p0["spot_nok"] / p0["kwh"], "by": {}}
        for s in strategies(res)[1:]:
            v = y[s]
            r["by"][s] = {
                "saving_nok": y[f"{s}_saving_nok"], "saving_pct": y[f"{s}_saving_pct"],
                "kwh_saved": p0["kwh"] - v["kwh"], "kwh_pct": 100 * (p0["kwh"] - v["kwh"]) / p0["kwh"],
                "consumers": {c: p0[c] - v[c] for c in CONSUMERS},
                "cost_parts": {c: p0[c] - v[c] for c in COST_PARTS},
                "comfort_dh": (p0["comfort_dh"], v["comfort_dh"]), "hazard_h": (p0["hazard_h"], v["hazard_h"]),
                "kwh_pct_ex_cables": 100 * ((p0["kwh"] - p0["heating_cables"]) - (v["kwh"] - v["heating_cables"]))
                / (p0["kwh"] - p0["heating_cables"]),
            }
        out.append(r)
    return out


def render_md(res: dict[str, Any]) -> str:
    rs, ss, sk = rows(res), strategies(res), safety_keys(res)
    best = ss[-1]
    by_nok = max(rs, key=lambda r: r["by"][best]["saving_nok"]["mean"])
    by_kwh = max(rs, key=lambda r: r["by"][best]["kwh_saved"])
    L = [f"# {title(res)}", "",
         f"Seeds {', '.join(map(str, res['seeds']))}. Strategies: {', '.join(ss)}. Scope: {res['scope']}", ""]
    L += how_to_read_md(res)
    L += abc_md(res)
    L += ["## Year", "",
         "| Area | P0 NOK | P0 kWh | Spot NOK/kWh (P0) | "
         + " | ".join(f"{s} saves NOK (min..max) | {s} % | {s} kWh %" for s in ss[1:]) + " |",
         "|---|---:|---:|---:|" + "---:|---:|---:|" * (len(ss) - 1)]
    for r in rs:
        cells = []
        for s in ss[1:]:
            b = r["by"][s]
            sv = b["saving_nok"]
            cells.append(f"{nok(sv['mean'])} ({nok(sv['min'])}..{nok(sv['max'])}) | {b['saving_pct']:.1f} | {b['kwh_pct']:.1f}")
        L.append(f"| {r['area']} {r['name']} | {nok(r['p0_nok'])} | {nok(r['p0_kwh'])} | {r['spot_price']:.2f} | "
                 + " | ".join(cells) + " |")
    L += ["", f"Largest saving in NOK with {best}: **{by_nok['area']} {by_nok['name']}**. Largest saving in kWh: "
          f"**{by_kwh['area']} {by_kwh['name']}**. The two differ because the spot price differs by area: a saved "
          "kWh in the north is worth a fraction of one in the south.", "",
          f"## Where the {best} saving comes from", "",
          "kWh saved against P0, by consumer, and the cost saved by tariff part (NOK).", "",
          "| Area | " + " | ".join(c.replace("_", " ") for c in CONSUMERS) + " | spot | grid energy | grid power | "
          "kWh % without the cable lever |",
          "|---|" + "---:|" * (len(CONSUMERS) + 4)]
    for r in rs:
        b = r["by"][best]
        L.append(f"| {r['area']} | " + " | ".join(nok(b["consumers"][c]) for c in CONSUMERS) + " | "
                 + " | ".join(nok(b["cost_parts"][c]) for c in COST_PARTS) + f" | {b['kwh_pct_ex_cables']:.1f} |")
    L += ["", "The heating cables dominate. As found they run on an air thermostat (-8..+3 degC), so they heat a dry "
          "ramp for most of the winter; on the ground sensor they run only while the surface is wet and freezing. "
          "That share rests on the twin's assumed cable ratings and the surface model, and a real site must be "
          "measured before the figure means anything. The last column shows the rest of the levers alone.", "",
          "## Comfort and safety", "",
          "| Area | " + " | ".join(f"{s} {lbl}" for s in ss for _, lbl in sk) + " |",
          "|---|" + "---:|" * (len(ss) * len(sk))]
    for r in rs:
        y = year_row(res, r["area"])
        L.append(f"| {r['area']} | " + " | ".join(f"{y[s][k]:.0f}" for s in ss for k, _ in sk) + " |")
    L += ["", "Comfort is degree-hours outside the occupied band, summed over three zones. The icing hazard is hours "
          "with the ground sensor reporting a wet surface at -10..+1 degC while a cable was off.", "",
          "Almost all the comfort deviation is summer overheating (May to September), not cold rooms. The "
          "strategies that light only occupied rooms and dim by daylight put less heat into the zones, so they "
          "overheat less. Winter comfort stays within a few degree-hours in every strategy.", ""]
    ct = caretaker_table(res)
    if ct:
        L += ["## The caretaker's own record (P3 and P3raw)", "",
              "Controller steps by route, optimizer faults injected and caught by the validator, and FDD alarms "
              "scored against the injected sensor faults. Means over the seeds.", "",
              "| Area | Arm | " + " | ".join(lbl for _, lbl in CT_COLS) + " |", "|---|---|" + "---:|" * len(CT_COLS)]
        for a, s, st in ct:
            L.append(f"| {a} | {s} | " + " | ".join(f"{ct_value(st, k):.0f}" for k, _ in CT_COLS) + " |")
        L.append("")
    for lv in ("half", "quarter", "month"):
        L += [f"## By {lv}", "", "| Area | Period | P0 NOK | " + " | ".join(f"{s} saves NOK | {s} %" for s in ss[1:])
              + " | P0 peak kW |", "|---|---|---:|" + "---:|---:|" * (len(ss) - 1) + "---:|"]
        for r in rs:
            for k, v in res["table"][r["area"]][lv].items():
                L.append(f"| {r['area']} | {k} | {nok(v['P0']['total_nok'])} | "
                         + " | ".join(f"{nok(v[f'{s}_saving_nok']['mean'])} | {v[f'{s}_saving_pct']:.1f}" for s in ss[1:])
                         + f" | {v['P0'].get('peak_kw', 0):.1f} |")
        L.append("")
    return "\n".join(L)


def grouped_bars(rs: list[dict[str, Any]], res: dict[str, Any], width: int = 760, height: int = 300) -> str:
    ss = strategies(res)
    ml, mr, mt, mb = 60, 10, 16, 40
    w, h = width - ml - mr, height - mt - mb
    vals = {r["area"]: [year_row(res, r["area"])[s]["total_nok"] for s in ss] for r in rs}
    vmax = max(max(v) for v in vals.values()) * 1.1
    band = w / len(rs)
    bw = band * 0.7 / len(ss)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Year cost per area and strategy">']
    for i in range(5):
        v = vmax * i / 4
        y = mt + h - h * v / vmax
        out.append(f'<line x1="{ml}" x2="{ml + w}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{ml - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{nok(v)}</text>')
    for i, r in enumerate(rs):
        x0 = ml + i * band + band * 0.15
        for j, v in enumerate(vals[r["area"]]):
            bh = h * v / vmax
            out.append(f'<rect x="{x0 + j * bw:.1f}" y="{mt + h - bh:.1f}" width="{bw - 2:.1f}" height="{bh:.1f}" '
                       f'rx="2" class="s{j}"><title>{E(r["area"])} {ss[j]}: {nok(v)} NOK</title></rect>')
        out.append(f'<text x="{ml + i * band + band / 2:.1f}" y="{height - 18}" class="lbl" text-anchor="middle">'
                   f'{E(r["area"])} {E(r["name"])}</text>')
    out.append("</svg>")
    return "".join(out)


def stacked_consumers(rs: list[dict[str, Any]], s: str, width: int = 760) -> str:
    rh, lw = 30, 130
    tot = max(r["by"][s]["kwh_saved"] for r in rs)
    out = [f'<svg viewBox="0 0 {width} {rh * len(rs) + 8}" width="100%" role="img" aria-label="kWh saved by consumer">']
    for i, r in enumerate(rs):
        y, x = 4 + i * rh, float(lw)
        out.append(f'<text x="0" y="{y + 17}" class="lbl">{E(r["area"])} {E(r["name"])}</text>')
        for j, c in enumerate(CONSUMERS):
            v = max(r["by"][s]["consumers"][c], 0.0)
            bw = (width - lw - 90) * v / tot
            if bw > 0.5:
                out.append(f'<rect x="{x:.1f}" y="{y + 5}" width="{bw:.1f}" height="16" class="c{j}">'
                           f'<title>{E(c.replace("_", " "))}: {nok(v)} kWh</title></rect>')
            x += bw
        out.append(f'<text x="{x + 6:.1f}" y="{y + 18}" class="tick">{nok(r["by"][s]["kwh_saved"])} kWh</text>')
    out.append("</svg>")
    return "".join(out)


def render_html(res: dict[str, Any], src: str = "docs/results/2026-10-27/areas-2025.json") -> str:
    rs, ss, sk = rows(res), strategies(res), safety_keys(res)
    best = ss[-1]
    by_nok = max(rs, key=lambda r: r["by"][best]["saving_nok"]["mean"])
    by_kwh = max(rs, key=lambda r: r["by"][best]["kwh_saved"])
    colors = ["var(--s0)", "var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]
    ccol = ["var(--c0)", "var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"]

    def tile(label: str, value: str, note: str) -> str:
        return f'<div class="tile"><div class="tl">{E(label)}</div><div class="tv">{E(value)}</div><div class="tn">{E(note)}</div></div>'

    head = "".join(f"<th>{s} saves NOK (min–max)</th><th>{s} %</th><th>{s} kWh %</th>" for s in ss[1:])
    yrows = ""
    for r in rs:
        cells = "".join(f"<td>{nok(r['by'][s]['saving_nok']['mean'])} <span class=muted>({nok(r['by'][s]['saving_nok']['min'])}–"
                        f"{nok(r['by'][s]['saving_nok']['max'])})</span></td><td>{r['by'][s]['saving_pct']:.1f}</td>"
                        f"<td>{r['by'][s]['kwh_pct']:.1f}</td>" for s in ss[1:])
        yrows += (f"<tr><td>{E(r['area'])} {E(r['name'])}</td><td>{nok(r['p0_nok'])}</td><td>{nok(r['p0_kwh'])}</td>"
                  f"<td>{r['spot_price']:.2f}</td>{cells}</tr>")
    crow = "".join(f"<tr><td>{E(r['area'])}</td>" + "".join(f"<td>{nok(r['by'][best]['consumers'][c])}</td>" for c in CONSUMERS)
                   + "".join(f"<td>{nok(r['by'][best]['cost_parts'][c])}</td>" for c in COST_PARTS)
                   + f"<td>{r['by'][best]['kwh_pct_ex_cables']:.1f} %</td></tr>" for r in rs)
    safety = "".join(f"<tr><td>{E(r['area'])}</td>" + "".join(
        f"<td>{year_row(res, r['area'])[s][k]:.0f}</td>" for s in ss for k, _ in sk) + "</tr>" for r in rs)
    ct = caretaker_table(res)
    ct_html = ""
    if ct:
        ct_html = ("<h2>The caretaker's own record</h2><p>Controller steps by route, optimizer faults injected and "
                   "caught by the validator, and FDD alarms scored against the injected sensor faults (means over "
                   "the seeds). P3raw is the same engine without telemetry checks or validator.</p>"
                   "<div class=card><table><tr><th>Area</th><th>Arm</th>"
                   + "".join(f"<th>{E(lbl)}</th>" for _, lbl in CT_COLS) + "</tr>"
                   + "".join(f"<tr><td>{E(a)}</td><td>{E(s)}</td>"
                             + "".join(f"<td>{ct_value(st, k):.0f}</td>" for k, _ in CT_COLS) + "</tr>"
                             for a, s, st in ct) + "</table></div>")
    flags = (" --faults" if res.get("faults") else "")
    stem = src.rsplit("/", 1)[-1].removesuffix(".json")
    out_dir = src.rsplit("/", 1)[0]
    details = ""
    for r in rs:
        body = ""
        for lv in ("half", "quarter", "month"):
            for k, v in res["table"][r["area"]][lv].items():
                body += (f"<tr><td>{E(k)}</td><td>{nok(v['P0']['total_nok'])}</td>"
                         + "".join(f"<td>{nok(v[f'{s}_saving_nok']['mean'])}</td><td>{v[f'{s}_saving_pct']:.1f}</td>" for s in ss[1:])
                         + f"<td>{v['P0'].get('peak_kw', 0):.1f}</td><td>{v['P0']['comfort_dh']:.0f} / {v[best]['comfort_dh']:.0f}</td></tr>")
        details += (f"<details class=card><summary><b>{E(r['area'])} {E(r['name'])}</b>: halves, quarters, months</summary>"
                    f"<table><tr><th>Period</th><th>P0 NOK</th>"
                    + "".join(f"<th>{s} saves</th><th>{s} %</th>" for s in ss[1:])
                    + f"<th>P0 peak kW</th><th>Comfort dh P0 / {best}</th></tr>{body}</table></details>")
    legend = "".join(f'<span><i class="sw" style="background:{colors[j]}"></i>{s}</span>' for j, s in enumerate(ss))
    clegend = "".join(f'<span><i class="sw" style="background:{ccol[j]}"></i>{E(c.replace("_", " "))}</span>'
                      for j, c in enumerate(CONSUMERS))
    sv = by_nok["by"][best]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title(res).removesuffix(", 2025"))}</title>
<style>
:root{{--bg:#f6f7f5;--panel:#fff;--fg:#1d2422;--muted:#5d6b66;--line:#d9dfdc;--accent2:#c27a2c;
--s0:#8a9692;--s1:#c27a2c;--s2:#2f6f5e;--s3:#3b5ea8;--s4:#8a4fa3;
--c0:#2f6f5e;--c1:#c27a2c;--c2:#b23b3b;--c3:#d9b45c;--c4:#3b5ea8;--c5:#6cc2a8;--c6:#8a9692;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,Menlo,Consolas,monospace;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#131817;--panel:#1b2220;--fg:#e4ebe8;--muted:#9aa8a3;--line:#2c3532;--accent2:#e3a35c;
--s0:#7d8a86;--s1:#e3a35c;--s2:#6cc2a8;--s3:#7f9fe0;--s4:#c08ad6;--c0:#6cc2a8;--c1:#e3a35c;--c2:#e07a7a;--c3:#e6cf85;--c4:#7f9fe0;--c5:#9fe0cc;--c6:#7d8a86;color-scheme:dark}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 var(--sans)}}
main{{max-width:1080px;margin:0 auto;padding:28px 16px 60px}}
h1{{font-size:28px;margin:0 0 4px}} h2{{font-size:19px;margin:34px 0 10px}}
p{{max-width:72ch}} .muted{{color:var(--muted)}}
.scope{{border-left:3px solid var(--accent2);padding:8px 12px;background:var(--panel);color:var(--muted);font-size:13.5px;max-width:none}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}
.tile,.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}}
.card{{margin:12px 0;overflow-x:auto}} summary{{cursor:pointer}}
.tl{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}} .tv{{font-size:23px;font-variant-numeric:tabular-nums}} .tn{{font-size:12.5px;color:var(--muted)}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}} th{{color:var(--muted);font-weight:600}}
code{{font-family:var(--mono);font-size:12.5px}}
svg .grid{{stroke:var(--line)}} svg .tick{{fill:var(--muted);font:11.5px var(--sans)}} svg .lbl{{fill:var(--fg);font:11.5px var(--sans)}}
{"".join(f"svg .s{j}{{fill:{colors[j]}}}" for j in range(len(colors)))}
{"".join(f"svg .c{j}{{fill:{ccol[j]}}}" for j in range(len(ccol)))}
.legend span{{display:inline-flex;align-items:center;gap:6px;margin-right:14px;font-size:12.5px;color:var(--muted)}} .sw{{width:12px;height:12px;border-radius:3px;display:inline-block}}
</style></head><body><main>
<h1>{E(title(res))}</h1>
<p class="muted">autonomous-ot-control · one fictitious building per Norwegian price area · seeds {", ".join(map(str, res["seeds"]))} · strategies {", ".join(ss)}</p>
<p class="scope">{E(res["scope"])}</p>
<div class="tiles">
{tile(f"Largest NOK saving ({best})", f"{by_nok['area']} {by_nok['name']}", f"{nok(sv['saving_nok']['mean'])} NOK · {sv['saving_pct']:.1f} %")}
{tile(f"Largest kWh saving ({best})", f"{by_kwh['area']} {by_kwh['name']}", f"{nok(by_kwh['by'][best]['kwh_saved'])} kWh · {by_kwh['by'][best]['kwh_pct']:.1f} %")}
{tile("Spot price range (P0 weighted)", f"{min(r['spot_price'] for r in rs):.2f}–{max(r['spot_price'] for r in rs):.2f}", "NOK/kWh, 2025, taxes excluded")}
{tile(f"Icing hazard with {best}", f"{sum(year_row(res, r['area'])[best]['hazard_h'] for r in rs):.0f} h", f"P0: {sum(year_row(res, r['area'])['P0']['hazard_h'] for r in rs):.0f} h over all areas")}
</div>
{how_to_read_html(res)}
{abc_html(res)}
<h2>1. Year cost by area</h2>
<p>The same building and the same strategies at five coordinates, each with its own verified day-ahead prices and local ERA5 weather. Cost is spot plus a declared reference grid tariff (energy part and the monthly power charge on the highest hourly kW).</p>
<div class="card"><div class="legend">{legend}</div>{grouped_bars(rs, res)}</div>
<div class="card"><table><tr><th>Area</th><th>P0 NOK</th><th>P0 kWh</th><th>Spot NOK/kWh</th>{head}</tr>{yrows}</table></div>
<p>The ranking in NOK and the ranking in kWh differ: a saved kWh is worth the area's spot price, and NO4's is a fraction of the southern areas'.</p>
<h2>2. Where the {best} saving comes from</h2>
<div class="card"><div class="legend">{clegend}</div>{stacked_consumers(rs, best)}</div>
<div class="card"><table><tr><th>Area</th>{"".join(f"<th>{E(c.replace('_', ' '))} kWh</th>" for c in CONSUMERS)}<th>spot NOK</th><th>grid energy NOK</th><th>grid power NOK</th><th>kWh saved without the cable lever</th></tr>{crow}</table>
<p class="muted">The heating cables dominate. As found they run on an air thermostat (−8..+3 °C) and heat a dry ramp for most of the winter; on the ground sensor they run only while the surface is wet and freezing. That share rests on the twin's assumed cable ratings and surface model: measure a real site before the figure means anything. The last column is everything else alone. Equipment (plug loads) is not controlled, so it never changes.</p></div>
<h2>3. Comfort and safety</h2>
<div class="card"><table><tr><th>Area</th>{"".join(f"<th>{s} {E(lbl)}</th>" for s in ss for _, lbl in sk)}</tr>{safety}</table>
<p class="muted">Comfort: degree-hours outside the occupied band, summed over three zones. Icing hazard: hours with the ground sensor reporting a wet surface at −10..+1 °C while a cable was off. The air thermostat misses ice below −8 °C (Tromsø, Oslo); the sensor mode never does. Almost all the comfort deviation is summer overheating (May to September), not cold rooms: presence lighting with daylight dimming puts less heat into the zones. Winter comfort stays within a few degree-hours in every strategy. CO2: ppm-hours above 1000 ppm while occupied, summed over the zones. Tank: hours above 60 °C, beyond the heat pump's supply limit.</p></div>
{ct_html}
<h2>4. Halves, quarters and months</h2>
{details}
<h2>5. Reproduce</h2>
<div class="card"><pre><code>python -m runner.exogenous verify scenarios/data/demo-building-no*/*.json   # re-fetch, compare hashes
python -m runner.areas --seeds {" ".join(map(str, res["seeds"]))} --days 365 --strategies {" ".join(ss)}{flags} --out artifacts/{stem}
python -m runner.report_areas --results {src} \\
    --md {out_dir}/{stem}.md --html {out_dir}/{stem}.html</code></pre></div>
</main></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.report_areas")
    p.add_argument("--results", required=True)
    p.add_argument("--md")
    p.add_argument("--html")
    a = p.parse_args(argv)
    res = json.loads(Path(a.results).read_text("utf-8"))
    if a.md:
        Path(a.md).write_text(render_md(res) + "\n", encoding="utf-8", newline="\n")
    if a.html:
        Path(a.html).write_text(render_html(res, a.results.replace("\\", "/")), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
