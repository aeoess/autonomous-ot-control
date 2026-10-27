# Results, 2026-10-27

| File | What it is |
|---|---|
| `report.html` | Self-contained English report of everything below (open in a browser). Rebuilt by `python -m runner.report_html --results docs/results/2026-10-27 --out docs/results/2026-10-27/report.html`. |
| `governed-demo-week.md`, `.json` | The governed week: decisions by outcome, savings against P0, determinism and audit chain. |
| `governed-demo-ledger.jsonl` | One row per proposal of the governed week, attacks included. |
| `decision-record-sample.json` | One proposal's ledger row with REMORA's DecisionEnvelope and evidence, verbatim. |
| `seasonal-2025.md`, `.json` | P0 against P1 over the verified 2025 year, three seeds. |
| `areas-2025.md`, `.json`, `.html` | P0, P1, P2 and P3 over the 2025 year in all five price areas (NO1 Oslo, NO2 Kristiansand, NO3 Trondheim, NO4 Tromsø, NO5 Bergen), three seeds, local prices and weather, the grid tariff, energy by consumer, comfort, icing, CO2 and tank safety. |
| `abc-2025.md`, `.json`, `.html` | The A/B/C comparison with seeded faults: P0 (A), P3raw without telemetry checks or validator (B), P3 (C). Same sensor faults and corrupted proposals in every arm; the caretaker's routes, faults caught and FDD scores. |
| `abc-2025-fdd-alarms.jsonl` | Every FDD alarm of the faulted run with its evidence and its ground truth. |
| `abc-2025-decision-records-no1.jsonl` | The caretaker's decision record for every step of 2025-01-15 and 2025-07-15, NO1, seed 177 (`ot-caretaker-decision/v1`). |

The area reports are rebuilt by `python -m runner.report_areas --results docs/results/2026-10-27/<stem>.json --md docs/results/2026-10-27/<stem>.md --html docs/results/2026-10-27/<stem>.html`.

| `acceptance-942ba37.json`, `-manifest.json` | The governed acceptance suite at testbed 942ba37: 14 PASS, 0 FAIL, 4 NOT_RUN. |

**How to read "EFFECT_VERIFIED".** The counter `effect_EFFECT_VERIFIED` in the demo summary is the verdict of the testbed's read-only observer. REMORA records each one as the verifier's claim, bound to that dispatch's grant: the envelope's effect outcome is `EFFECT_CLAIMED`, with verdict source `claimed`. A verdict REMORA computes itself needs a signed postcondition in the ToolSpec (schema v5). That is the next step and is not established here.

Scope for every file: a synthetic, uncalibrated building on verified spot prices and ERA5 weather, same-test-deployment vantage. The governed week and `seasonal-2025` use NO3 and spot cost only; `areas-2025` and `abc-2025` add the other four areas and a declared reference grid tariff (ASSUMPTION, taxes excluded). These are not real-building savings.
