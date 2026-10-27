# ADR-007 — Building-services plant, dependencies, interlocks and governed tools

- Status: Proposed. Slice A (plant physics, catalogue, generated documentation) is implemented. Slice B (actuator routes, interlocks, REMORA ToolSpecs for the new tools) is designed here
- Date: 2026-10-27
- Related: ADR-004 (A/B/C), ADR-005 (data, savings, ledger), ADR-006 (strategies); `docs/datapoints.md`; INV-03, INV-05, INV-07

## Context

A testbed that only moves one electric heater per zone can show that REMORA
gates a write. It cannot show that REMORA governs a building.

Real buildings couple their systems:
- a zone setpoint draws a shared tank, and the tank starts a heat pump;
- a demand-response cap can start a backup heater that costs more than it saves;
- dimming lights in winter returns part of the saving as radiator heat;
- stopping ventilation is not allowed while people are present.

Some goals need several adjustments in a fixed order. Others are harmless
alone and damaging together. To test REMORA and measure savings credibly, the
simulator needs these couplings, and every value it exposes must be documented
well enough that a reviewer can check the physics.

## Decision 1 — a second model, `three-zone-hvac-v1`

The original `three-zone-rc-v1` stays as it is, so PR #3's evidence remains valid.
The new model keeps the same three zones and envelope and replaces the zone
heater with a plant (`twin/plant.py`), summarised in the table below. Variants
share one config, `twin/fixtures/three-zone-hvac-v1.json`, and are selected by
`plant.heat_source`.

| System | Model | Main simplification, stated |
|---|---|---|
| Radiators | EN 442 characteristic, n = 1.3, relative to 70/55/20; valve proportional over 1 K | return temperature = supply − design ΔT, not flow-dependent |
| Supply water | weather-compensated curve (60 °C at −20, 30 °C at +15), never above the tank | one curve, no room-temperature feedback |
| Buffer tank | 1 500 l, fully mixed, standing loss 4 W/K | no stratification |
| Heat pump (variant) | air-to-water, COP = 45 % of Carnot (clamped 1–6), capacity −2.5 %/K below A7, defrost −10 % in −7..+5 °C at RH ≥ 75 %, on/off with 3 K hysteresis, 10 min min-run and min-stop, high-pressure cut-out, 9 kW backup immersion heater | on/off compressor, not inverter; defrost as a penalty, not cycles |
| District heating (variant) | substation 98 %, up to 40 kW, spot-linked energy tariff | tariff is an ASSUMPTION until D-13 |
| Electric boiler (variant) | resistive, up to 30 kW | — |
| AHU | one unit, rotary heat recovery 80 % (×0.75 below −15 °C), free-cooling bypass, heating and cooling coils to a supply-air setpoint, fan power = SFP 1.5 × airflow | no humidity, no pressure model |
| DCV | material minimum 0.35 l/s·m², 7 l/s per person, boost to the maximum over 200 ppm above the CO₂ setpoint | proportional, no damper dynamics |
| CO₂ | well-mixed balance, 18 l/h per person, 420 ppm outdoors | — |
| Cooling | chilled beams per zone, AHU cooling coil, chiller with EER = 45 % of Carnot (clamped 2–7) | condenser 10 K above max(outdoor, 15) |
| Lighting | daylight = daylight factor × 110 lm/W × irradiance; electric light tops up to the setpoint (500 lux at full, 10 % power at minimum dim); all light power is heat | horizontal irradiance, no window geometry |
| Equipment and people | W/m² occupied plus a base load (the lab runs 25 W/m² around the clock, so it needs cooling in January); 75 W sensible per person | — |
| Heating cables | ramp 6 kW, gutter 3 kW; `auto` follows the icing-risk state | — |

Every component form is pinned by `tests/unit/test_plant.py`. The tests cover:
- the COP and capacity trends;
- the compressor timers, cut-out, backup and defrost behaviour;
- tank energy conservation for all three heat sources;
- the electric meter equal to the sum of its consumers;
- SFP × airflow, and CO₂ settling at its mass balance;
- heat recovery and its frost limit, and daylight dimming;
- winter lab cooling, and the cable hazard.

Writing these tests found one real bug: the radiator supply was capped by the
tank setpoint rather than the tank. Writing the catalogue found another: a
heat-pump point was emitted in buildings without a heat pump.

Two consequences:
- Comfort is now an outcome of the plant, not of a heater that always delivers.
- The reference schedule (P0) on the verified Trondheim week costs 1 164.68 NOK
  (872 kWh) and leaves 0.48 degree-hours outside the comfort band. The v1 twin's
  idealized heater did not.

## Decision 2 — two control layers

| Layer | Who writes it | Examples |
|---|---|---|
| Local loops | the plant itself, like a PLC; no tool reaches them | radiator valves, compressor hysteresis and timers, coils, DCV dampers, daylight dimming, cut-outs, backup heater |
| Supervisory setpoints | a strategy, **only** through REMORA's governed tools, through the twin's interlocks | zone heating, cooling, CO₂ and lux setpoints; supply air; tank; heat-pump cap; AHU on/off; cable mode |

An agent never writes a valve position or starts a compressor. It moves the
targets a building operator would move. The plant's own protections (minimum
run time, high-pressure cut-out) act whatever the agent asks, as they do in a
real plant.

## Decision 3 — one catalogue, generated documentation

`twin/datapoints.py` is the single source for every point, interlock, tool,
procedure and cascade. `python -m twin.datapoints --write` renders
`docs/datapoints.md` and `twin/fixtures/datapoints-v1.json`.

Each point records:
- id, system and kind;
- unit and plausibility range;
- description and the source of its value;
- the points it directly affects;
- the heat-source variants it exists in;
- the tool that writes it, with its interlocks.

`tests/unit/test_datapoints.py` fails when:
- the plant emits an uncatalogued point, or the catalogue lists a measured point the plant never emits;
- a point is emitted in a variant it is not declared for;
- a dependency edge, cascade step or interlock reference names an unknown point;
- a writable point lacks a tool or an interlock;
- an implemented tool's schema differs from the signed contract;
- the rendered files are stale.

Every interlock, tool and procedure has a status:
- `IMPLEMENTED` means the twin enforces or serves it today. That is the zone
  heating tool, its 19 °C floor and its rate limit.
- `SPECIFIED` means it is designed and not yet built.

The documentation therefore cannot claim more than the code does, and the test
pins that only `set_zone_setpoint` is implemented today.

## Decision 4 — dependencies, interlocks and procedures

**Cascades (CS-01..07)** are documented chains from a supervisory change to
its downstream effects. Each states the consequence a strategy must account
for. They are also the oracle's reference for "this change caused that cost"
in the decision ledger (ADR-005).

**Interlocks (IL-01..11)** protect the plant and its users. They are
cross-point rules, not only per-value bounds:
- deadband between heating and cooling setpoints;
- comfort floor while occupied;
- an 8 °C frost floor;
- tank setpoint range and rate;
- supply-air range;
- no AHU stop while occupied;
- CO₂ and lux bounds;
- no compressor cap while the tank is short (the backup-heater cascade);
- no cable off in icing risk;
- rate limits.

They live in the **twin's interlock**, the local protection layer that holds
even if middleware is compromised (INV-07). Where a rule is static, it is also
in the signed ToolSpec, so REMORA refuses it at assess. REMORA never replaces
an interlock. It refuses earlier and records why.

**Procedures (PR-01..05)** are the multi-step changes, with their order and
their verification steps. Examples:
- raise the cooling setpoint before the heating setpoint approaches it;
- pre-charge the tank before capping the compressor, and release the cap
  before the backup heater would start;
- set the supply air before restarting the AHU.

A procedure is executed as a sequence of individually governed calls under one
approved plan (ADR-006, D-16). A step that would leave the plant in an unsafe
intermediate state is refused by an interlock, so getting the order wrong costs
a refusal, not damage.

## Decision 5 — ToolSpec structure for slice B

Each new tool (see `docs/datapoints.md`, Governed tools) becomes all of:

1. a REMORA ToolSpec in the signed bundle (`deploy/ot/sign_deployment.py`),
   generated from the catalogue. It carries:
   - the argument schema with exact bounds;
   - `allowed_targets`;
   - the risk tier;
   - a semantic contract (capability, effect, resource type, argument roles);
   - the closure digest;
   - a description that names the cascade;
2. a twin actuator route on the plant-private listener, with the same
   single-use operation id, request digest and revision binding as today;
3. its interlocks in `twin/guard.py`, evaluated against live plant state;
4. a meter in the cumulative-limits policy where movement accumulates: heating
   and cooling setpoints, supply air, tank;
5. acceptance cases: every interlock has a refusing case, and every procedure
   has an in-order case and an out-of-order case that must be refused with
   zero effect.

The ToolSpec bundle and the interlocks are generated from or checked against
the catalogue, so a bound cannot differ between REMORA, the twin and the
documentation.

## Consequences

- The simulator can now produce the couplings that make savings non-obvious.
  The backup-heater cascade (CS-02) is the clearest case of an "optimization"
  that loses money, so it belongs in the A/B/C classes as `self_defeating_cap`.
- Lighting and ventilation savings are now measurable, together with their
  side effects: radiator heat replacing lighting heat, and CO₂ rising.
- Everything stays synthetic and uncalibrated: comparisons between strategies on
  the same inputs, never a real-building saving.
- Slice B multiplies the governed surface from one tool to nine. Each needs
  its interlock and acceptance cases before it is `IMPLEMENTED`.
