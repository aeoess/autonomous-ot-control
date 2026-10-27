# From edges to an A/B/C report

What the final report is, what exists for it today, and what is still
missing, in the order it will be built. Nothing here is a status claim; the
evidence levels live in [`docs/status.md`](../status.md).

## Two A/B/Cs, kept apart

| | Strategy A/B/C (exists) | Federation A/B/C (ADR-004, the goal) |
|---|---|---|
| Question | What is the caretaker's validator worth when sensors lie? | What does REMORA add beyond the plant's interlock, and what does an external edge add beyond REMORA? |
| Arms | A = P0 as found, B = P3raw (no validator), C = P3 | A = ungoverned (interlock only, known-negative), B = REMORA, C = REMORA + one edge at one declared boundary |
| Workload | one year, five price areas, seeded sensor faults | one episode: seeded scenario + fixed proposal stream with injected attack classes |
| Oracle | the site policy | written into the episode before any run, from the work orders and the plant's limits table; never read by REMORA or an edge |
| Report | [`abc-2025.md`](../results/2026-10-27/abc-2025.md) | `forbidden_effects / forbidden_proposals` by oracle rule, `permitted_effects / permitted_proposals`, refusals by layer (`interlock`, `remora:<reason>`, `edge:<id>:<reason>`, `reviewer`), per arm × reviewer model |

The strategy report is finished and explains itself. The federation report
is the one the edges are for.

## What exists today

| Piece | Where | State |
|---|---|---|
| Arm B: REMORA governs the plant; every applied write leaves a decision trail with REMORA's envelope and evidence | `runner.demo`, `runner.acceptance` | `TESTED_DOCKER` (governed-e2e) |
| Decision trails export the twin's own operation record, so an offline pass needs no live stack | `runner.demo` (`twin_operation` in `decisions/<id>.json`) | in `main` |
| Offline edges: contracts, sealed sets `v1` and `v2`, scorer, run records with derived independence level, reviewed reports | `federation/`, `runner.edge_check` | `TESTED_DOCKER`; one external `L4` record, one reviewed `runner_validity` report |
| Edges beside the run: every offline `effect_evidence` edge over a governed run's trails, findings reported, outcome untouched | `runner.edge_pass`, wired into `run-demo-governed.sh` | `TESTED_LOCALLY` (synthetic trails); not yet run on a Docker demo |
| Attack injection in the governed demo (day 2: replay, tamper, wrong zone, out of contract) | `runner.demo.inject_attacks` | `TESTED_DOCKER` |

## What is missing, in build order

Each step is one pull request with its own acceptance test, and each is
usable on its own.

1. **Episodes and the oracle** (`scenarios/episodes/<id>.json`, schema
   `ot-episode/v1`). The proposal stream the demo already generates, written
   down with `origin`, `intent_ref` and the oracle verdict per proposal
   (`permitted` or `forbidden:<rule>`), derived from the work orders and the
   limits table. Acceptance: the oracle never reads REMORA's reason codes; a
   test fails if it does.
2. **Per-layer refusal accounting in the governed demo.** Every proposal in
   the ledger carries which layer refused it (`interlock`, `remora:<reason>`,
   `reviewer`) and the oracle's verdict, so B's metrics come straight from the
   ledger. Acceptance: `state_revision` delta equals counted effects.
3. **Arm A, the ungoverned known-negative.** `compose.arm-a.yaml` under its
   own project, its own twin and volumes, agent holds the actuator token and
   picks its own operation ids, evidence labelled `architecture:
   KNOWN_NEGATIVE`. Acceptance: a structural test pins that the default
   compose files never reference it (D-10).
4. **Arm C, inline admission harness.** The `admission_runtime` boundary:
   after REMORA decides, the harness asks the edge's `POST /decide`; `refuse`
   rejects the approval or leaves the ACCEPT token unredeemed, `permit`
   changes nothing, timeout or error counts as `refuse` and is reported as
   `edge_unavailable`. Acceptance: a test that an edge can never turn a REMORA
   refusal into an effect (FED-OT-001).
5. **`runner.ab_compare` and the report.** Same episode through A, B-careful,
   B-rubber_stamp, C; counts and denominators per arm, refusals by layer with
   `edge:<id>:<reason>`, plus the offline edge pass beside every arm;
   rendered as Markdown and HTML like the strategy reports, with a how-to-read
   section. Acceptance: AT-16 (exact metrics and delta, no unqualified
   savings claim); no arm is called "safer" in prose.

Steps 1, 2 and 5 need no external participant. Step 4 is where an inline edge
first changes a number; until then every external contribution is offline,
which is also the only kind the registry has so far.

## What an edge owner needs to know

- An offline edge already appears in the final report: the edge pass runs it
  over every arm's trails and lists its findings beside the arm's numbers.
- An inline edge will be asked one question per proposal, with a 2 s budget,
  and can only add refusals. Its refusals are counted under `edge:<id>` with
  the reason it gave, and its false refusals of permitted proposals are
  counted too.
- Nothing in either report promotes a status: the plant is synthetic, the
  vantage is one deployment, and verification is not endorsement.
