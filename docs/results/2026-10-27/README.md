# Results, 2026-10-27

| File | What it is |
|---|---|
| `report.html` | Self-contained English report of everything below (open in a browser). Rebuilt by `python -m runner.report_html --results docs/results/2026-10-27 --out docs/results/2026-10-27/report.html`. |
| `governed-demo-week.md`, `.json` | The governed week (P1): decisions by outcome, savings against P0, determinism and audit chain. |
| `governed-demo-ledger.jsonl` | One row per proposal of the governed week, attacks included. |
| `governed-p3-demo.md`, `.json`, `governed-p3-ledger.jsonl` | The governed P3 caretaker run with all supervisory tools in the REMORA loop (`set_zone_setpoint`, `set_zone_cooling_setpoint`, `set_tank_setpoint`, `set_ahu_enabled`, `set_heating_cable_mode`), ToolSpec v5 postconditions (`verdict_source: computed`), 4/4 attacks blocked and 100 % determinism. |
| `decision-record-sample.json` | One proposal's ledger row with REMORA's DecisionEnvelope (`effect_outcome: EFFECT_VERIFIED`, `effect_verdict_source: computed`) and evidence, verbatim. |
| `seasonal-2025.md`, `.json` | P0 against P1 over the verified 2025 year, three seeds. |
| `areas-2025.md`, `.json`, `.html` | P0, P1, P2 and P3 over the 2025 year in all five price areas (NO1 Oslo, NO2 Kristiansand, NO3 Trondheim, NO4 Tromsø, NO5 Bergen), three seeds, local prices and weather, the grid tariff, energy by consumer, comfort, icing, CO2 and tank safety. |
| `abc-2025.md`, `.json`, `.html` | The A/B/C comparison with seeded faults: P0 (A), P3raw without telemetry checks or validator (B), P3 (C). Same sensor faults and corrupted proposals in every arm; the caretaker's routes, faults caught and FDD scores. |
| `abc-2025-fdd-alarms.jsonl` | Every FDD alarm of the faulted run with its evidence and its ground truth. |
| `abc-2025-decision-records-no1.jsonl` | The caretaker's decision record for every step of 2025-01-15 and 2025-07-15, NO1, seed 177 (`ot-caretaker-decision/v1`). |

The area reports are rebuilt by `python -m runner.report_areas --results docs/results/2026-10-27/<stem>.json --md docs/results/2026-10-27/<stem>.md --html docs/results/2026-10-27/<stem>.html`.

| `acceptance-942ba37.json`, `-manifest.json` | The governed acceptance suite at testbed 942ba37: 14 PASS, 0 FAIL, 4 NOT_RUN. |

**How to read "EFFECT_VERIFIED".** With ToolSpec schema v5 signed postconditions (`deploy/ot/sign_deployment.py`), every governed write tool carries a signed `postcondition` template (`target_selector`, `expected_fields` with `{"$arg": ...}`, and `comparison_rules`). When the independent read-only observer (`ot-effect-observer/v1`) submits the observed state and arguments, REMORA verifies that the arguments hash to the dispatched call, instantiates the signed postcondition, and computes `EFFECT_VERIFIED` itself (`effect_outcome: EFFECT_VERIFIED`, `effect_verdict_source: computed`).

Scope for every file: a synthetic, uncalibrated building on verified spot prices and ERA5 weather, same-test-deployment vantage. The governed week and `seasonal-2025` use NO3 and spot cost only; `areas-2025` and `abc-2025` add the other four areas and a declared reference grid tariff (ASSUMPTION, taxes excluded). These are not real-building savings.
