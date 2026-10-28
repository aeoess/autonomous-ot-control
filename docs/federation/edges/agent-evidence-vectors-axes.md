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
  tests/unit/test_edge_usage.py tests/unit/test_federation_runner_contract.py tests/unit/test_ab_compare.py
#    Result: 59 passed
```

**Why 0/44 on that run.** The trails of `abc-20261027T204956Z` were exported
before the observer's sealed record was. `observation_after_dispatch`
therefore has no recorded time, and both edges answer `NOT_ESTABLISHED` for
every trail.

- This is correct, and it is not a fault in the edge.
- The earlier 44/44 rested on values the pass had filled in itself (fixed in
  `runner.edge_pass` on 2026-10-28).
- A governed run made after that change exports the record. That export path
  is covered by synthetic tests. It has not yet been run on a Docker stack,
  because Docker was not available on the maintainer host on 2026-10-28.

## What is open

- **A Docker A/B/C run** with the new export, to show established live
  trails end to end.
- **Seeded mutation sets of live trails.** These give a ground truth for
  confirmed, unique and false findings outside the sealed sets.
- **A v2 run**, if Probity opts in. It would be a new record, and the v1
  record stays as it is.
