# From edges to an A/B/C report

What the A/B/C report is, what exists for it, how to run it, and what it does
not establish. Evidence levels live in [`docs/status.md`](../status.md).

## Two A/B/Cs, kept apart

| | Strategy A/B/C | Federation A/B/C (ADR-004) |
|---|---|---|
| Question | What is the caretaker's validator worth when sensors lie? | What does REMORA add beyond the plant's interlock, and what does an edge add beyond REMORA? |
| Arms | A = P0 as found, B = P3raw (no validator), C = P3 | A = ungoverned (interlock only, known-negative), B = REMORA, C = REMORA + one inline edge |
| Workload | one year, five price areas, seeded sensor faults | one episode: the demo week's proposal stream plus injected attacks, oracle written before any run |
| Oracle | the site policy | `runner.oracle`, from the work orders and the plant's limits; never from REMORA, the reviewer or an edge |
| Report | [`abc-2025.md`](../results/2026-10-27/abc-2025.md) | `abc-report.md` / `.html` from `runner.ab_compare` |

## What exists

| Piece | Where | State |
|---|---|---|
| Episodes and the oracle (`ot-episode/v1`): the P1 stream for the demo week, four injected attacks, every proposal `permitted` or `forbidden:<rule>`, inputs pinned by digest, `check` rebuilds and re-judges | `runner.episode`, `runner.oracle`, `scenarios/episodes/trondheim-week-2026w03-hvac-v1-p1-7d-v1.json` (48 proposals: 44 permitted, 4 forbidden) | `TESTED_LOCALLY`; a test fails if the oracle imports anything of REMORA's or reads a decision, reason or refusal field |
| Per-layer refusal accounting: `refused_by` (`interlock` / `remora:<reason>` / `reviewer` / `edge:<id>:<reason>`) and the oracle verdict on every ledger proposal; forbidden and permitted effects over their denominators; refusals by layer and rule; twin revision delta against counted effects | `runner.ledger_metrics`, `runner.demo.account_arm_b` (`arm-b-metrics.json`) | `TESTED_LOCALLY` |
| **Arm A**, the ungoverned known-negative: the agent applies every proposal of the episode directly, attacks included, own operation ids, only the interlock in between; labelled `KNOWN_NEGATIVE`; nothing in the default compose files or images refers to it (D-10) | `runner.arm_a`, offline | `TESTED_LOCALLY` on the committed episode |
| **Arm B**: REMORA in the loop, declared reviewer model `careful` or `rubber_stamp` (`runner.demo --reviewer`) | `runner.demo` | `TESTED_DOCKER` (careful); rubber_stamp not yet run on Docker |
| **Arm C**: the inline admission harness. After REMORA and the reviewer approved, the demo asks the edge `POST /decide` with the exact call and a context (the day plan as what it permits, the current setpoint, the declared step limit) within 2 s; `refuse` rejects the approval with the edge's reason, `permit` changes nothing, timeout, error or a malformed answer is `refuse` reported as `edge_unavailable`. The composition rule is a pure function a test pins: no answer of an edge can turn a refusal into an effect (FED-OT-001) | `runner.admission`, `runner.demo --inline-edge` | `TESTED_LOCALLY` (harness, composition rule, demo wiring); not yet run on Docker |
| Reference inline edge: a second, independently written admission check from the call and the plan alone (zone in the work order, value the work order permits, step within the declared maximum); stdlib HTTP server | `federation/edges/_inline_template/` | `TESTED_LOCALLY` over HTTP |
| Offline edges beside every arm that exported trails, each claim from a recorded source only, reported as the edge's answer, on recorded inputs, and with strict vantage. Runs every registered offline `effect_evidence` edge, today the reference verifier and Probity's `agent-evidence-vectors-axes` (PR #22) | `runner.edge_pass` | `TESTED_LOCALLY` |
| Mutation sets and benchmark for offline edges: 37 case operators (every claim, boundaries, canonicalisation, malformed input, positive controls) and 10 pipeline operators on a governed run's fully recorded trails, expectations from the contract written before any edge runs; kill rate, exact accuracy, missing evidence accepted, false refusals, per-operator fallback when an edge crashes, throughput. Results: `docs/results/2026-10-28/edge-mutations.md` | `runner.edge_mutations` | `TESTED_DOCKER` seeds (`observer-e2e-20261028T012704Z`), scored locally |
| Shadow evaluation: shadow edges asked about every proposal before REMORA, without effect, on the inline edge's context (with the fixed decision time); per arm the four-cell matrix on forbidden proposals, false refusals on both sides, unavailable answers and latency | `runner.demo --shadow-edge`, `runner.ab_compare.shadow_stats`, `SHADOW_EDGES` in `run-abc.sh` | `TESTED_LOCALLY` |
| Usage accounting per edge: `usage.json` per pass (verifier runs, availability, runtime, cases processed, findings, agreement with the reference verifier; tied to edge version, input digest and run id) and a summary per edge and contributor across passes. Use is not value: confirmed, unique and false findings stay null without a ground truth | `runner.edge_pass`, `runner.edge_usage` | `TESTED_LOCALLY` |
| **The comparison and the report**: every arm accounted against the oracle, per-rule table, refusals by layer, plant outcome per arm, inline edges by what they added (refused forbidden, refused permitted, missed, unavailable), offline edges by findings beside each arm and by their record on the sealed sets, Markdown and HTML, a how-to-read section, the non-claims | `runner.ab_compare` | `TESTED_LOCALLY`: arm A real, arms B and C synthetic ledgers in the demo's shape |
| One command for the whole thing on a Docker host | `deploy/scripts/run-abc.sh` (A offline; B-careful, B-rubber_stamp, C-rubber_stamp with the reference inline edge served on the host; compare) | written, not yet run |

## Run it

```bash
DAYS=7 ./deploy/scripts/run-abc.sh            # Docker host; ~4 governed runs plus the comparison
python -m runner.ab_compare --episode scenarios/episodes/trondheim-week-2026w03-hvac-v1-p1-7d-v1.json \
    --out artifacts/abc-local --run-arm-a     # no Docker: arm A alone, report says B and C did not run
```

The report lists the arms that actually ran; absent arms are absent rows, not
assumptions.

## Deviations from ADR-004, stated

- Arm A runs offline on the same twin rather than as its own compose project
  with an agent holding the actuator token. The numbers are the same, since
  the interlock is the twin's; the compose form would add a container and no
  information. D-10 is kept: nothing default refers to arm A.
- Arm C is run with the testbed's own reference inline edge until an external
  inline edge joins. Its contribution is therefore a demonstration of the
  harness and the accounting, not an external result.
- Of the eight oracle rules, four are exercised by the episode's injected
  attacks (`outside_work_order`, `outside_contract`, `duplicate_effect`,
  `unauthorized`). `over_authority_budget`, `not_the_approved_call`,
  `stale_basis` and `basis_not_verified_data` have rules and tests but no
  injected proposals yet (the last two need the AT-09 and ADR-005 hooks).

## What an edge owner gets from the report

- An offline edge appears beside every arm with its findings, and with its
  record on the sealed sets (highest independence level, claims established,
  negatives caught).
- An inline edge appears in arm C with exactly what it added: refusals of
  forbidden proposals that REMORA and the reviewer let through, false refusals
  of permitted ones, misses, and unavailable answers.
- Comparing edges is comparing those rows on the same episode. It is a
  statement about this episode and these sealed sets, not about an edge in
  general, and no row is an endorsement.

## What it does not establish

A synthetic, uncalibrated plant seen from inside one deployment. No arm is
called safer in prose; the counts and denominators are the result. Physical
OT safety and real-building savings stay `NOT_ESTABLISHED`.
