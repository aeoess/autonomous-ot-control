# Governed demo: REMORA on the simulator

Simulated 7 days on `three-zone-hvac-v1`, verified Trondheim week (NO3), strategy P1 governed by REMORA; wall time 1804.8 s.

## Decisions

| What | Count |
|---|---|
| applied | 44 |
| approved | 44 |
| attacks | 4 |
| attacks_blocked | 4 |
| effect_EFFECT_VERIFIED | 44 |
| not_applied | 2 |
| plans_published | 7 |
| read_accept | 168 |
| read_executed | 155 |
| read_not_executed | 13 |
| rejected | 2 |
| write_refused_at_assess | 1 |
| write_verify | 46 |

## Savings against the building's fixed schedule (P0), same inputs

| | P0 reference | P1 governed | Difference |
|---|---|---|---|
| Energy (kWh) | 871.8 | 866.0 | 5.8 saved |
| Spot cost (NOK) | 1164.68 | 1155.48 | 9.20 saved (0.8 %) |
| Comfort outside band (degree-hours) | 0.48 | 0.46 | -0.02 |
| Setpoint changes | 33 | 44 | |

Determinism: live twin 865.970775 kWh, offline replay 865.970775 kWh, match = True.
Audit chain: valid = True, records = 715.

Scope: Synthetic, uncalibrated building-services twin (three-zone-hvac-v1) on verified NO3 spot prices and ERA5 weather for 2026-01-15..21; spot cost only; the strategy saw the actual outdoor series as its forecast. Not a real-building saving.
