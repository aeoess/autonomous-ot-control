# Join the testbed as a federation edge

The testbed runs a simulated building whose every change goes through REMORA.
A *federation edge* is your project's check, in your own code, run against
what the testbed records. It never receives a credential, never reaches the
plant, and can only add refusals, never permissions (ADR-004).

Everything below is self-service: pinned inputs, written-out rules, a scorer
that runs with the standard library, and a record format that says what was
checked, by whom and where. Nothing is listed on your behalf. You join by
opening a pull request; that pull request is your consent record, and you
leave the same way. The machine-readable entry point is
[`federation/index.json`](../../federation/index.json).

## What you get

| Artifact | Where | Contract |
|---|---|---|
| Sealed fixture set: 12 recorded decision trails, 6 negative controls with one seeded fault each, per-file SHA-256, package digest, the six claims | `federation/fixtures/v1/` | [`federation-fixtures-v1`](../../contracts/federation-fixtures-v1.schema.json), cases: [`federation-case-v1`](../../contracts/federation-case-v1.schema.json) |
| Reference verifier, standard library only, no testbed imports | `federation/edges/_template/adapter.py` | — |
| Edge manifest: who you are, pin, licence, claims with ceilings, what your evaluator is | `federation/edges/<id>/edge.json` | [`edge-manifest-v1`](../../contracts/edge-manifest-v1.schema.json) |
| Result: one verdict per case | written by your adapter | [`edge-result-v1`](../../contracts/edge-result-v1.schema.json) |
| Run record: pins, digests, operator, host, derived independence level, per-claim results, what it does not establish | written by `runner.edge_check` | [`edge-run-record-v1`](../../contracts/edge-run-record-v1.schema.json) |
| Results table, generated from the records | [`RESULTS.md`](RESULTS.md) | — |

### Boundaries

| Boundary | Mode | You receive | You answer | Today |
|---|---|---|---|---|
| `effect_evidence` | offline | one decision trail per case: the proposal, REMORA's decision, its envelope and evidence manifest, the twin's operation record, the observed effect | is each claimed effect backed by the evidence, per claim? | open, auto-scored |
| `corpus_adequacy` | offline | the sealed set and the reference verifier as the checker to mutate | which seeded faults would a checker miss? | open, report reviewed |
| `runner_validity` | offline | the sealed set and the scorer `runner.edge_check`; you supply the deliberately broken verifiers | does the scorer report a failure as a failure, never a pass? | open, report reviewed |
| `admission_runtime` | inline | the exact call and its context, before dispatch | `permit` or `refuse`, with a reason | specified, no harness yet |
| `tool_definition` | inline | the served tool definition | does it match the approved one? | blocked on the MCP gateway |
| `authority_fixture` | inline | a synthetic signed work order | what does it authorize? | specified, no harness yet |

The scorer reads one verdict per case, so a green CI run is an
`effect_evidence` result. A `corpus_adequacy` or `runner_validity` edge puts
its report in its edge directory and links it from the pull request; it is
reviewed, not auto-scored. For `runner_validity` the checklist is the nine
`runner_contract` properties every run record carries (fresh working
directory, success exit 0, failure exit non-zero with no report and no PASS,
existing output refused, dependency failure non-zero, killed verifier
non-zero, malformed result fails closed); the testbed pins them in
`tests/unit/test_federation_runner_contract.py`, and a report that breaks one
is a finding.

### Claims

The fixture manifest names six claims and maps every seeded fault to the
claim it contradicts. Your `edge.json` lists the claims you check, with a
ceiling each, and every run is scored per claim.

| Claim | Statement | Seeded fault |
|---|---|---|
| `decision_correspondence` | a dispatch is attested only when a captured ACCEPT, or an approved VERIFY/ESCALATE, is bound to the proposal | `executed_without_approval` |
| `single_dispatch` | the approved proposal executed exactly once | `replayed_execution` |
| `call_binding` | the twin's own operation record belongs to this proposal | `proposal_swapped` |
| `declared_effect_observed` | the twin recorded APPLIED for the claimed zone and value, and the observation shows that value | `effect_value_not_reached` |
| `observation_after_dispatch` | the effect was observed at or after the dispatch | `observed_before_dispatch` |
| `evidence_digest_integrity` | `evidence_sha256` is the canonical SHA-256 of `remora_evidence` | `evidence_digest_mismatch` |

## In five steps

**1. Scaffold.**

```bash
python -m runner.edge_check --init <your-edge-id> --project "<project name>" --maintainer <github-handle>
```

This copies the template to `federation/edges/<your-edge-id>/` and fills in
`edge_id`. Edit `edge.json`: repository, a pinned commit or image digest,
licence, the claims you check, each with its claim ceiling (what a green
result shows and what it does not), and `evaluator`:

- `kind`: `reference` while `check()` is still ours; `second_implementation`
  once you replaced it with your own logic;
- `imports_reference_verifier`, `imports_testbed_code`: true if your adapter
  imports `adapter.py` from the template or anything from `runner/`,
  `twin/`, `adapter/` or `deploy/`.

These three fields, together with who runs it and where, decide the
independence level of every run. Nobody declares the level. Validate at any time:

```bash
python -m runner.edge_check --validate federation/edges/<your-edge-id>
```

**2. Implement one entry point.**

- **Offline:** `adapter run --fixtures <dir> --out <result.json>`. Read every
  case under `<dir>/cases/` and write one verdict per case with a reason
  (`claim_id` optional). Any language works if you ship a Dockerfile whose
  entry point behaves the same way; `entrypoint.image` names it.
- **Inline:** an HTTP endpoint `POST /decide` that takes `{call, context}` and
  answers `{verdict: "permit" | "refuse", reason}` within 2 s. A timeout or an
  error counts as `refuse` and is reported as `edge_unavailable`, never as a
  catch. No harness runs this yet.

**Digest rules** (the only two an independent implementation needs):
`evidence_sha256` is SHA-256 over the UTF-8 bytes of the canonical JSON of
`remora_evidence`, keys sorted, separators `,` and `:` with no whitespace,
non-ASCII kept as-is (Python
`json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).
`fixtures_sha256` in your result is SHA-256 over the raw bytes of
`manifest.json`. The manifest itself lists every case file with its SHA-256
and a `package_digest` over those lines, so you can check that the bytes you
evaluated are the bytes the testbed sealed.

**3. Run it, locally first.**

```bash
./deploy/scripts/run-edge.sh <your-edge-id> --local   # one process, standard library, no Docker
./deploy/scripts/run-edge.sh <your-edge-id>           # sandboxed container, exactly as CI
```

Both verify the fixture set against its manifest, run your adapter in a fresh
working directory, refuse to score a result that is missing or malformed,
and write `result.json`, `report.md`, `score.json` and `run-record.json`
under `artifacts/edges/<your-edge-id>/<run>/`. The sandboxed run has no
network, a read-only root filesystem and a non-root user. A run passes only
if every case is answered and **every negative control is caught**. False
refusals of positive cases are counted and shown, never hidden, and do not
fail the run: an edge may be stricter than the testbed. The script calls
`python3`; on Windows (Git Bash) set `PYTHON=python`.

Say who ran it and where. The defaults describe the testbed's own CI:

```bash
OPERATOR=EXTERNAL HOST=EXTERNAL RUN_REF=https://<your-published-run> ./deploy/scripts/run-edge.sh <your-edge-id>
```

| Evaluator | Operator | Host | Level |
|---|---|---|---|
| reference (or imports it) | PRODUCER | any | `L0_SELF_TEST` |
| reference (or imports it) | EXTERNAL | any | `L1_REPRODUCTION` |
| second implementation that imports testbed code, or run by the testbed's CI | — | — | `L2_SECOND_IMPLEMENTATION` |
| second implementation, no testbed imports | EXTERNAL | PRODUCER_CONTROLLED | `L3_INDEPENDENT_RECOMPUTATION` |
| second implementation, no testbed imports | EXTERNAL | EXTERNAL | `L4_INDEPENDENT_HOST_RUN` |

A CI run on this repository is operator PRODUCER on a PRODUCER_CONTROLLED
host, so it reaches at most L2. L3 and L4 are your own run, submitted as a
record. The record says what it establishes (the claims whose seeded faults
were all caught, at that level) and repeats what it does not: your ceilings,
and the testbed's (synthetic plant, one deployment, no physical write path).

**4. Submit the record with the pull request.**

Copy `run-record.json` to `federation/records/<your-edge-id>/<started_at>.json`
and add your entry to [`edge-registry.yaml`](edge-registry.yaml):

```yaml
participants:
  - edge_id: <your-edge-id>
    project: <name>
    maintainer: <your GitHub handle>
    boundary: effect_evidence
    mode: offline
    consent_ref: https://github.com/darklordVirtual/autonomous-ot-control/pull/<n>
```

Then regenerate the results table and include it:

```bash
python -m runner.edge_check --check-records && python -m runner.edge_check --results
```

**5. Open the pull request.** It may add `federation/edges/<your-edge-id>/`,
`federation/records/<your-edge-id>/`, your registry entry and the regenerated
`RESULTS.md`, and nothing else. CI validates every record (schema, digests
against the sealed set, derived level), refuses a results table that differs
from the records, and runs your edge in the sandbox at L0–L2 as a
reproduction beside your own record. A maintainer reviews it. On merge, you
are listed, with your own record and the CI record side by side.

## What a result means

- A row in `RESULTS.md` says what was checked, against which sealed bytes, by
  whom, where, and at which derived level. It implies no endorsement, no
  production safety, no broader validity and no authority; those four are
  schema constants fixed at `false` in every record.
- Verification is symmetric and not endorsement: your green run does not
  endorse the testbed, and listing you does not endorse your project.
- A record that catches fewer faults than the template is a result, not a
  rejection. It stays in the table. A positive case you refuse is shown as a
  false refusal and may be a finding against the testbed; open an issue.
- Nothing here moves a status in [`docs/status.md`](../status.md). Real
  buildings and physical OT safety stay `NOT_ESTABLISHED` whatever an edge
  finds.

## What your pull request may and may not touch

- **May add:** files under `federation/edges/<your-edge-id>/` and
  `federation/records/<your-edge-id>/`, your registry entry, the regenerated
  `RESULTS.md`.
- **May not change:** the REMORA image, the adapter in `deploy/ot/`, the twin's
  interlock, any policy, the fixtures, the scorer, or another edge's files.
  Those belong in a separate pull request with its own review.
- **Never receives:** REMORA tokens, the twin's actuator or control token, or
  access to `plant-private`.

## The fixtures

`federation/fixtures/v1/` is generated from a real governed run of the testbed
(`runner.federation_fixtures`, from `runner.demo` output) and sealed: the
manifest lists every case file with its SHA-256 and a package digest over
those lines (`'<path> <sha256>\n'` sorted by path; the manifest is never
hashed into itself).

- **Positive cases** are decision trails exactly as recorded.
- **Negative cases** are copies with one seeded fault each (the table under
  Claims). The manifest names the fault, the claim it contradicts and the
  expected verdict.

A fixture set is never edited after it has a record against it; a change is a
new directory. `v1` was sealed on 2026-10-27, before any participant joined.

| Set | Cases | Built from | Reference result |
|---|---|---|---|
| `v1` | 12 positive, 6 negative (one per fault) | governed demo week 2026-01-15..21, testbed `942ba37` | 12/12 established, 6/6 faults caught, all six claims (L0) |

Questions go in an issue on this repository. The discussion that motivated
the testbed is aeoess/agent-governance-vocabulary#177, but joining happens
here, by pull request. Licence for the fixtures and contracts is not yet
decided (SDD D-01): you may run them and publish your own attributed result;
redistributing the fixtures themselves waits for the licence.
