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

## Interoperate with the testbed

The testbed is built to be **checked from outside**. Other governance,
verification or safety projects join as an opt-in **federation edge**: your
own check, in your own code, run against what the testbed records. An edge
never receives a credential, never reaches the plant, and can only add
refusals, never permissions.

| Boundary | Mode | Question your edge answers | Today |
|---|---|---|---|
| `effect_evidence` | offline | Is each claimed effect backed by the decision, the twin's own record, the observation and the evidence digest? | open, auto-scored |
| `corpus_adequacy` | offline | Which seeded faults would a checker miss? (mutate the reference verifier over the fixture set) | open, report reviewed |
| `runner_validity` | offline | Does the scorer report a failing verifier as a failure, never a pass? | open, report reviewed |
| `admission_runtime` | inline | `permit` or `refuse` before dispatch, with a reason | specified (ADR-004), no harness yet |
| `tool_definition` | inline | Does the served tool definition match the approved one? | needs the MCP gateway |
| `authority_fixture` | inline | What does a synthetic signed work order authorize? | specified, no harness yet |

What you get, pinned and offline:

- **A sealed fixture set** `federation/fixtures/v1/`: 12 positive decision
  trails and 6 negative controls, one seeded fault each, from a real governed
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
  and five invariants each bound to the test that enforces it.

```bash
python -m runner.edge_check --init <your-edge-id> --project "<name>" --maintainer <github-handle>
./deploy/scripts/run-edge.sh <your-edge-id> --local   # stdlib only, no Docker
./deploy/scripts/run-edge.sh <your-edge-id>           # sandboxed container, exactly as CI
```

Then open a pull request adding `federation/edges/<your-edge-id>/` and your
entry in [`docs/federation/edge-registry.yaml`](docs/federation/edge-registry.yaml).
That pull request is your consent record. Verification is not endorsement: a
report states what was checked, against which pinned fixtures, and what the
fixtures do not establish (a synthetic plant, one deployment).

- Guide: [`docs/federation/README.md`](docs/federation/README.md)
- Merged runs: [`docs/federation/RESULTS.md`](docs/federation/RESULTS.md)
- The discussion that motivated this: aeoess/agent-governance-vocabulary#177

## Quick start

```bash
./deploy/scripts/run-demo.sh        # Docker: build, smoke, baseline, evidence in artifacts/
./deploy/scripts/run-governed.sh    # Docker: REMORA in the loop, acceptance suite, evidence
python -m pytest -q                 # no Docker: unit and integration tests
```

Requires Python 3.11+ and, for the Docker paths, Docker Compose v2. Governed
demo, CLI proposals, trust boundary and the upstream pin:
[`docs/running.md`](docs/running.md).

## Status and results

Everything in the governed path is `TESTED_DOCKER`: one host, one REMORA
replica, synthetic plant, same-deployment vantage. Real-building savings and
physical OT safety are `NOT_ESTABLISHED`. The full capability table, with the
evidence level per line, is in [`docs/status.md`](docs/status.md). Statuses
are promoted only by new evidence.

Over 2025, in all five price areas, the caretaker (P3) saves 25–42 % of the
as-found cost on the synthetic building, 9–14 % of the kWh without the
heating-cable lever; the independent validator keeps the tank out of the
unsafe band the unvalidated caretaker enters. Strategies, tables and reports:
[`docs/strategies.md`](docs/strategies.md).

## Layout

```
twin/        model, plant (HVAC/tank/HP/AHU/lighting/cables), tfm (PA 0802), datapoints, guard, api, effect_observer
adapter/     REMORA-free helpers: contracts (signed ToolSpec), actuation (effect primitives), verifier
deploy/ot/   modules loaded inside REMORA: ot_registry, ot_bundle, site_policy, caretaker_fallback, limits, signer
runner/      caretaker (P3), strategies (P0..P2), areas, demo, acceptance, edge_check, federation_fixtures
federation/  edges/_template (reference verifier) and fixtures/v1 (12 positive + 6 seeded-fault trails)
contracts/   ToolSpec, tool surface, caretaker profile catalog, federation edge/case/result, MCP OpenAPI
scenarios/   verified 2025 price and ERA5 weather series for NO1..NO5, baseline day, refusal vectors
docs/        SDD-OT-001, status, strategies, running, datapoints, tfm, federation/, results/, adr/
upstream/    pinned REMORA commit (123a5fd0)
```

## Upstream, security, licence

- REMORA is consumed at commit `123a5fd0` ([`upstream/remora.lock.json`](upstream/remora.lock.json));
  nothing upstream is patched locally. REMORA is BUSL-1.1 with its own Research
  Grant and is not relicensed by anything here.
- Simulation only. Report a bypass of the actuator boundary privately as
  described in [`SECURITY.md`](SECURITY.md).
- Licence for this repository: not yet decided (SDD decision `D-01`).
