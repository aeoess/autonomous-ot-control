# Energy-saving strategies and REMORA synergy (2025, five price areas)

The caretaker (`runner/caretaker.py`, ADR-009) combines safety overlays
(`frost_protection`, `iaq_protection`, `icing_protection`, `telemetry_degraded`),
one active base profile (`comfort_priority`, `demand_response`, `preconditioning`,
`night_setback`, `energy_saving`, or `balanced_default`), one named algorithm
per setpoint, an independent validator against [`deploy/ot/site_policy.v1.json`](../deploy/ot/site_policy.v1.json),
a hash-pinned sensor-free fallback ([`deploy/ot/caretaker_fallback.v1.json`](../deploy/ot/caretaker_fallback.v1.json)),
and zone sensor FDD. Every supervisory change maps to one of the 10 governed
tools in [datapoints.md](datapoints.md) and carries its decision record
(`ot-caretaker-decision/v1`) as evidence into REMORA.

## Strategies

| Strategy | What it is |
|---|---|
| P0 | As found (`runner.baseline`): 21 °C occupied with 1 h preheat, 20 °C otherwise; fixed AHU schedule; lighting on schedule; heating cables on an air thermostat; tank at 50 °C with no cap |
| P1 | P0 plus night setback to 19 °C and self-tuning optimal start (`runner/strategies.py`, ADR-006) |
| P2 | P1 plus weekend/holiday setback by outdoor mean, occupancy-aware ventilation, presence lighting with daylight dimming, ground-sensor cable control, tank on the radiator curve with a cheap-hour boost and a peak limiter |
| P3 | The caretaker profile engine: overlays, profiles, independent validator, hash-pinned fallback and FDD (`runner/caretaker.py`, ADR-009) |
| P3raw | P3 without the independent validator, run with the same seeded sensor faults |

## Results, full year 2025

| Price area | P0 cost (NOK) | P0 energy (kWh) | P3 saves vs P0 (%) | P3 saves (NOK) | P3raw (no validator, faulted) | Validator value |
|---|---:|---:|---:|---:|---:|---:|
| **NO1 Oslo** | 56 127 | 47 331 | **41.9 %** | 23 517 | 37.4 % | +4.5 pp (0 h tank > 60 °C vs 322 h) |
| **NO2 Kristiansand** | 53 103 | 40 410 | **39.9 %** | 21 208 | 34.5 % | +5.5 pp (0 h tank > 60 °C vs 383 h) |
| **NO5 Bergen** | 48 704 | 43 629 | **39.8 %** | 19 384 | 32.1 % | +7.3 pp (0 h tank > 60 °C vs 452 h) |
| **NO3 Trondheim** | 37 092 | 48 840 | **33.8 %** | 12 539 | 27.3 % | +6.5 pp (0 h tank > 60 °C vs 355 h) |
| **NO4 Tromsø** | 29 997 | 65 101 | **25.5 %** | 7 637 | 16.4 % | +9.5 pp (0 h tank > 60 °C vs 283 h) |

Synthetic, uncalibrated building (3 seeds, full year 2025, verified local Nord
Pool spot prices + ERA5 weather + declared reference grid tariff, taxes
excluded). Without the heating-cable lever (ground moisture sensor vs air
thermostat), P3 saves 9–14 % kWh across the remaining HVAC and lighting levers.
None of this is a real-building saving.

Full reports: [areas-2025.md](results/2026-10-27/areas-2025.md) /
[areas-2025.html](results/2026-10-27/areas-2025.html) and
[abc-2025.md](results/2026-10-27/abc-2025.md) /
[abc-2025.html](results/2026-10-27/abc-2025.html).

Reproduce without Docker:

```bash
python -m runner.areas --seeds 177 178 179 --out artifacts/areas-2025
```
