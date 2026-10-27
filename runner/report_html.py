"""Render the testbed results of one day as a self-contained HTML report (English).

    python -m runner.report_html --results docs/results/2026-10-27 --out docs/results/2026-10-27/report.html

Reads only committed result files in that directory, so the report can be
rebuilt by anyone from the repository:

- governed-demo-week.json, governed-demo-ledger.jsonl;
- seasonal-2025.json;
- acceptance-942ba37.json;
- decision-record-sample.json.

No external scripts, fonts or images. Charts are inline SVG, and the page
follows the viewer's light or dark theme.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

E = html.escape


def nok(x: float, d: int = 0) -> str:
    return f"{x:,.{d}f}".replace(",", " ")


def load(results: Path) -> dict[str, Any]:
    j = lambda n: json.loads((results / n).read_text("utf-8"))  # noqa: E731
    ledger = [json.loads(x) for x in (results / "governed-demo-ledger.jsonl").read_text("utf-8").splitlines() if x]
    return {"demo": j("governed-demo-week.json"), "seasonal": j("seasonal-2025.json"),
            "accept": j("acceptance-942ba37.json"), "sample": j("decision-record-sample.json"),
            "ledger": ledger}


def bar_chart(months: list[tuple[str, float, float, float]], width: int = 760, height: int = 300) -> str:
    """Monthly P0 vs P1 cost (bars) and mean spot price (line, right axis)."""
    ml, mr, mt, mb = 56, 56, 16, 36
    w, h = width - ml - mr, height - mt - mb
    vmax = max(max(p0, p1) for _, p0, p1, _ in months) * 1.1
    pmax = max(p for *_, p in months) * 1.15
    n = len(months)
    band = w / n
    bw = band * 0.34
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
           f'aria-label="Monthly spot cost, P0 and P1, with mean spot price">']
    for i in range(5):
        v = vmax * i / 4
        y = mt + h - h * v / vmax
        out.append(f'<line x1="{ml}" x2="{ml + w}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{ml - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{nok(v)}</text>')
        pv = pmax * i / 4
        out.append(f'<text x="{ml + w + 6}" y="{y + 4:.1f}" class="tick">{pv:.2f}</text>')
    pts = []
    for i, (m, p0, p1, price) in enumerate(months):
        x0 = ml + i * band + band * 0.14
        for j, (v, cls) in enumerate(((p0, "b0"), (p1, "b1"))):
            bh = h * v / vmax
            out.append(f'<rect x="{x0 + j * (bw + 2):.1f}" y="{mt + h - bh:.1f}" width="{bw:.1f}" '
                       f'height="{bh:.1f}" rx="3" class="{cls}"><title>{E(m)} {"P0" if j == 0 else "P1"}: '
                       f'{nok(v)} NOK</title></rect>')
        cx = ml + i * band + band / 2
        pts.append(f"{cx:.1f},{mt + h - h * price / pmax:.1f}")
        out.append(f'<text x="{cx:.1f}" y="{mt + h + 16}" class="tick" text-anchor="middle">{E(m[5:])}</text>')
    out.append(f'<polyline points="{" ".join(pts)}" class="price"/>')
    for p in pts:
        x, y = p.split(",")
        out.append(f'<circle cx="{x}" cy="{y}" r="3" class="pricedot"/>')
    out.append(f'<text x="{ml}" y="{height - 4}" class="tick">NOK per month (bars)</text>'
               f'<text x="{ml + w}" y="{height - 4}" class="tick" text-anchor="end">mean spot NOK/kWh (line)</text>')
    out.append("</svg>")
    return "".join(out)


def hbars(rows: list[tuple[str, float]], width: int = 520) -> str:
    total = sum(v for _, v in rows)
    rh, lw = 26, 120
    out = [f'<svg viewBox="0 0 {width} {rh * len(rows) + 8}" width="100%" role="img" aria-label="Energy by consumer">']
    vmax = max(v for _, v in rows)
    for i, (k, v) in enumerate(rows):
        y = 4 + i * rh
        bw = (width - lw - 120) * v / vmax
        out.append(f'<text x="0" y="{y + 16}" class="lbl">{E(k.replace("_", " "))}</text>'
                   f'<rect x="{lw}" y="{y + 4}" width="{bw:.1f}" height="16" rx="3" class="b0"/>'
                   f'<text x="{lw + bw + 6:.1f}" y="{y + 17}" class="tick">{nok(v)} kWh · {100 * v / total:.0f} %</text>')
    out.append("</svg>")
    return "".join(out)


FINDINGS = [
    ("REMORA-research#1099", "SDK", "AuditRef could not parse the documented deferred audit reference, so approve() raised after a successful approval.", "merged"),
    ("REMORA-research#1100", "ToolSpec", "The closure digest ignored the registry's sibling package (the effect code), so editing it left the signed digest unchanged.", "merged"),
    ("REMORA-research#1101", "Limits", "The limit preview ran before the signed contract and called the observer with agent-chosen arguments.", "open, CI running"),
    ("testbed", "Image", "REMORA image lacked httpx; the registry failed on first dispatch, after the approval was spent.", "fixed"),
    ("testbed", "Compose", "Default demo needed governed secrets (compose interpolates every service).", "fixed"),
    ("testbed", "Tool metadata", "Without REMORA_TOOL_METADATA_FILE every tool floored to critical (senior_authority, evidence_insufficient).", "fixed"),
    ("testbed", "Grounding", "A read only ACCEPTs with literal identifiers in the work order, REMORA_GROUNDED_READ_ACCEPT, and a coverage domain equal to the tool's domain.", "fixed"),
    ("testbed", "Effect receipts", "Production requires a separate effect_verifier principal and REMORA_EFFECT_VERIFIER_BINDINGS.", "fixed"),
    ("testbed", "Network", "plant-private overlapped a Docker auto-assigned pool; moved to 10.250.250.0/24.", "fixed"),
    ("testbed", "Time", "Limit windows run on REMORA's wall clock; a time-compressed demo hits production budgets, so the demo signs a documented accelerated policy.", "documented"),
    ("environment", "Clock", "Docker Desktop's VM clock stepped back up to 866 ms;verifier timestamp alignment added so observations never precede the container's dispatch timestamp.", "fixed"),
    ("REMORA", "Effect verdict", "Upgraded to signed ToolSpec schema v5 with postcondition templates across all 10 supervisory write tools. REMORA now computes EFFECT_VERIFIED itself (verdict_source: computed, effect_outcome: EFFECT_VERIFIED).", "fixed"),
]


def render(d: dict[str, Any]) -> str:
    demo, sea, acc, sample, ledger = d["demo"], d["seasonal"], d["accept"], d["sample"], d["ledger"]
    s, c, audit = demo["summary"], demo["counts"], demo["audit"]
    g, r = s["governed_P1"], s["reference_P0"]
    year = sea["table"]["year"]["2025"]
    months = [(k, v["p0_cost_nok"], v["p1_cost_nok"], v["mean_price_nok_per_kwh"])
              for k, v in sea["table"]["month"].items()]
    breakdown = sorted(year["p0_breakdown_kwh"].items(), key=lambda x: -x[1])
    passed = sum(1 for x in acc if x["outcome"] == "PASS")
    failed = sum(1 for x in acc if x["outcome"] == "FAIL")
    not_run = sum(1 for x in acc if x["outcome"] == "NOT_RUN")
    env = sample["envelope"]
    led = sample["ledger_row"]
    eff = (env.get("effect") or {}).get("ledger_entry") or {}
    attacks = [x for x in ledger if str(x.get("origin", "")).startswith("attack:") and x.get("origin") != "attack:summary"]

    def tile(label: str, value: str, note: str) -> str:
        return f'<div class="tile"><div class="tl">{E(label)}</div><div class="tv">{E(value)}</div><div class="tn">{E(note)}</div></div>'

    periods = []
    for lvl in ("year", "half", "quarter"):
        for k, v in sea["table"][lvl].items():
            periods.append(f"<tr><td>{E(k)}</td><td>{nok(v['p0_kwh'])}</td><td>{nok(v['p0_cost_nok'])}</td>"
                           f"<td>{nok(v['p1_cost_nok'])}</td><td>{nok(v['savings_nok_mean'])} "
                           f"<span class=muted>({nok(v['savings_nok_min'])}–{nok(v['savings_nok_max'])})</span></td>"
                           f"<td>{v['savings_pct']:.1f} %</td><td>{v['comfort_delta_dh_mean']:+.2f}</td>"
                           f"<td>{v['mean_price_nok_per_kwh']:.2f}</td><td>{nok(v['heating_season_h'])}</td></tr>")
    mrows = "".join(
        f"<tr><td>{E(k)}</td><td>{nok(v['p0_cost_nok'])}</td><td>{nok(v['p1_cost_nok'])}</td>"
        f"<td>{nok(v['savings_nok_mean'])}</td><td>{v['comfort_delta_dh_mean']:+.2f}</td>"
        f"<td>{v['mean_price_nok_per_kwh']:.2f}</td><td>{nok(v['heating_season_h'])}</td></tr>"
        for k, v in sea["table"]["month"].items())
    arows = "".join(f'<tr><td>{E(x["case_id"])}</td><td>{E(x.get("title", ""))}</td>'
                    f'<td><span class="pill {x["outcome"].lower()}">{E(x["outcome"])}</span></td></tr>' for x in acc)
    atk = "".join(
        f"<tr><td>{E(x['origin'].split(':', 1)[1].replace('_', ' '))}</td><td>{E(str(x.get('to_c', x.get('replayed_item', ''))))}</td>"
        f"<td>{E(str(x.get('decision') or x.get('status') or ''))}</td>"
        f"<td>{E(str((x.get('reviewer') or {}).get('reason') or x.get('refusal') or x.get('detail') or ''))}</td>"
        f"<td>{'no effect' if not x.get('applied') else 'APPLIED'}</td></tr>" for x in attacks)
    frows = "".join(f"<tr><td>{E(a)}</td><td>{E(b)}</td><td>{E(t)}</td><td>{E(st)}</td></tr>" for a, b, t, st in FINDINGS)
    count_rows = "".join(f"<tr><td>{E(k.replace('_', ' '))}</td><td>{v}</td></tr>" for k, v in sorted(c.items()))
    sample_rows = [
        ("proposal", led["proposal_id"]), ("call", f"set_zone_setpoint {led['zone_id']} {led['from_c']} → {led['to_c']} °C"),
        ("why (strategy)", led["strategy_reason"]), ("work order", led["intent_ref"]),
        ("spot price at the time", f"{led['price_nok_per_kwh']} NOK/kWh"), ("outdoor", f"{led['outdoor_c']} °C"),
        ("REMORA decision", f"{led['decision']} — {', '.join(led['reasons'])}"),
        ("values grounded", str(led.get("grounded"))), ("required role", str(led.get("required_role"))),
        ("reviewer", f"{led['reviewer']['model']}: {led['reviewer']['reason']}"),
        ("dispatch", f"{led['execute']['outcome']} · twin {led['execute']['twin_status']} · ToolSpec enforced {led['execute']['toolspec_enforced']}"),
        ("envelope gate", (env.get("gate") or {}).get("outcome")),
        ("envelope policy version", (env.get("audit") or {}).get("policy_version")),
        ("effect (observer)", eff.get("effect_status")), ("effect verdict source (REMORA)", eff.get("effect_verdict_source")),
        ("effect outcome (REMORA)", (env.get("effect") or {}).get("effect_outcome")),
        ("verifier identity", eff.get("effect_verifier_identity")), ("effect vantage", eff.get("effect_vantage")),
    ]
    srows = "".join(f"<tr><th>{E(k)}</th><td>{E(str(v))}</td></tr>" for k, v in sample_rows)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>REMORA Testbed Report</title>
<style>
:root{{--bg:#f6f7f5;--panel:#ffffff;--fg:#1d2422;--muted:#5d6b66;--line:#d9dfdc;--accent:#2f6f5e;--accent2:#c27a2c;--ok:#2f7d4f;--bad:#b23b3b;--warn:#a6761d;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#131817;--panel:#1b2220;--fg:#e4ebe8;--muted:#9aa8a3;--line:#2c3532;--accent:#6cc2a8;--accent2:#e3a35c;--ok:#6cc28a;--bad:#e07a7a;--warn:#d9b45c;color-scheme:dark}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 var(--sans)}}
main{{max-width:1080px;margin:0 auto;padding:28px 18px 60px}}
h1{{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}} h2{{font-size:19px;margin:34px 0 10px}} h3{{font-size:15px;margin:18px 0 6px}}
p{{max-width:70ch}} .muted{{color:var(--muted)}} .scope{{border-left:3px solid var(--accent2);padding:8px 12px;background:var(--panel);color:var(--muted);font-size:13.5px;max-width:none}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}}
.tl{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}} .tv{{font-size:24px;font-variant-numeric:tabular-nums;margin:2px 0}} .tn{{font-size:12.5px;color:var(--muted)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:12px 0;overflow-x:auto}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:12px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}}
th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}} th{{color:var(--muted);font-weight:600}}
code{{font-family:var(--mono);font-size:12.5px}}
.pill{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600}}
.pill.pass{{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}} .pill.fail{{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad)}}
.pill.not_run{{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}}
svg .grid{{stroke:var(--line);stroke-width:1}} svg .tick,svg .lbl{{fill:var(--muted);font:11.5px var(--sans)}} svg .lbl{{fill:var(--fg)}}
svg .b0{{fill:var(--accent);opacity:.85}} svg .b1{{fill:var(--accent2);opacity:.9}} svg .price{{fill:none;stroke:var(--fg);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}} svg .pricedot{{fill:var(--fg)}}
.legend span{{display:inline-flex;align-items:center;gap:6px;margin-right:14px;font-size:12.5px;color:var(--muted)}} .sw{{width:12px;height:12px;border-radius:3px;display:inline-block}}
.flow{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px}} .flow b{{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-weight:600}}
</style></head><body><main>
<h1>REMORA Testbed Report</h1>
<p class="muted">autonomous-ot-control · 2026-10-27 · fictitious <code>demo-building-001</code> at 63.4182 N 10.4051 E (Trondheim, price area NO3)</p>
<p class="scope">Synthetic, uncalibrated building-services twin. Real inputs: NO3 day-ahead spot prices (hvakosterstrommen.no) and ERA5 weather (Open-Meteo), verified by re-fetch. Spot cost only (no grid tariff, VAT or markup). Same-test-deployment vantage. Nothing here is a real-building saving or a claim about physical OT safety.</p>

<div class="tiles">
{tile("Writes through REMORA", f"{c.get('applied', 0)} / {c.get('write_verify', 0)}", "VERIFY, approved by plan, applied")}
{tile("Reads", f"{c.get('read_accept', 0)} ACCEPT", f"{c.get('read_executed', 0)} redeemed, {c.get('read_not_executed', 0)} refused (clock)")}
{tile("Attacks blocked", f"{c.get('attacks_blocked', 0)} / {c.get('attacks', 0)}", "no plant effect")}
{tile("Week saving vs P0", f"{s['savings_nok']:.2f} NOK", f"{s['savings_pct_cost']:.1f} % · comfort {s['comfort_delta_degree_hours']:+.2f} dh")}
{tile("Year 2025 saving", f"{nok(year['savings_nok_mean'])} NOK", f"{year['savings_pct']:.1f} % of {nok(year['p0_cost_nok'])} NOK")}
{tile("Acceptance", f"{passed} PASS", f"{failed} FAIL · {not_run} NOT_RUN")}
</div>

<h2>1. The governed week (REMORA in the loop)</h2>
<p>Seven simulated days (2026-01-15..21) on the live stack: REMORA {E('123a5fd0')} in production mode with PostgreSQL, a signed ToolSpec and signed cumulative limits. Strategy P1 (night setback with self-tuning optimal start) proposed every change; each day's plan was a work order with the literal values; a declared <em>careful</em> reviewer approved only plan steps; the dispatcher executed; the read-only observer recorded each effect under its own principal.</p>
<div class="card"><div class="flow"><b>read_zone_state</b> → <b>ACCEPT</b> (grounded_read_accept) → <b>execute-accepted</b>
&nbsp;&nbsp;|&nbsp;&nbsp;<b>set_zone_setpoint</b> → <b>VERIFY</b> (authority_resolved_review) → <b>reviewer</b> → <b>execute</b> → <b>twin APPLIED</b> → <b>effect recorded</b></div></div>
<div class="grid2">
<div class="card"><h3>Decisions</h3><table>{count_rows}</table></div>
<div class="card"><h3>Against the fixed schedule P0, same inputs</h3><table>
<tr><th></th><th>P0</th><th>P1 governed</th></tr>
<tr><td>Energy (kWh)</td><td>{r['energy_kwh']:.1f}</td><td>{g['energy_kwh']:.1f}</td></tr>
<tr><td>Spot cost (NOK)</td><td>{r['energy_cost_simulated']:.2f}</td><td>{g['energy_cost_simulated']:.2f}</td></tr>
<tr><td>Comfort outside band (degree-hours)</td><td>{r['comfort_violation_degree_hours']:.2f}</td><td>{g['comfort_violation_degree_hours']:.2f}</td></tr>
<tr><td>Setpoint changes</td><td>{r['setpoint_changes_count']}</td><td>{g['setpoint_changes_count']}</td></tr></table>
<p class="muted">Determinism: live twin {s['determinism']['live_energy_kwh']} kWh, offline replay {s['determinism']['replay_energy_kwh']} kWh, match = {s['determinism']['match']}. Audit chain valid = {audit.get('valid')}, {audit.get('records_checked')} records.</p></div>
</div>
<div class="card"><h3>Day 2 attacks</h3><table><tr><th>Attack</th><th>Value</th><th>REMORA</th><th>Why refused</th><th>Plant</th></tr>{atk}</table></div>

<h2>2. One decision, end to end</h2>
<p>A real record from the run (<code>decision-record-sample.json</code>): the ledger row and REMORA's DecisionEnvelope for the same proposal.</p>
<div class="card"><table>{srows}</table>
<p class="muted">With ToolSpec schema v5 signed postconditions, REMORA instantiates the postcondition contract from the approved call's arguments and computes <code>EFFECT_VERIFIED</code> itself (<code>verdict_source: "computed"</code>, <code>effect_outcome: "EFFECT_VERIFIED"</code>).</p></div>

<h2>3. A year of verified data: 2025</h2>
<p>The same strategy over the 2025 Trondheim year, three seeds, with Norwegian holidays, seeded presence and equipment variation, and a summer stop (no space heating above a 15 °C 24-hour mean). P1 is applied directly here; the governed week shows that with REMORA approving the same changes the energy is identical.</p>
<div class="card"><div class="legend"><span><i class="sw" style="background:var(--accent)"></i>P0 fixed schedule</span><span><i class="sw" style="background:var(--accent2)"></i>P1 setback + optimal start</span><span><i class="sw" style="background:var(--fg)"></i>mean spot price</span></div>
{bar_chart(months)}</div>
<div class="grid2">
<div class="card"><h3>Year, halves, quarters</h3><table><tr><th>Period</th><th>P0 kWh</th><th>P0 NOK</th><th>P1 NOK</th><th>Saved NOK (range)</th><th>Saved</th><th>Comfort Δ dh</th><th>Spot NOK/kWh</th><th>Heating h</th></tr>{"".join(periods)}</table></div>
<div class="card"><h3>Where the energy goes (P0, 2025)</h3>{hbars(breakdown)}
<p class="muted">Setback reaches only the heat pump's share. The large levers (heating cables, lighting, AHU night stop, price-shifted tank charging) are specified tools that are not built yet.</p></div>
</div>
<div class="card"><h3>By month</h3><table><tr><th>Month</th><th>P0 NOK</th><th>P1 NOK</th><th>Saved NOK</th><th>Comfort Δ dh</th><th>Spot NOK/kWh</th><th>Heating h</th></tr>{mrows}</table></div>

<h2>4. Governed acceptance suite</h2>
<div class="card"><table><tr><th>Case</th><th>What it checks</th><th>Outcome</th></tr>{arows}</table>
<p class="muted">NOT_RUN cases are not counted as passes. Local run at testbed 942ba37, REMORA 123a5fd0.</p></div>

<h2>5. Findings</h2>
<div class="card"><table><tr><th>Where</th><th>Area</th><th>Finding</th><th>Status</th></tr>{frows}</table></div>

<h2>6. Reproduce</h2>
<div class="card"><pre><code>./deploy/scripts/run-governed.sh                      # acceptance suite
DAYS=7 ./deploy/scripts/run-demo-governed.sh          # the governed week
python -m runner.seasonal --seeds 177 178 179 --out artifacts/seasonal-2025
python -m runner.report_html --results docs/results/2026-10-27 --out docs/results/2026-10-27/report.html</code></pre></div>
</main></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.report_html")
    p.add_argument("--results", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    Path(a.out).write_text(render(load(Path(a.results))), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
