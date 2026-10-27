# ADR-005 — Site, verified price and weather data, savings in NOK, and the decision audit trail

- Status: Proposed. Slice 1 (site and verified data) is implemented in this PR; savings and the audit ledger are designed here and built next
- Date: 2026-10-27
- Related: SDD-OT-001 §5 (exogenous series, environmental result), §6, §12; ADR-004; INV-05, INV-08

## Context

The twin's weather and electricity price were parametric curves. A pilot that
steers heating by price needs real price structure (hourly spot prices with
their actual peaks) and weather for the same place. Two more things are needed
to show value: savings in kroner and øre that can be traced to REMORA's
actions, and a decision trail that shows, for every change, why it happened.

## Decision 1 — a fictitious site at a real coordinate

`twin/fixtures/site-demo-building-001.json` (`ot-site/v1`) gives the
fictitious `demo-building-001` a WGS84 point in Trondheim (63.4182 N,
10.4051 E). It names no real building, owner or address. The file declares the
price area (`NO3`, Midt-Norge) and the timezone (`Europe/Oslo`).

The area is declared, not derived. Bidding-zone boundaries are not modelled
here. `runner.exogenous` checks the coordinate against a coarse box for the
declared area, so a mistyped coordinate or area fails loudly. That box is a
consistency check, not a bidding-zone polygon.

## Decision 2 — verified snapshots, never a live feed in a run

`python -m runner.exogenous fetch --site … --from … --to … --out …` writes one
`ot-exogenous-snapshot/v1` file per kind:

| Kind | Source | Upstream | Unit | Step |
|---|---|---|---|---|
| `price` | hvakosterstrommen.no `/api/v1/prices/{y}/{mm-dd}_{area}.json` | ENTSO-E / Nord Pool day-ahead result | NOK/kWh, spot only | 1 h |
| `outdoor` | Open-Meteo historical archive `temperature_2m` | ERA5 reanalysis at the nearest grid cell | degC | 1 h |

Each snapshot records:
- every request URL and the SHA-256 of its raw response;
- the retrieval time;
- the attribution;
- its limitations, stated in the file.

A scenario takes a series with `{"series_ref": path, "sha256": hex}`. The twin
(`twin/series.py`) refuses a file whose bytes differ, a series that does not
cover the span, a gap, a non-finite value or a wrong unit. A run therefore
never falls back to a curve, never fills with zero, and never reads the
network. The scenario digest that every evidence bundle pins includes those
SHA-256 values, so the data is bound into the evidence.

`python -m runner.exogenous verify <snapshot>…` is the verify path. It
re-fetches every recorded URL and reports `VERIFIED_IDENTICAL` (same bytes),
`VERIFIED_EQUIVALENT` (same values; Open-Meteo stamps its generation time into
the body), `DIVERGED` or `UNREACHABLE`. Only the first two exit 0.

The first committed snapshot is 2026-01-15 to 2026-01-21 for NO3:
- 168 hourly prices, 0.71 to 2.84 NOK/kWh;
- 168 temperatures, -5.2 to 4.3 degC.

Prices re-fetched byte-identical and weather value-identical on 2026-10-27.
The scenario is `scenarios/trondheim-week-2026w03-v1.json`.

What this does **not** establish, stated in the snapshot files:
- **One source per kind.** A re-fetch shows the source still says the same
  thing. It does not show the source is right. Corroboration by ENTSO-E
  Transparency, which needs an API token, is `NOT_ESTABLISHED` (D-12).
- **Spot price only.** No grid tariff, VAT, supplier markup or power-based
  tariff (effektledd). Savings figures are spot-cost savings.
- **Hourly as the source publishes it.** Nord Pool day-ahead has 15-minute
  products. Whether this source's hour is an aggregate of them is `UNCERTAIN`.
- **Reanalysis, not a station.** A MET Norway Frost station series needs a
  client id (D-12).

Live operation (a future price-aware agent reading tomorrow's prices) uses the
same fetch, writes a snapshot first, and decides only from the snapshot it
names. That gives every decision a data reference.

## Decision 3 — savings in NOK, attributed to decisions

The twin is deterministic, so savings come from counterfactual runs on
identical inputs, never from a model of what the operator would have done.

- **Run level:** `energy_kwh`, `energy_cost_nok` and
  `comfort_violation_degree_hours` for a reference run (the building's own
  schedule, `runner.baseline`) and for each arm of ADR-004, all on the same
  episode. Savings are the difference in NOK, to two decimals, with a comfort
  delta beside it. A saving with worse comfort is reported as both, never
  netted.
- **Decision level:** for each applied change `k`, the run is replayed with
  the same inputs and change `k` omitted. Change `k`'s attributed saving is
  `cost(without k) - cost(with all)`. Interactions mean the attributions need
  not sum to the run-level saving. The report states the residual instead of
  forcing a sum.
- **What REMORA's actions saved** is reported per arm. It is not claimed as
  REMORA's saving: REMORA authorizes or refuses, the agent proposes. The split
  that matters is (a) savings from permitted changes REMORA let through, and
  (b) cost avoided, or comfort protected, by forbidden changes it refused. The
  second comes from the arm-A counterfactual, where the same proposals ran
  ungoverned.

Every figure carries `estimate: true`, the scenario digest, the snapshot
SHA-256 values and the scope line "synthetic, uncalibrated three-zone RC twin;
spot price only". `real_building_savings` stays `NOT_ESTABLISHED`.

## Decision 4 — a full decision audit trail per change

The twin's operation id is REMORA's proposal id, so every plant effect joins to
REMORA's record without a mapping table. Each run exports
`decision-ledger.jsonl`, one row per proposal. A row covers
proposal → assessment → review → grant → dispatch → plant → observation → cost,
and holds:

- **proposal:** the call, `intent_ref`, origin (`schedule` or `injected:<class>`), and the price and temperature the proposer saw, with their snapshot SHA-256 values;
- **assessment:** decision, reasons, semantic grounding, ToolSpec hash, bundle digest, limit preview, audit `sequence_no` and `entry_hash`;
- **review:** reviewer principal, model (`careful` / `rubber_stamp`), approve or reject, reason, deferred-audit idempotency key;
- **grant and dispatch:** grant `jti`, `tool_call_hash`, outcome, and refusal reason with layer (REMORA / interlock / edge);
- **plant:** the twin's operation record (status, reason, revision before and after, setpoints);
- **observation:** the effect observer's envelope and the `record_effect` result;
- **cost:** the decision-level attributed NOK and comfort delta from Decision 3;
- **links:** REMORA's own evidence export for the proposal (`GET /v1/execution/proposals/{id}/evidence`, its hashed manifest kept verbatim), the lifecycle trail, and the tenant chain verification at the end of the run.

The ledger is derived. REMORA's audit chain and the twin's operation log stay
the records of truth. The ledger is checked against both: every row's audit
entries must exist and verify, and every APPLIED twin operation must have
exactly one row. A missing join fails the run. The same rows feed the
federation envelope of ADR-004, so an offline edge sees the same trail.

## Owner decisions this ADR needs

| ID | Decision | Recommended default |
|---|---|---|
| D-12 | Second sources | An ENTSO-E Transparency token for price corroboration and a MET Frost client id for station temperatures. Both are optional; without them the files say `NOT_ESTABLISHED` |
| D-13 | Tariff model | Spot only first. A Tensio (NO3) grid tariff and power-based tariff as a declared, versioned table later, never mixed into spot-only figures |
| D-14 | Data licence notes | Keep the attribution in each snapshot plus `scenarios/data/README.md`, and confirm the redistribution terms before snapshots are published outside this repository |

## Consequences

- Prices with real peaks give a price-aware strategy something to exploit,
  and give an attacker something to forge. ADR-004 adds `price_spoof`: the
  agent is fed a forged price and proposes accordingly. The oracle judges
  against the verified snapshot, so a change justified only by the forged
  price is forbidden.
- A savings figure always comes with its counterfactual, inputs and scope, so
  it can be recomputed by anyone with the repository.
- The ledger makes every decision reviewable after the fact. It adds one
  REMORA evidence export per proposal to each run's artifacts.
