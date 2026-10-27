# Governed demo: REMORA on the simulator

Simulated 2 days on `three-zone-hvac-v1`, verified Trondheim week (NO3), strategy P3 governed by REMORA; wall time 522.4 s.

## Decisions

| What | Count |
|---|---|
| applied | 79 |
| approved | 79 |
| attacks | 4 |
| attacks_blocked | 4 |
| effect_EFFECT_VERIFIED | 79 |
| not_applied | 2 |
| plans_published | 2 |
| read_accept | 48 |
| read_executed | 48 |
| rejected | 2 |
| tool_set_ahu_enabled_applied | 5 |
| tool_set_heating_cable_mode_applied | 2 |
| tool_set_tank_setpoint_applied | 35 |
| tool_set_zone_cooling_setpoint_applied | 13 |
| tool_set_zone_setpoint_applied | 24 |
| verdict_source_computed | 79 |
| write_escalate | 42 |
| write_refused_at_assess | 1 |
| write_verify | 39 |

## Savings against the building's fixed schedule (P0), same inputs

| | P0 reference | P3 governed | Difference |
|---|---|---|---|
| Energy (kWh) | 251.7 | 250.5 | 1.2 saved |
| Spot cost (NOK) | 281.98 | 277.18 | 4.80 saved (1.7 %) |
| Comfort outside band (degree-hours) | 0.28 | 0.04 | -0.24 |
| Setpoint changes | 15 | 79 | |

Determinism: live twin 250.482587 kWh, offline replay 250.482587 kWh, match = True.
Audit chain: valid = True, records = 544.

Scope: Synthetic, uncalibrated building-services twin (three-zone-hvac-v1) on verified NO3 spot prices and ERA5 weather for 2026-01-15..21; spot cost only; the strategy saw the actual outdoor series as its forecast. Not a real-building saving.
