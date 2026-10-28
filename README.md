# autonomous-ot-control

A simulation-only testbed for **governed autonomy in building automation**.
A digital caretaker proposes bounded HVAC, lighting, tank and heating-cable
actions for a simulated building; [REMORA](https://github.com/darklordVirtual/REMORA-research)
authorizes or refuses each one under a signed tool contract and signed
cumulative limits; a protected dispatcher sends approved writes to the
simulated actuator; a separate read-only observer verifies the effect. Every
step leaves evidence that an outside project can check without trusting this
repository.

> **Scope:** `SIMULATED_CONTROLLED_PILOT`. No physical OT/BMS write path exists
> here, and simulation results never unlock a live connector (INV-12).
> Specification: [`docs/SDD-OT-001.md`](docs/SDD-OT-001.md).

| I want to… | Go to |
|---|---|
| **See results** of outside runs against the testbed | [`docs/federation/RESULTS.md`](docs/federation/RESULTS.md) (generated from run records; reviewed reports below the table) |
| **Join** with my own check, no credentials, five minutes | [Join as a federation edge](#join-as-a-federation-edge) and the [guide](docs/federation/README.md) |
| **Read the reports** | Strategy A/B/C with seeded faults: [abc-2025.html](docs/results/2026-10-27/abc-2025.html) · five price areas 2025: [areas-2025.html](docs/results/2026-10-27/areas-2025.html) · governed week: [report.html](docs/results/2026-10-27/report.html) · federation A/B/C: `artifacts/abc-<run>/abc-report.html` after [`run-abc.sh`](#the-abc-report) |
| **Check what is established**, and at what evidence level | [`docs/status.md`](docs/status.md) |
| **Find the entry point for machines** | [`federation/index.json`](federation/index.json) |

## How it works

```
 caretaker (P3)        REMORA                 dispatcher            twin                 observer
 proposes one   ───▶   signed ToolSpec v5,  ───▶  holds the     ───▶  interlocks     ───▶  read-only,
 governed tool         signed limits,            actuator            IL-01..IL-11,        own token,
 call with its         single-use ids,           credential,         single-use           records the
 decision record       audit chain               exactly once        operation ids        effect
        │                                                                                   │
        └──────────────── evidence: decision, envelope, twin operation record, observation ─┘
```

<details>
<summary>The four properties the suite proves, and where</summary>

- **Ten supervisory write tools**, all governed, each with declared bounds,
  target and postconditions ([`contracts/ot-toolspec-v1.json`](contracts/ot-toolspec-v1.json),
  [`docs/datapoints.md`](docs/datapoints.md)).
- **Three credentials, three networks.** The proposer never holds the actuator
  credential or a route to it; only the twin and REMORA's dispatcher do
  ([trust boundary](docs/running.md#trust-boundary)).
- **Refusals are the product.** Replay, tampering, out-of-contract calls,
  budget overruns, interlocks, a stopped plant and a database outage all end
  with no effect, and the suite proves it ([status](docs/status.md)).
- **Real inputs, synthetic plant.** Verified 2025 Nord Pool prices and ERA5
  weather for all five Norwegian price areas drive a deterministic 3-zone
  twin, with Statsbygg PA 0802 (TFM) naming for every data point
  ([`docs/tfm.md`](docs/tfm.md)).

Design records: [`docs/adr/`](docs/adr/) (ADR-003..010).

</details>

## Join as a federation edge

Your own check, in your own code, run against what the testbed records. An
edge never receives a credential, never reaches the plant, and can only add
refusals, never permissions. Verification is not endorsement, in either
direction.

```bash
python -m runner.edge_check --init <your-edge-id> --project "<name>" --maintainer <github-handle>
./deploy/scripts/run-edge.sh <your-edge-id> --local   # stdlib only, no Docker
./deploy/scripts/run-edge.sh <your-edge-id>           # sandboxed container, exactly as CI
```

Then open a pull request adding `federation/edges/<your-edge-id>/`, your run
record, and your entry in [`docs/federation/edge-registry.yaml`](docs/federation/edge-registry.yaml).
That pull request is your consent record. A bounded report without an
adapter is also welcome; see the [guide](docs/federation/README.md) and
[CONTRIBUTING.md](CONTRIBUTING.md). Questions: an issue here, or
aeoess/agent-governance-vocabulary#177, the discussion that motivated this.

<details>
<summary>Boundaries you can join at</summary>

| Boundary | Mode | Question your edge answers | Today |
|---|---|---|---|
| `effect_evidence` | offline | Is each claimed effect backed by the decision, the twin's own record, the observation and the evidence digest? | open, auto-scored |
| `corpus_adequacy` | offline | Which seeded faults would a checker miss? (mutate the reference verifier over the fixture set) | open, report reviewed |
| `runner_validity` | offline | Does the scorer report a failing verifier as a failure, never a pass? | open, report reviewed |
| `admission_runtime` | inline | `permit` or `refuse` before dispatch, with a reason; consulted only after REMORA and the reviewer approved, can only add refusals | open: harness in the governed demo (`--inline-edge`), reference edge `federation/edges/_inline_template`, scored in the A/B/C report |
| `tool_definition` | inline | Does the served tool definition match the approved one? | no harness yet. Planned next, against the signed ToolSpec bundle REMORA pins by digest (no MCP gateway needed): the harness hands the edge the served definition and the pinned digest per call, same shape as AgentAvow's `tool_manifest_digest` (E8) |
| `authority_fixture` | inline | What does a synthetic signed work order authorize? | no harness yet. Blocked on a signed work-order format: the testbed's work orders are intent-table entries, not signed artifacts. Planned after `tool_definition` with the testbed's own Ed25519 signer as the interim format and the episode's work orders as the oracle; a federation authority format (APS) is preferred when one is pinned |

</details>

<details>
<summary>What you get, pinned and offline</summary>

- **Sealed fixture sets**: `v2` (default) contains 12 positive decision
  trails and 10 negative controls; frozen `v1` retains 12 and 6. Each negative
  control contains one seeded fault. The trails come from a real governed
  run. The manifest names six claims, maps every fault to the claim it
  contradicts, lists every case file with its SHA-256 and a package digest
  ([fixtures schema](contracts/federation-fixtures-v1.schema.json)).
- **Contracts**: one verdict per case, `ESTABLISHED` / `CONTRADICTED` /
  `NOT_ESTABLISHED` ([result schema](contracts/edge-result-v1.schema.json));
  an edge manifest with your pin, licence, claims and **claim ceilings** in
  your own words ([manifest schema](contracts/edge-manifest-v1.schema.json));
  the digest rules written out, so no one has to import our code.
- **A reference verifier** (`federation/edges/_template/`, standard library
  only) to replace or wrap, and a scorer that verifies the sealed bytes, fails
  any run that answers nothing or lets a negative control through, and writes
  a **run record** with a derived independence level, `L0_SELF_TEST` to
  `L4_INDEPENDENT_HOST_RUN` ([record schema](contracts/edge-run-record-v1.schema.json)).
  The results table is generated from the records; CI refuses a hand-edited one.
- **A machine-readable entry point**, [`federation/index.json`](federation/index.json):
  what the testbed produces and consumes, its boundaries, its claim ceiling,
  five invariants each bound to the test that enforces it, and the findings
  fixed with the revision to test against.
- External contributors keep their own authorship. External verifier source
  stays in its own repository; repeatable edges may use a public container
  image pinned by digest. One accepted exception: Probity's edge (PR #22)
  ships an unchanged, hash-verified copy of its upstream reader with its own
  licence file. An edge can declare `fixture_sets` to keep an agreed
  `v1` evaluation from being enrolled in `v2`.

</details>

## Results so far

**Runs from outside**, all on 2026-10-27, the day the kit opened:

- Probity's `agent-evidence-vectors-axes` edge: a second implementation run on
  its own host, 12/12 established and 6/6 faults caught on `v1`, derived
  `L4_INDEPENDENT_HOST_RUN` (PR #22).
- argentum-core's clean-room run of the reference verifier on `v1`,
  `L1_REPRODUCTION` (PR #25).
- Altru.dev's bounded `runner_validity` report, recorded as reviewed.

Those runs and a further negative test found three defects in the testbed,
fixed on `main` with regression tests that failed first, and listed with the
revision to test against in [`federation/index.json`](federation/index.json)
under `revisions`. Every row and every finding stays in
[`docs/federation/RESULTS.md`](docs/federation/RESULTS.md).

**On the synthetic building, 2025, five price areas:** the caretaker (P3) saves
**9–14 % of the kWh** from the HVAC and lighting levers, and the independent
validator keeps the tank out of the unsafe band the unvalidated caretaker
enters. The reports also show a far larger total (25–42 % of cost) that is
80–90 % one lever, the heating cables on an air thermostat as found; it rests
on unmeasured assumptions of the twin and is not the figure to quote. Each
report opens with what drives the number. Tables, definitions of P0–P3 and the
arms, and the reports: [`docs/strategies.md`](docs/strategies.md).

Everything in the governed path is `TESTED_DOCKER`: one host, one REMORA
replica, synthetic plant, same-deployment vantage. Real-building savings and
physical OT safety are `NOT_ESTABLISHED`. The full capability table with the
evidence level per line is [`docs/status.md`](docs/status.md); statuses are
promoted only by new evidence.

<details>
<summary>The three defects the outside runs found</summary>

- The observer accepted one applied operation's id as proof of another tool's
  or zone's effect when the desired state already existed (PR #20).
- The attestor re-decided that refusal by comparing the plant's current value,
  so the real SDK returned `EFFECT_VERIFIED` anyway (PR #21).
- A restart lost the HVAC setpoints, plant state and point history while
  keeping the revision and the operation log (PR #21).

A fourth, from a negative test of our own: the reference verifier accepted
missing and self-contradicting evidence as `ESTABLISHED`; fixture set `v2`
seals the four cases that catch it (PR #21).

</details>

## The A/B/C report

Where the edges end up. The demo week is pinned as an episode
(`scenarios/episodes/`), every proposal judged `permitted` or
`forbidden:<rule>` by an oracle written from the work orders and the plant's
limits before any arm runs. Arm A applies the stream ungoverned (interlock
only, known-negative), arm B runs it through REMORA with a declared reviewer
model, arm C adds one inline edge that is consulted after REMORA and the
reviewer approved and can only add refusals.

```bash
DAYS=7 ./deploy/scripts/run-abc.sh     # Docker: A offline; B-careful, B-rubber_stamp, C with the reference inline edge; report
```

`runner.ab_compare` accounts every arm against the oracle, runs every offline
edge beside every arm, and reports what each edge added: refusals of
forbidden proposals, false refusals, misses, findings on real trails, and its
record on the sealed sets. That is how edges are compared. What exists, what
was run, and what none of it establishes:
[`docs/federation/ABC-ROADMAP.md`](docs/federation/ABC-ROADMAP.md).

## Licence

The code and documents written for this repository are **Apache-2.0**
([LICENSE](LICENSE), [NOTICE](NOTICE)). That grant stops at what this
repository's authors wrote:

- **REMORA and its SDK are not covered.** REMORA is BUSL-1.1 with its own
  Research Grant, is not included here, and is fetched at build time at the
  pinned commit. Running the governed path uses REMORA under REMORA's licence.
- **Contributions from other projects keep their own licence and copyright**,
  for example the Probity edge (Apache-2.0, its own licence file).
- **Data and documents from third parties keep their terms:** Open-Meteo/ERA5
  weather (CC BY 4.0 with attribution), day-ahead prices republished by
  hvakosterstrommen.no, Statsbygg's PA 0802 code lists, Enova's
  energy-certificate data, and one building's meter aggregates published with
  its owner's consent.

Every item, its licence, attribution and what it does not cover:
**[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)**.

<details>
<summary>Quick start, layout, upstream pin and security</summary>

### Quick start

```bash
./deploy/scripts/run-demo.sh        # Docker: build, smoke, baseline, evidence in artifacts/
./deploy/scripts/run-governed.sh    # Docker: REMORA in the loop, acceptance suite, evidence
python -m pytest -q                 # no Docker: unit and integration tests
```

Requires Python 3.11+ and, for the Docker paths, Docker Compose v2. Governed
demo, CLI proposals, the A/B/C runs, trust boundary and the upstream pin:
[`docs/running.md`](docs/running.md).

### Layout

```
twin/        model, plant (HVAC/tank/HP/AHU/lighting/cables), tfm (PA 0802), datapoints, guard, api, effect_observer
adapter/     REMORA-free helpers: contracts (signed ToolSpec), actuation (effect primitives), verifier
deploy/ot/   modules loaded inside REMORA: ot_registry, ot_bundle, site_policy, caretaker_fallback, limits, signer
runner/      caretaker (P3), strategies (P0..P2), areas, demo, acceptance; federation: edge_check, federation_fixtures,
             edge_pass, episode, oracle, ledger_metrics, arm_a, admission, ab_compare
federation/  index.json (entry point), edges/_template and edges/_inline_template, external edges, sealed fixtures/v1
             (12 + 6) and fixtures/v2 (12 + 10), records/ (run records), reports.json (reviewed reports)
contracts/   ToolSpec, tool surface, caretaker profile catalog, federation edge/case/result/run-record/fixtures/index,
             episode (ot-episode/v1), MCP OpenAPI
scenarios/   verified 2025 price and ERA5 weather series for NO1..NO5, baseline day, refusal vectors, episodes/
docs/        SDD-OT-001, status, strategies, running, datapoints, tfm, federation/, results/, adr/
upstream/    pinned REMORA commit (123a5fd0)
```

### Upstream and security

- REMORA is consumed at commit `123a5fd0` ([`upstream/remora.lock.json`](upstream/remora.lock.json));
  nothing upstream is patched locally. REMORA is BUSL-1.1 with its own Research
  Grant and is not relicensed by anything here.
- Simulation only. Report a bypass of the actuator boundary privately as
  described in [`SECURITY.md`](SECURITY.md).

</details>
