# Join the testbed as a federation edge

The testbed runs a simulated building whose every change goes through REMORA.
A *federation edge* is your project's check, run against what the testbed
records. Your edge either checks evidence after the fact (**offline**) or adds
a decision before dispatch (**inline**). It never receives a credential, never
reaches the plant, and can only add refusals, never permissions (ADR-004).

Nothing is listed on your behalf. You join by opening a pull request. That pull
request is your consent record, and you can leave the same way.

## In five steps

**1. Pick your boundary and mode.**

| Boundary | Mode | You receive | You answer |
|---|---|---|---|
| `effect_evidence` | offline | one decision trail per case: the proposal, REMORA's decision, its envelope and evidence manifest, the twin's operation record, the observed effect | is the claimed effect backed by the evidence? |
| `corpus_adequacy` | offline | the whole fixture set, including the negative controls | which seeded faults would a check miss? |
| `runner_validity` | offline | the fixture set and a deliberately failing verifier | does a runner report the failure, never a pass? |
| `admission_runtime` | inline | the exact call and its context, before dispatch | `permit` or `refuse`, with a reason |
| `tool_definition` | inline | the served tool definition (after the MCP gateway exists) | does it match the approved one? |
| `authority_fixture` | inline | a synthetic signed work order | what does it authorize? |

**2. Copy the template.**

```bash
cp -r federation/edges/_template federation/edges/<your-edge-id>
```

Edit `edge.json` in the new directory. It is your manifest
(`contracts/edge-manifest-v1.schema.json`): who you are, a pinned commit or
image digest, your licence, the boundary and mode, and the **claim ceiling**,
meaning what a green result does and does not show.

**3. Implement one entry point.**

- **Offline:** `adapter run --fixtures <dir> --out <result.json>`. Read every
  case under `<dir>/cases/` and write one verdict per case
  (`contracts/edge-result-v1.schema.json`).
- **Inline:** an HTTP endpoint `POST /decide` that takes `{call, context}` and
  answers `{verdict: "permit" | "refuse", reason}` within 2 s. A timeout or an
  error counts as `refuse` and is reported as `edge_unavailable`, never as a
  catch.

The template's `adapter.py` is a working offline verifier using only the
standard library. Replace its checks with yours, or keep the file as a thin
wrapper that calls your own tool. Any language works if you ship a Dockerfile
whose entry point behaves the same way.

**4. Run it locally, exactly as CI will.**

```bash
./deploy/scripts/run-edge.sh <your-edge-id>
```

This runs your adapter in a container with no network, a read-only root
filesystem and a non-root user, against the frozen fixtures in
`federation/fixtures/v1/`. It then validates your output and scores it into
`artifacts/edges/<your-edge-id>/<run>/report.md`:

- every case answered;
- positive controls against their expected verdicts;
- **every negative control caught.**

A run that answers nothing, or that passes a negative control, fails. A green
result that cannot fail is not evidence.

**5. Open a pull request** that adds `federation/edges/<your-edge-id>/` and an
entry in `docs/federation/edge-registry.yaml`:

```yaml
participants:
  - edge_id: <your-edge-id>
    project: <name>
    maintainer: <your GitHub handle>
    boundary: effect_evidence
    mode: offline
    consent_ref: https://github.com/darklordVirtual/autonomous-ot-control/pull/<n>
```

CI runs step 4 without secrets or write access and attaches the report. A
maintainer reviews it. On merge, you are listed.

## What your pull request may and may not touch

- **May add:** files under `federation/edges/<your-edge-id>/` and your registry entry.
- **May not change:** the REMORA image, the adapter in `deploy/ot/`, the twin's
  interlock, any policy, the fixtures, or another edge's files. Those changes
  belong in a separate pull request with its own review.
- **Never receives:** REMORA tokens, the twin's actuator or control token, or
  access to `plant-private`.

## How results are presented

Each run's report gives:

- the fixture set version and its SHA-256;
- your pinned commit or digest, and the testbed commit;
- the exact command;
- per-case verdicts, with positive and negative controls counted separately;
- your claim ceiling, stated verbatim;
- one of two labels:
  - **reproduction**: run by this repository's maintainers;
  - **independent**: run by you on your own infrastructure, with your report linked.

Merged results are summarized in `docs/federation/RESULTS.md`. Negative results
stay there; nothing is removed because it looks bad. You can publish the same
`report.md` on your side, and it says the same thing.

## The fixtures

`federation/fixtures/v1/` is generated from a real governed run of the testbed
(`runner.federation_fixtures`, from `runner.demo` output).

- **Positive cases** are decision trails exactly as recorded.
- **Negative cases** are copies with one seeded fault each:
  - an effect claimed for a value the plant never reached;
  - a proposal id swapped between trails;
  - an evidence manifest whose hash no longer matches;
  - an effect observed before the dispatch;
  - a dispatch with no approval;
  - a replayed execution.

The manifest lists every case with its expected verdict and the fault it
seeds. A new fixture version never replaces an old one; it gets a new
directory.

Questions go in an issue on this repository. The discussion that motivated
the testbed is aeoess/agent-governance-vocabulary#177, but joining happens
here, by pull request.
