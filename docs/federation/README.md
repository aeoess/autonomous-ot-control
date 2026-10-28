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

## Pinned evaluations already in progress

The bounded, offline `runner_validity` evaluation started on
`4b9f6ec7882b81ca95ecf414e107ec202663bb13`. That revision is retained on
[`archive/federation-runner-validity-2026-10-27-v1`](https://github.com/darklordVirtual/autonomous-ot-control/tree/archive/federation-runner-validity-2026-10-27-v1).
Keep that archive branch at this exact SHA; do not move or delete it.

```bash
git fetch origin archive/federation-runner-validity-2026-10-27-v1
git checkout --detach 4b9f6ec7882b81ca95ecf414e107ec202663bb13
python -m pytest -q tests/unit/test_federation_runner_contract.py
```

An ongoing evaluation keeps its agreed revision, fixture set and scope.
Later fixes and the v2 default do not change an earlier run's inputs or
upgrade its result. A run on a newer revision is a separate run with its
own SHA and fixture digest, only if the evaluator chooses to do it. This
preservation record does not enroll a participant or add any obligation.

The reviewed PR #21 revision, `67baebd8a3b74208db2c8700d157423268c6b9ab`,
is also retained on `archive/pr21-pre-ci-fix-67baebd8`, including the earlier
commits named by fixture provenance. Preserve that branch too. Integrate
PR #21 with a merge commit, so the named ancestors remain on `main`.

## What you get

| Artifact | Where | Contract |
|---|---|---|
| Sealed fixture sets: 12 recorded decision trails and negative controls with one seeded fault each (6 in `v1`, 10 in `v2`), per-file SHA-256, package digest, the six claims, the expected verdict per negative | `federation/fixtures/v2/` (default), `federation/fixtures/v1/` | [`federation-fixtures-v1`](../../contracts/federation-fixtures-v1.schema.json), cases: [`federation-case-v1`](../../contracts/federation-case-v1.schema.json) |
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
| `admission_runtime` | inline | the exact call and a context (the day plan as what it permits, the current setpoint, the declared step limit), after REMORA and the reviewer approved | `permit` or `refuse`, with a reason, within 2 s | open: harness in `runner.demo --inline-edge`, reference edge `_inline_template`, scored in the A/B/C report ([ABC-ROADMAP.md](ABC-ROADMAP.md)) |
| `tool_definition` | inline | the served tool definition and the digest of the signed ToolSpec bundle REMORA pinned, per call | does the served definition match the approved one? | no harness yet; planned next, against the pinned ToolSpec bundle rather than an MCP gateway, in the shape of AgentAvow's `tool_manifest_digest` (E8) |
| `authority_fixture` | inline | a synthetic signed work order | what does it authorize? | no harness yet. The signed work-order format, the normalized call, RFC 8785 JCS and test-key signing are proposed with APS in [authority-fixture-contract.md](authority-fixture-contract.md) (issue #27) and implemented (`runner/jcs.py`, `runner/work_order.py`); the offline harness (`runner/authority_harness.py`), the five cases as a sealed set with a stand-in verifier (`federation/authority/`, not APS's format) and the oracle's `delegation_invalid` rule are in place; shadow evaluation comes next |

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
| `observation_after_dispatch` | the effect was observed at or after the dispatch | `observed_before_dispatch`; v2: `dispatch_time_missing`, `observation_time_missing` |
| `evidence_digest_integrity` | `evidence_sha256` is the canonical SHA-256 of `remora_evidence` | `evidence_digest_mismatch` |
| (`single_dispatch`, `decision_correspondence` again) | | v2: `execution_count_missing`, `denied_but_applied` |

Each negative control names the verdict a correct verifier gives
(`expected_verdict` in the manifest). The v1 faults are trails that disagree
with themselves: `CONTRADICTED`. Three v2 faults remove evidence a claim
needs (the dispatch time, the observation time, the execution count): the
right answer is `NOT_ESTABLISHED`, never an assumed value. The fourth,
`denied_but_applied`, says nothing executed while the twin's own record says
APPLIED: `CONTRADICTED`. The scorer counts a negative as caught only when the
verdict is the expected one; `ESTABLISHED` on any negative fails the run.

### Current adapter admission rules (#177 follow-up)

These rules apply to the current adapters; they do not change the sealed
v1/v2 files, their manifests, or historical evaluation results.

- A captured `accept` needs no reviewer. An executed `verify`/`escalate`
  needs `decision.approved=true` and a reviewer with `approved=true`.
  A recorded reviewer refusal is `CONTRADICTED`; absent or untyped reviewer
  approval is `NOT_ESTABLISHED`. A deny cannot be turned into approval by
  setting the outer approval flag.
- Every recorded binding at `remora_envelope.request_id`,
  `remora_envelope.request.request_id`, `remora_evidence.proposal_id`,
  `remora_evidence.manifest.proposal_id` and
  `remora_evidence.proposal.proposal_id` must equal `claim.proposal_id`.
  A mismatch contradicts `call_binding`, even with a newly computed valid
  digest. Optional absent identifiers remain compatible with older synthetic
  exports; this is consistency checking, not proof of identity or completeness.
- Invalid case shapes, unknown top-level fields, booleans in numeric fields,
  non-finite numbers, numbers outside the adapter's numeric range and duplicate
  JSON members are `NOT_ESTABLISHED`.
  A malformed case is isolated and does not prevent verdicts on other cases.
  Optional missing execution/observation evidence retains its per-claim result.
- Standalone adapters use local standard-library admission checks, because
  the sandbox mounts no repository code or schema package. The same maintainer
  guard is included in both wrappers; it is not independent validation by the
  upstream reader. The vendored reader itself remains unchanged.

The [mutation crosswalk](mutation-crosswalk.md) records the added controls
and the limits of comparison with the external reader's mutation battery.

## In five steps

Keep your own commit author identity. External names and GitHub noreply
addresses are accepted; do not impersonate the repository owner to pass CI.
See [CONTRIBUTING.md](../../CONTRIBUTING.md).

### Choose the submission path

**One bounded report, without enrolling.** Publish the report where you
control it (your repository, a gist or a release), written from
[REPORT_TEMPLATE.md](REPORT_TEMPLATE.md): link the evaluator's own immutable
source and logs; include the testbed SHA, agreed inputs, exact commands,
outcomes, negative controls and claim ceiling. Then open a pull request (or an
issue, and a maintainer adds it) that appends one row to
[`federation/reports.json`](../../federation/reports.json) with the report's
URL, testbed commit, boundary, result and non-claims as you stated them;
`runner.edge_check --check-results` validates the row. The report itself is
not copied into this repository. No edge directory, runtime adapter, registry
entry, credentials or ongoing maintenance is required.
`runner_validity` and `corpus_adequacy` reports are reviewed by a maintainer;
CI does not execute them as effect verifiers or manufacture a PASS record.
A report alone is not an auto-scored row in `RESULTS.md`.

**Repeatable effect-evidence edge.** Follow the steps below. The checker may
remain entirely in the contributor's repository and public container image.
Only its metadata and run records need to be committed here. A pull request
adding the edge and registry entry is the consent record for that edge only.
For a report-reviewed boundary that also wants an edge manifest, an empty
`entrypoint: {}` is accepted; no per-case adapter is required or run.

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

**Keep the agreed fixture scope.** Set `"fixture_sets": ["v1"]` in
`edge.json` for a v1-only evaluation, or `["v2"]` for a v2-only one.
CI runs only these versions, while its reference self-test still checks
every sealed set. Empty, duplicate, unknown or path-like names are refused.
Omitting the field preserves the previous behavior: CI tests every sealed
set. Adding a version is an opt-in change, never an automatic upgrade to an
earlier report. List the selection with:

```bash
python -m runner.edge_check --list-fixture-sets federation/edges/<your-edge-id>
```

**2. Implement one entry point.**

- **Offline:** `adapter run --fixtures <dir> --out <result.json>`. Read every
  case under `<dir>/cases/` and write one verdict per case with a reason
  (`claim_id` optional). Any language works if you ship a Dockerfile whose
  entry point behaves the same way; `entrypoint.image` names it.
  **The image must be pinned by digest**, as `name[:tag]@sha256:<64 hex>`
  (SDD-OT-002 SR-01). A tag alone, or anything that starts with `-`, fails
  `--validate`. With `pin.kind: image_digest` it must equal `pin.value`.
- **Your adapter always runs in the testbed's container sandbox**
  (`runner.edge_sandbox`). The sandbox has no network, a read-only root, no
  capabilities, and runs as nobody. The edge and the fixtures are mounted
  read-only. The sandbox writes to a private directory, from which the host
  takes `result.json` alone and refuses links. This also holds when the edge
  runs beside a governed run (`runner.edge_pass`), and without Docker the edge
  does not run at all. Only `--trust-edge <id>` (or `run-edge.sh --local`,
  which passes it) runs an adapter on the host. Use it for your own code on
  your own machine.
- **Inline:** an HTTP endpoint `POST /decide` that takes `{call, context}` and
  answers `{verdict: "permit" | "refuse", reason}` within 2 s. A timeout, an
  error or a malformed answer counts as `refuse` and is reported as
  `edge_unavailable`, never as a catch. The governed demo consults it only
  after REMORA and the reviewer approved (`runner.demo --inline-edge
  federation/edges/<id>`, `runner.admission`), so it can only add refusals;
  its contribution is scored in the A/B/C report as refusals of forbidden
  proposals, false refusals of permitted ones, misses and unavailable answers.
  Start from `federation/edges/_inline_template/` (a stdlib server that
  re-checks the call against the day plan).

**A delivered example.** Probity's `agent-evidence-vectors-axes`
([PR #22](https://github.com/darklordVirtual/autonomous-ot-control/pull/22))
is an offline `effect_evidence` edge: its manifest is
`federation/edges/agent-evidence-vectors-axes/edge.json`, its external run is
`federation/records/agent-evidence-vectors-axes/20261027T171310Z.json` (v1,
12/12 positives, 6/6 negatives, `L4_INDEPENDENT_HOST_RUN` from the declared
operator and host; no public run URL), and it is listed under
`registered_edges` in [`federation/index.json`](../../federation/index.json).
It agreed to **v1 only** (`"fixture_sets": ["v1"]`), so v2 is not run against
it unless its maintainer opts in. `runner.edge_pass` also runs it over every
governed arm's exported trails, beside the run and never changing it. It is
described for users in [edges/agent-evidence-vectors-axes.md](edges/agent-evidence-vectors-axes.md)
(how it is used, where its numbers are, how to check it). It is
the one accepted exception to the rule below: the PR ships an unchanged,
hash-verified copy of the upstream `invocation_axes` reader with its licence
file, because the reader is stdlib-only and the sandbox has no network.

**Keep external implementations external.** Instead of copying a verifier or
library here, publish the checker in its own repository/image. Remove the
scaffolded `adapter.py` from your contribution and set, for example:

```json
{
  "pin": {"kind": "image_digest", "value": "ghcr.io/<owner>/<checker>@sha256:<64-hex-digest>"},
  "fixture_sets": ["v1"],
  "entrypoint": {
    "image": "ghcr.io/<owner>/<checker>@sha256:<64-hex-digest>",
    "command": ["python", "/opt/checker/adapter.py", "run", "--fixtures", "/fixtures", "--out", "/out/result.json"]
  }
}
```

This is a fragment of `edge.json`, not a complete manifest. Replace the
placeholders with the actual public image and full digest; the command must
match that image. The image contains its dependencies before publication;
there is no runtime `pip install` or network fetch. It must run as UID 65534
with a read-only root and write only under `/out` or `/tmp`. Its external
source pin and licence belong in `run.md`. Do not vendor its source or add a
dependency-bump bot here. Check out the contributor's pinned source separately
when reviewing its implementation.

Use the Docker command below for an image-only edge. `--local` runs an
on-disk entry point and cannot execute a checker that exists only in an image.

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

For an agreed v1 run, pass `v1` explicitly:
`./deploy/scripts/run-edge.sh <your-edge-id> v1`. The shell's default remains
v2; `fixture_sets` controls CI, not a manual command's explicit input.

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

Open a draft pull request first to obtain its number for `consent_ref`, then
add the registry entry and record in that same branch. Do not use a guessed
number or another participant's pull request. A report-only submission needs
no registry entry.

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
from the records, and runs your effect-evidence edge on its agreed sets in the sandbox at L0–L2 as a
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
  `RESULTS.md`. A report-only submission may instead add only its row in
  `federation/reports.json` (the report stays at its published URL).
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
| `v2` | the 18 v1 cases byte for byte, plus 4 negatives (missing dispatch time, missing observation time, missing execution count, denied-but-applied) | `python -m runner.federation_fixtures --derive-from federation/fixtures/v1`, sealed 2026-10-27 against the testbed revision in its manifest | 12/12 established, 10/10 faults caught (L0) |

`v2` is the default set for `run-edge.sh`. It exists because the reference
verifier at the v1 freeze accepted the three missing-evidence mutations and
the denied-but-applied trail as `ESTABLISHED`. `v1` keeps its bytes, its
seal and its records.

**Provenance of a set has two parts, kept apart.** `testbed_commit` is the
revision the decision trails were recorded on (for `v2`, inherited from
`v1`). `sealed_against` is the verifier and scorer the set was sealed
against: an existing, earlier commit plus the SHA-256 of
`federation/edges/_template/adapter.py` and `runner/edge_check.py` at that
commit. A set never names the commit that adds it, because that commit
cannot be known when the manifest is written. The check

```bash
python -m runner.edge_check --check-provenance federation/fixtures/v2
```

fetches those two files at the declared commit out of git, requires their
digests to match the declaration, runs that exact verifier against the set
in a fresh directory, and requires every case, the set's added faults first
among them, to get the verdict the manifest expects. CI runs it on every
derived set. Every run record also carries `provenance`: the commit the
working tree was at, whether it was dirty, and the digests of the edge's
files and of the scorer that scored it.

Questions go in an issue on this repository. The discussion that motivated
the testbed is aeoess/agent-governance-vocabulary#177, but joining happens
here, by pull request. The fixtures, contracts, scorer and reference
verifiers are Apache-2.0 like the rest of this repository; your edge keeps its
own licence and copyright, stated in its directory. REMORA is not covered
(BUSL-1.1). Details: [THIRD_PARTY_LICENSES.md](../../THIRD_PARTY_LICENSES.md).
