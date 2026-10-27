# ADR-006 — Control strategy presets: realistic, forecast-aware, price-aware, governed

- Status: Proposed (design; presets P0–P3 and the heating-cable extension are built in later slices)
- Date: 2026-10-27
- Related: ADR-004 (arms, oracle), ADR-005 (verified data, savings, decision ledger); SDD-OT-001 §5, INV-07

## Context

A pilot only shows value if the controller REMORA governs is one a building
operator would recognize, saves money for reasons that survive scrutiny, and
never trades safety for kroner. A first proposal (owner, 2026-10-27) listed
deterministic presets for heating, lighting, ventilation and cooling. It set
the priority order safety → comfort → rate and cumulative limits → cost →
preferences. This ADR adapts it to what the twin models, to a Norwegian
commercial building in winter, and to an honest savings measurement.

## What the twin models, and what it does not

The twin models heating only: three zones, an RC envelope and a proportional
electric heater per zone.

- The time constant is about C/UA = 6 MJ/K / 110 W/K ≈ 15 h.
- The heater is 9 kW. At -5 degC outdoors, holding 21 degC takes about 2.9 kW,
  so the spare capacity reheats a zone by about 1 K per hour.

Night setback and preheat therefore have a real, measurable effect here.

Not modelled, so not optimized and never claimed:
- cooling (irrelevant in a Trondheim January; needs a summer model);
- ventilation, CO2 and humidity;
- lighting and daylight;
- heat-pump COP;
- district heating.

The proposal's DCV, economizer, daylight-harvesting and price-dimming presets
wait until the twin has those processes. Optimizing a load the twin does not
simulate would produce fictitious savings.

## Principles (adopted from the proposal, made testable)

1. **The strategy proposes; REMORA decides; the interlock is last.** A preset
   is agent-side code that emits `set_zone_setpoint` proposals with a reason.
   It holds no credential and no authority.
2. **Priority order:**
   1. hard safety (frost, interlock);
   2. comfort band during occupancy;
   3. rate and cumulative limits;
   4. cost inside the band;
   5. preferences, which are advisory only.

   A lower level never relaxes a higher one. Each proposal records the level
   that chose it.
3. **Bands, not points.** Hysteresis: no proposal unless the change is at least 0.3 degC.
4. **Information honesty.** The strategy may use only what it could have known
   at decision time:
   - **Prices:** day-ahead prices, which are published for the next day
     around 12:45 CET, so the price horizon runs to the end of tomorrow only
     after that time.
   - **Weather:** a replay drives the plant with actual ERA5 temperature.
     The strategy sees the archived forecast issued the day before (Open-Meteo
     previous-runs, `temperature_2m_previous_day1`), snapshotted and verified
     like ADR-005's data.
   - **Model:** the strategy's own model of the building is **not** the
     plant. It runs with declared parameter errors (capacity and UA ±20%,
     seeded), so a forecast-based plan meets a plant that behaves differently.
   - **Occupancy:** from the calendar. Unplanned occupancy is an episode event
     the strategy learns about only when it happens.

   A perfect-foresight run (actual weather, exact model) is computed **only**
   as an upper bound and labelled so. It is never the headline figure.

## Modes and bands (owner decision D-15)

| Mode | When | Band (degC) | Purpose |
|---|---|---|---|
| `OCCUPIED` | calendar occupancy | 20.0–22.0, target 21.0 | comfort |
| `PREHEAT` | before occupancy, lead from the model | ramps to ≥ 20.0 at the occupancy start | optimal start |
| `SETBACK` | nights, weekends | 17.0–19.0 | night setback (nattsenking) |
| `FROST` | long absence (holiday) | floor 10.0 | frost protection (frostsikring) |
| hard floor | always | 8.0 | the interlock refuses below it in every mode |

The current interlock and the signed ToolSpec bound every zone to 19–23 degC,
which makes realistic setback impossible. The proposal is:

- **Signed ToolSpec:** widen to 8.0–23.0.
- **Mode rule in the interlock:** below 20.0 only while the zone is not
  occupied, and the strategy must be able to recover in time. The twin knows
  the calendar, so this stays a local check.
- **REMORA limits:** the cumulative movement budget stays, so a setback is a
  planned ramp, not a jump. With the current interlock (1.0 degC per change,
  2 changes per hour), 21 to 17 takes two hours.

Changing these bounds is a safety-configuration change and needs the owner's
sign-off.

## Presets

| Id | Name | Behaviour | Uses |
|---|---|---|---|
| P0 | Fixed schedule | 21 occupied (1 h preheat), 20 otherwise. The existing `runner.baseline`, the reference every saving is measured against | calendar |
| P1 | Classic BMS | P0, plus setback to 17 on nights and weekends and a fixed 3 h preheat; weather-compensated preheat lead (lead = a + b·(20 − T_out_forecast), clamped to 1–5 h) | calendar, forecast |
| P2 | Optimal start/stop | Simulates its own (perturbed) model forward with the forecast and starts the recovery as late as still reaches 20.0 at the occupancy start with a 0.3 K margin; coasts down in the last hour of occupancy if the model says comfort holds | calendar, forecast, model |
| P3 | Price-aware within band | P2's constraints, plus a 24–36 h plan on published prices. It searches setpoint plans over 15-minute steps (targets in 0.5 degC steps inside the mode band, within the interlock's rate) and picks the lowest expected spot cost meeting comfort with margin. Typical results: heat to 22.0 in cheap hours before an expensive block, coast toward 20.0 in it, and move setback recovery into cheap hours. Falls back to P2 when prices are not yet published, when the forecast is older than 30 h, or when the forecast missed the last 6 h by more than 3 K | prices, forecast, model |
| P4 | Peak-aware (later) | P3, plus a monthly peak-power objective for the grid tariff's power charge (effektledd; mean of the three highest hourly peaks on separate days) | needs the tariff table (ADR-005 D-13) |

Every proposal from P1–P3 carries:
- the mode;
- the priority level that chose it;
- the expected cost and comfort of the chosen plan and of the next-best rejected plan;
- the price and forecast snapshot SHA-256 values it used.

That is the "why" column of the decision ledger (ADR-005).

## Heating cables (varmekabel): twin extension

Ramps and gutters get heating-cable circuits as a new load:
- fixed rated power, e.g. ramp 6 kW and gutter 3 kW;
- a new tool, `set_heating_cable_mode(circuit, mode ∈ {auto, on, off})`;
- a twin icing-risk state: outdoor −6 to +2 degC **and** precipitation or snowfall
  in the last 3 h or the next 3 h, from the verified weather snapshot, which
  gains `precipitation` and `snowfall`.

| Rule | Behaviour |
|---|---|
| C1 (baseline) | `auto`: on while icing risk, off otherwise; what a moisture and temperature controller does |
| C2 (price-aware) | C1, but the ramp circuit may defer a start by at most 1 h into a cheaper hour, only when no precipitation is forecast during the deferral; never during active precipitation; the gutter circuit never defers |
| Interlock | refuses `off` during icing risk; an expired deferral turns the circuit on |

**Hazard metric (oracle):** icing-risk hours with the circuit off. This is a
safety cost no saving can offset; any non-zero value is reported first.

## Savings, honestly

Savings follow ADR-005, now per preset: P0 under the same episode, inputs and
seeds is the reference. A table reports P1–P3, each under arm A (ungoverned)
and arm B (REMORA, careful and rubber-stamp reviewers). It has four columns:
- spot NOK;
- kWh;
- comfort degree-hours;
- hazard hours.

Beside it: the perfect-foresight bound and the model-error setting. The first
expectation, from the physics, is that most of the saving comes from setback
and optimal start (P1/P2), and a smaller part from price shifting (P3),
limited by the 22 degC ceiling and 1 K/h of reheat. If P3 shows more, that
is a reason to look for a bug before it is a result.

## How REMORA governs a strategy that acts every 15 minutes

A human reviewing every step is not realistic. The intended model is plan
approval:

1. Once a day the strategy proposes a **day plan**: modes and setpoint windows
   per zone, with their reasons. A reviewer approves it as a signed work order.
2. Each 15-minute step is then a governed call bound to that work order: its
   zone and value must fall inside the plan's window. REMORA's semantic
   grounding marks the value grounded because the plan states it, and its
   limits cap the cumulative movement.
3. An off-plan step (another zone, outside the window, no plan) goes to review
   or is refused.

Whether the pinned REMORA can ACCEPT plan-bound steps without a per-step
review is **unverified**. Today every write is routed to VERIFY
(`schema_unverified_verify`, `evidence_insufficient`, setpoint ungrounded).
Settling that is a spike against REMORA's semantic bundle and intent source
(D-16). A REMORA change it needs belongs upstream (INV-11).

## Owner decisions

| ID | Decision | Recommended default |
|---|---|---|
| D-15 | Mode bands and the widened bounds | The table above; the interlock's mode rule replaces the static 19.0 floor; signed by the owner |
| D-16 | Plan approval as the autonomy model | Yes. A spike first: can REMORA ACCEPT plan-bound steps, and what must change upstream? |
| D-17 | Forecast source for replay and live | Open-Meteo previous-runs for replay; MET Norway locationforecast for live, snapshotted per decision |
| D-18 | Model-error setting for headline figures | ±20% capacity and UA, three seeds, reported as a range |

## Consequences

- The presets are deterministic and explainable. Each proposal says which
  rule chose it and what the alternative would have cost.
- Lighting and ventilation savings are not claimed until the twin models them.
- The heating-cable extension adds the first hazard metric that a cost
  optimizer could worsen. That is exactly the case REMORA and the interlock
  have to show they hold.
