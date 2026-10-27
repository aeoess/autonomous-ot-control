# Results, 2026-10-27

| File | What it is |
|---|---|
| `report.html` | Self-contained English report of everything below (open in a browser). Rebuilt by `python -m runner.report_html --results docs/results/2026-10-27 --out docs/results/2026-10-27/report.html`. |
| `governed-demo-week.md`, `.json` | The governed week: decisions by outcome, savings against P0, determinism and audit chain. |
| `governed-demo-ledger.jsonl` | One row per proposal of the governed week, attacks included. |
| `decision-record-sample.json` | One proposal's ledger row with REMORA's DecisionEnvelope and evidence, verbatim. |
| `seasonal-2025.md`, `.json` | P0 against P1 over the verified 2025 year, three seeds. |
| `acceptance-942ba37.json`, `-manifest.json` | The governed acceptance suite at testbed 942ba37: 14 PASS, 0 FAIL, 4 NOT_RUN. |

**How to read "EFFECT_VERIFIED".** The counter `effect_EFFECT_VERIFIED` in the demo summary is the verdict of the testbed's read-only observer. REMORA records each one as the verifier's claim, bound to that dispatch's grant: the envelope's effect outcome is `EFFECT_CLAIMED`, with verdict source `claimed`. A verdict REMORA computes itself needs a signed postcondition in the ToolSpec (schema v5). That is the next step and is not established here.

Scope for every file: a synthetic, uncalibrated building on verified NO3 spot prices and ERA5 weather, spot cost only, same-test-deployment vantage. These are not real-building savings.

**Levers, 2025** (`levers-2025.json`, `python -m runner.levers --seed 177`). This is an offline what-if per lever on top of P1, with no REMORA in the loop: it shows the saving is there to be had, not that it was achieved.

| Lever | Saved per year | Energy | Note |
|---|---|---|---|
| AHU night stop | 260 NOK (1.9 %) | 1 101 kWh | |
| Deep setback (16 °C) | 90 NOK | 478 kWh | |
| Cable moisture control | 1 686 NOK (12 %) | 4 032 kWh | adds 448 h of icing risk with the circuit off, against the conservative 3-hour risk window |
| Tank price shifting | 36 NOK | uses 1 133 kWh more | |
| All four | 2 106 NOK (15 %) | 4 624 kWh | |

Two simulator caveats:

- Equipment (56 %, mostly the lab's constant 2 kW base load) cannot be controlled by any tool.
- The comfort figure of about 990 degree-hours per year is the same in every case. It comes from the cooling setpoint sitting on the comfort band's upper edge, where proportional cooling settles slightly above 24 °C. That is a configuration artifact to fix (cooling setpoint 23.5 °C), not a property of any strategy.
