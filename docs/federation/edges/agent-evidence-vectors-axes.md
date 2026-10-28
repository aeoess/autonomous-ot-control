# Probity's edge: how it is used and where its numbers are

`agent-evidence-vectors-axes`. Maintainer: Sankalp Gilda
([@astrogilda](https://github.com/astrogilda), Probity AI). Joined by
[PR #22](https://github.com/darklordVirtual/autonomous-ot-control/pull/22),
which is the consent record. Licence: Apache-2.0, see
[THIRD_PARTY_LICENSES.md](../../../THIRD_PARTY_LICENSES.md) §5.

This page is the maintainer's account of how the testbed uses the edge. The
edge's own files are in
[`federation/edges/agent-evidence-vectors-axes/`](../../../federation/edges/agent-evidence-vectors-axes/),
including its `run.md`, `edge.json` and `adapter.py`. This page changes
nothing there.

## What it is, and what it is not

- **An offline `effect_evidence` verifier.** It reads a recorded trail
  *after* the effect: the proposal, REMORA's decision, the dispatch, the twin's
  operation record, the observation and the evidence digest. It answers
  `ESTABLISHED`, `CONTRADICTED` or `NOT_ESTABLISHED` per trail, against six
  claims:
  - `decision_correspondence`
  - `single_dispatch`
  - `call_binding`
  - `declared_effect_observed`
  - `observation_after_dispatch`
  - `evidence_digest_integrity`
- **Not in the control loop.** It never sees a credential, never reaches the
  plant, and cannot stop or permit an action. It is not an A/B/C arm, so B
  versus C does not measure it. Its own measurement profile is ADR-011 §9
  ([draft PR #28](https://github.com/darklordVirtual/autonomous-ot-control/pull/28)).
- **Not an external service.** Nothing calls Probity's API. The edge runs
  locally and offline, in a network-less sandbox in CI. It uses an unchanged
  copy of the upstream `invocation_axes/reader.py` from
  `agent-evidence-vectors==0.28.0`. The adapter checks that copy's SHA-256
  before loading it. One run answers a whole set of trails.
- **Work is split between Probity's reader and the testbed's adapter.**
  - The reader judges `decision_correspondence`,
    `declared_effect_observed` and `evidence_digest_integrity`.
  - The adapter adds `single_dispatch`, `observation_after_dispatch` and
    `call_binding`, plus the maintainer's hardening described in `run.md`.
  - All six are reported under the edge's name. Of those, only the first
    three are Probity's code.

## Where it is used

| # | Use | Input | Runs when | Output |
|---|---|---|---|---|
| 1 | Sealed evaluation | `federation/fixtures/v1`: 12 positive and 6 seeded-fault cases. **v1 only**: the edge declares `"fixture_sets": ["v1"]`, so v2 runs only if its maintainer opts in. | The maintainer's own external run (the record), or anyone with `run-edge.sh` | A run record and a row in `RESULTS.md` |
| 2 | CI | Same as 1 | `.github/workflows/federation-edge.yml`, on a pull request that changes the edge's files. Records and the results table are checked on every pull request. | CI log and the `edge-reports-*` artefact |
| 3 | Beside a governed run | Every applied `set_zone_setpoint` trail a governed arm exported (`decisions/`) | `run-demo-governed.sh` (`runner.edge_pass`) and `run-abc.sh` (`runner.ab_compare` calls `runner.edge_pass` for each arm with trails) | `edges.json`, `edges.md`, `usage.json` per arm, and `usage-summary.md` per A/B/C run |
| 4 | Maintainer tests | Synthetic trails and v1 mutations | `pytest` | Pass or fail |

### The rule for use 3: recorded inputs only

The pass does not make up what the edge checks. Each claim's input comes from
a record written when it happened:

| Claim | Source |
|---|---|
| `decision_correspondence` | ledger row: REMORA's decision and the reviewer |
| `single_dispatch` | REMORA's lifecycle: number of `execution_result` events |
| `call_binding` | the twin's operation record |
| `declared_effect_observed` | REMORA's `effect_verification` readback (`vantage same_deployment`) |
| `observation_after_dispatch` | the observer's sealed record (`sim_time_s`), exported since 2026-10-28 |
| `evidence_digest_integrity` | the digest captured in `ledger.jsonl` at export, otherwise REMORA's per-section export manifest |

A missing record makes that claim `NOT_ESTABLISHED`. A record that
contradicts itself or its capture digest makes it `CONTRADICTED`. Every
result is reported three ways:

- the edge's own answer;
- the answer on recorded inputs only;
- the answer under **strict vantage**, which also requires an independent
  observation. Every observation here is `same_deployment`, so strict
  vantage never establishes a trail in this testbed.

## Where the numbers are

| What | Where | Committed? |
|---|---|---|
| The external v1 result: 12/12 positives, 6/6 faults, `L4_INDEPENDENT_HOST_RUN` from the declared operator and host (no public run URL) | [`federation/records/agent-evidence-vectors-axes/20261027T171310Z.json`](../../../federation/records/agent-evidence-vectors-axes/20261027T171310Z.json) | yes, never edited |
| The results table (generated from the records; CI refuses a hand-edited one) | [`docs/federation/RESULTS.md`](../RESULTS.md) | yes |
| Machine-readable entry: pin, licence, agreed sets, best record | [`federation/index.json`](../../../federation/index.json), under `registered_edges`; kept in step with the registry, `edge.json` and the record by `test_registered_edges_match_registry_manifests_and_records` | yes |
| Registry and consent | [`docs/federation/edge-registry.yaml`](../edge-registry.yaml) | yes |
| A local re-run on a sealed set (your own run is `L2`, not a new `L4`) | `artifacts/edges/agent-evidence-vectors-axes/<timestamp>/`: `report.md`, `result.json`, `score.json`, `run-record.json` | no (`artifacts/` is ignored) |
| Verdicts on a governed run's trails | `artifacts/<run>-work/edges/edges.json` and `edges.md`; in an A/B/C run, `artifacts/<run-id>/edges/<arm>/` | no |
| Use in one pass: one verifier run, exit, runtime, cases sent and processed, verdicts, findings by case digest, agreement with the reference verifier, all tied to the edge's version digest, the input digest and the run id | `usage.json`, next to `edges.json` | no |
| Use across passes, per edge and contributor | `python -m runner.edge_usage --root <dir>` prints it; `--out` writes `usage-summary.json` and `.md` (`run-abc.sh` does this per run) | no |
| End-to-end check of the observer export on Docker (14 new trails) | [`docs/results/2026-10-28/observer-e2e-edges.md`](../../results/2026-10-28/observer-e2e-edges.md) and `.json` | yes |
| Mutation scores (case and pipeline layer) and benchmark | [`docs/results/2026-10-28/edge-mutations.md`](../../results/2026-10-28/edge-mutations.md) and `.json`; regenerate with `python -m runner.edge_mutations` | yes |

### How to read the numbers

- **Use is not value.** `verifier_runs` and `cases_processed` count work
  done. A repeat of the same input on the same edge version adds use, but no
  new distinct cases or findings.
- **Confirmed, unique and false findings need a ground truth.** On live
  trails they are `null`. On the sealed sets, the run record counts them.
  - On v1, this edge and the reference verifier each caught all six seeded
    faults, so the edge has **no unique findings on v1**.
  - What a second implementation adds is reproduction: the same verdicts from
    independently written code, reported as agreement (n compared, n agreed).
- **A finding only this edge makes** on a live trail is listed as a candidate
  for review (`edge_only_findings`), never as a catch.

## Check that it works

Run these from the repository root. Each was run on 2026-10-28, and the
result is shown.

```bash
# 1. Sealed v1, local (Docker sandbox without --local)
./deploy/scripts/run-edge.sh agent-evidence-vectors-axes v1 --local
#    Result: PASS; caught 6/6 by fault and 1/1 per claim; level L2_SECOND_IMPLEMENTATION (a local re-run)

# 2. Strict vantage on v1
python federation/edges/agent-evidence-vectors-axes/adapter.py run \
  --fixtures federation/fixtures/v1 --out /tmp/axes-strict.json --strict-vantage
#    Result: 12 positives NOT_ESTABLISHED (same_deployment observation), 6 negatives CONTRADICTED

# 3. Agreed fixture sets
python -m runner.edge_check --list-fixture-sets federation/edges/agent-evidence-vectors-axes
#    Result: v1

# 4. Records and results table
python -m runner.edge_check --check-records && python -m runner.edge_check --check-results
#    Result: 4 record(s) valid; RESULTS.md matches

# 5. Beside a governed run's trails, and its use
python -m runner.edge_pass --work artifacts/<run>/B-careful --out /tmp/pass/edges
python -m runner.edge_usage --root /tmp/pass
#    Result on abc-20261027T204956Z B-careful: 44 trails, one run each, about 100-170 ms,
#    agreement with the reference verifier 44/44, 0/44 established (see below)

# 6. Tests
python -m pytest -q tests/unit/test_agent_evidence_vectors_edge.py tests/unit/test_edge_pass.py \
  tests/unit/test_edge_usage.py tests/unit/test_federation_runner_contract.py tests/unit/test_ab_compare.py \
  tests/unit/test_edge_mutations.py
#    Result: 62 passed


# 7. End to end on Docker, then mutations and a benchmark on the new trails
DAYS=2 ./deploy/scripts/run-demo-governed.sh        # DAYS=1 has no attacks, so the demo exits 1 (see below)
python -m runner.edge_mutations --work artifacts/<run>-work --out /tmp/mut --bench 100 1000 5000
#    Result on observer-e2e-20261028T012704Z: see "End to end" and "Mutations" below
```

**Why 0/44 on that run.** The trails of `abc-20261027T204956Z` were exported
before the observer's sealed record was. `observation_after_dispatch`
therefore has no recorded time, and both edges answer `NOT_ESTABLISHED` for
every trail.

- This is correct, and it is not a fault in the edge.
- The earlier 44/44 rested on values the pass had filled in itself (fixed in
  `runner.edge_pass` on 2026-10-28).
- A governed run made after that change exports the record. See "End to end"
  below for the Docker check.

## End to end on Docker (2026-10-28)

Run `observer-e2e-20261028T012704Z` covered one simulated day of the governed
demo on Docker (REMORA in the loop, careful reviewer). The chain is: REMORA →
execution in the twin → effect observer → exported trail → both edges →
report.

- **Both edges established all 14 new trails on recorded inputs.** Every
  claim came from a recorded source.
- **Strict vantage:** 0/14 established, as expected for `same_deployment`
  observations.
- **Use:** one run per edge, under 100 ms each, with 14/14 agreement.
- **Why the demo itself exited 1.** Its attacks are injected on day 2, so a
  one-day run has none to block, and its acceptance check cannot hold. Use
  `DAYS=2` or more. The edge pass, which the script skips after a failed
  demo, was run by hand.

Details: [`observer-e2e-edges.md`](../../results/2026-10-28/observer-e2e-edges.md).

## Mutations and benchmark (2026-10-28)

`runner.edge_mutations` builds mutants from the 14 trails above. Each mutant's
expected verdict follows from its operator and the case contract, never from a
verifier, and is written before any edge runs. The mutants come in two layers:

- **Case layer:** 37 operators, 518 mutants of what an edge reads. They cover
  every claim, boundaries down to 0.0001 °C and one simulated second,
  canonicalisation traps, malformed input, and positive controls that must
  stay `ESTABLISHED`.
- **Pipeline layer:** 10 operators on the raw trail and ledger before the
  pass builds the cases. They remove or alter the evidence the pass reads:
  the observer record, REMORA's readback and lifecycle, the twin record, and
  the capture digest.

| | Reference verifier | `agent-evidence-vectors-axes` |
|---|---|---|
| Mutants killed (not `ESTABLISHED`) | 420/420 | 406/420 before the repair, 420/420 after |
| Exact verdict | 100 % | 97.3 % before the repair, 100 % after |
| Missing evidence accepted | 0 | 0 |
| False refusals of positive controls | 0/98 | 0/98 |
| Pipeline layer | 140/140 | 140/140 |
| Throughput at 5,000 cases (indicative, contended host) | about 3,000 cases/s | about 2,200 cases/s |

**Finding, now repaired.**

- The edge's adapter used to crash (`KeyError: 'observed_setpoint_c'` in `adapter.py`) on a case whose
  observation lacks `observed_setpoint_c`. A single such case stopped the whole run.
- The repair also exposed a second error that the crash had hidden: a missing observed value was compared as if
  it contradicted the claim. The contract makes such a case `NOT_ESTABLISHED`.
- Both are in the adapter, not in Probity's reader, whose digest is unchanged.
- The finding was reported to the edge's maintainer in
  [PR #22](https://github.com/darklordVirtual/autonomous-ot-control/pull/22#issuecomment-6458546497). The
  testbed maintainer repaired the adapter on 2026-10-28 and recorded the change in the edge's `run.md`.
- Every v1 verdict and reason is unchanged.
- After the repair, the same mutation set gives 420/420 mutants killed, 100 % exact verdicts, 0 missing
  evidence accepted and 0/98 false refusals, in one run of the whole set.
- There were no mutants that only this edge judged correctly. This edge's value here is independent agreement,
  not extra catches.

Details: before the repair [`edge-mutations.md`](../../results/2026-10-28/edge-mutations.md), after it [`edge-mutations-after-adapter-fix.md`](../../results/2026-10-28/edge-mutations-after-adapter-fix.md).

## What is open

- **A full Docker A/B/C run** with the new export (`DAYS=7`, a new
  `RUN_ID`).
- **More seeds and operators.** The mutation set is generated, not sealed in
  the repository: re-running it on a new governed run gives new seeds under
  the same operators. Adding an operator needs its expected verdict to follow
  from the contract.
- **A v2 run**, if Probity opts in. It would be a new record, and the v1
  record stays as it is.
