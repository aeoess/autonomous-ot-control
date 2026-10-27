# ADR-009: The caretaker profile engine (P3): overlays, profiles, a validator, a fallback, FDD

- Status: Accepted for the simulator. Not a physical-OT authorization.
- Date: 2026-10-27
- Related: ADR-004 (A/B/C arms), ADR-006 (presets), ADR-007 (plant, tools, interlocks), ADR-008 (decision paths)
- Code: `runner/caretaker.py`
- Contracts:
  - `contracts/caretaker-profile-catalog-v1.json` and its schema;
  - `deploy/ot/site_policy.v1.json`;
  - `deploy/ot/caretaker_fallback.v1.json`.

## Context

The owner shared two research notes on algorithmic setpoint optimisation for a digital caretaker. They agree on one architecture:

- the site policy defines what is allowed;
- a profile defines the goal;
- an algorithm proposes a value;
- REMORA decides whether the proposal becomes a physical action.

Both notes also say:

- keep kWh, NOK, kW and comfort apart;
- treat frost and water safety as overlays, not competing profiles;
- give machine-learning optimizers no safety authority;
- test with A/B/C arms on identical disturbances.

P2 (ADR-007 Addendum 2) proved that the twin has the levers. It is still one hand-written strategy. Nothing validates its output, and its tank step of +5 K in one 15-minute interval breaks IL-04's 3 K rule.

## Decision

1. **Overlays plus one base profile.**
   - The overlays are `frost_protection`, `iaq_protection`, `icing_protection` and `telemetry_degraded`. Any number can be active at once.
   - The base profiles, highest priority first, are `comfort_priority`, `demand_response`, `preconditioning`, `night_setback` and `energy_saving`. `balanced_default` is the fallback.
   - The selection is deterministic: the same context gives the same profile, algorithms and record. A test pins this.
2. **One small, named algorithm per setpoint.** Each has a one-line rule in the catalogue:
   - optimal start;
   - recovery setback;
   - DR offset and preconditioning;
   - unoccupied cooling setback;
   - Trim & Respond on the supply air (ASHRAE Guideline 36 style);
   - a summer night purge through the HRV bypass;
   - a weather-reset tank with price-ranked charging;
   - a peak limiter;
   - presence lighting;
   - ground-sensor cables.

   None of them is an MPC. `price_preheat_mpc`, `rl_shadow` and `portfolio_coordination` are catalogued as `not_implemented`.
3. **An independent validator.** It checks every proposal against the site policy and the active overlays: IL-01..IL-11, the frost and IAQ overlays, and step limits. It reads the twin's own state and occupancy, never the proposer's view of them.
4. **A hash-pinned deterministic fallback** replaces an invalid proposal. It reads no zone sensor and is validated too. If it also fails, the caretaker holds the current state and escalates.
5. **FDD on the zone sensors.**
   - A zone is suspect when its reading is stale (older than the guard's 600 s), jumps more than 1.5 K in one step, or carries the wrong unit.
   - A suspect zone runs on the fallback schedule and is escalated once.
   - It recovers only when the matching criterion holds for an hour: fresh again, the reverse jump, or the right unit. It never recovers in under 24 h. The first version released a drifted sensor after 24 h while it still read 3 K wrong. The recovery criterion replaced that latch.
   - Every alarm is recorded with its evidence: the reading, the previous reading, the age, the threshold and the action. The run scores it against the injected faults. The detector never reads them.
6. **A decision record per step** (`ot-caretaker-decision/v1`):
   - the context and its sha256;
   - the site policy's sha256;
   - the overlays, the base profile and the algorithm per setpoint;
   - the proposal, the validation verdict, any fallback (with its sha256), the changes and the route.

   With REMORA in the loop, this record is the evidence a write carries into VERIFY.
7. **Routes are the caretaker's, not REMORA's.**
   - `submit`: a change passed the validator.
   - `hold`: nothing to change.
   - `fallback`: the proposal failed and the fallback was sent.
   - `escalate`: new sensor fault, or no valid action.

   REMORA's own decision stays separate (ADR-008: reads ACCEPT, writes VERIFY). It is not predicted here.

## The A/B/C arms

| Arm | What | Where |
|---|---|---|
| A | P0, as found | every run |
| B | P3raw: the same proposal engine, no telemetry checks, no validator | offline only, never a physical write |
| C | P3 | every run |

The faulted run injects the same seeded faults into every arm:

- two frozen sensors per zone and year;
- one drifted sensor per zone and year (±3 K for 3..10 days);
- 1 % of the caretaker's proposals corrupted before validation, as a defective or compromised optimizer would emit them:
  - tank 70 °C;
  - supply air 10 °C;
  - AHU off;
  - cables off;
  - heating 12 °C;
  - an inverted deadband.

## Results

See `docs/results/2026-10-27/areas-2025.md` (no faults) and `abc-2025.md` (faults), summarised below.

Three seeds, 2025, all five price areas. A synthetic, uncalibrated building; the cost is spot plus a declared reference grid tariff (ASSUMPTION, taxes excluded). The saving is against P0 in the same run.

| Area | P2 saves | P3 saves | comfort dh P2 → P3 | P3raw saves (faults) | P3 saves (faults) | P3raw tank > 60 °C h | backup kWh P3raw / P3 | faults caught / injected | FDD episodes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 Oslo | 41.3 % | 41.9 % | 1211 → 970 | 37.4 % | 41.9 % | 322 | 285 / 26 | 261 / 355 | 9 / 9 |
| NO2 Kristiansand | 39.0 % | 39.9 % | 1141 → 925 | 34.5 % | 40.0 % | 383 | 280 / 22 | 260 / 355 | 9 / 9 |
| NO3 Trondheim | 33.9 % | 33.8 % | 731 → 623 | 27.3 % | 33.8 % | 355 | 341 / 21 | 262 / 355 | 9 / 9 |
| NO4 Tromsø | 25.5 % | 25.5 % | 391 → 319 | 16.4 % | 25.9 % | 283 | 354 / 16 | 265 / 355 | 9 / 9 |
| NO5 Bergen | 38.6 % | 39.8 % | 693 → 613 | 32.1 % | 39.4 % | 452 | 338 / 29 | 261 / 355 | 9 / 9 |

What this says:

- **P3 against P2, no faults.**
  - P3 saves about the same as P2: +0.6 to +1.2 points in NO1, NO2 and NO5, and none in NO3 and NO4.
  - P3 cuts the comfort deviation by 11 to 20 %. That deviation is summer overheating. The gain comes from the night purge, Trim & Respond and the unoccupied cooling setback.
  - The energy the caretaker cannot reach is mostly uncontrolled equipment (plug loads), 37 % (NO4) to 61 % (NO2) of P3's kWh.
- **C against B, with faults.** The validator is worth 4.5 to 9.5 points of saving.
  - Without it, about 1 % of corrupted proposals reach the plant. A 70 °C tank target runs the backup heater (COP 1) and sets new monthly power peaks: 280 to 354 kWh of backup against 16 to 29, and the power charge rises by 2 300 to 3 300 NOK a year.
  - The tank exceeds 60 °C for 283 to 452 h in B and for 0 h in C.
  - B also has 0 to 1 h of icing hazard (cables off on a freezing wet ramp) and 3 ppm-hours of CO2 above 1000 (the AHU off while occupied). C has none of either.
- **What the validator caught.**
  - It caught 260 to 265 of the 355 corrupted proposals per year.
  - Every one it missed broke no rule in its context: the AHU off in an empty building, or cables off on a dry ramp. Those changed nothing that matters.
  - The fallback replaced every caught proposal and itself passed validation every time. No step ended in an escalation for lack of a valid action.
- **FDD.**
  - 9 of 9 injected sensor-fault episodes were detected in every area, with 9 alarms and 0 false alarms.
  - The scope is limited: these are step faults (frozen, or ±3 K), the easy case. See the negative results.
  - The alarms with their evidence are in `abc-2025-fdd-alarms.jsonl`. One day's decision records for NO1 (2025-01-15 and 2025-07-15, seed 177) are in `abc-2025-decision-records-no1.jsonl`, including one fallback for `IL-04:range` and `IL-04:step`.

## Negative results kept

- **Adaptive deadband** (occupied heating −min(0.5, 0.5 z_p) K in expensive hours), measured on NO1 2025, seed 177:
  - −9 kWh and −31 NOK spot;
  - +268 NOK power charge, because the room reheats after the expensive hour and sets a new peak.

  It is in the code, off by default, and catalogued as `disabled`.
- **Sensor faults barely move the energy or comfort result.** The twin's radiator loop regulates on the true room temperature, so a frozen or drifted sensor reaches only the supervisory layer (optimal start, purge, Trim & Respond requests). In a real building the room controller uses the same sensor. There the fallback must also switch the zone controller to a backup sensor (the research note's `fallback_sensor`). The twin cannot do that yet.
- **FDD is scored on step faults only.** A slow drift below 1.5 K per step would not be caught by the jump rule, and no such fault is injected.

## Not decided here

- **Governed tools.** Every setpoint except `set_zone_setpoint` is written directly in these offline runs. Their governed tools, with signed ToolSpecs and the interlocks as REMORA cumulative limits, are the next slice.
- **An economic MPC** behind the same validator.
- **Real site values.** Every site-policy value is marked `site_required` and must come from the building's design basis, commissioning and regulations.
