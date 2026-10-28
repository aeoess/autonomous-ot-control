# agent-evidence-vectors invocation-axes edge

Submitted by `astrogilda`, the upstream maintainer, in
[PR #22](https://github.com/darklordVirtual/autonomous-ot-control/pull/22).
The repository owner explicitly accepted this isolated external edge.
It is not part of REMORA's implementation or the protected dispatcher.

## Source and licence

- Package: `agent-evidence-vectors==0.28.0`.
- Upstream source: `https://github.com/probityai/agent-evidence-vectors/blob/v0.28.0/packaging/agent_evidence_vectors/invocation_axes/reader.py`.
- Local copy: `invocation_axes_reader.py`, unchanged.
- SHA-256: `a86c2c4830af011f3707c8164ca1350c9b965d2a960967bff83c79532df54938`.
- Verified byte-for-byte against the 0.28.0 PyPI wheel during integration.
- Upstream licence: Apache-2.0, copyright 2026 Sankalp Gilda. The wheel's
  exact licence file is retained as `LICENSE.agent-evidence-vectors`.

The submitted edge manifest and historical external record said MIT. The
current manifest corrects that package metadata to Apache-2.0. The historical
record remains unchanged as evidence of what was submitted; its MIT field
is an identified metadata error, not the licence of the vendored reader.

## Reproduction

This edge opts into **v1 only**. Its original external result is 12/12
positive cases and 6/6 negative controls. `L4_INDEPENDENT_HOST_RUN` in the
submitted record derives from the contributor's declared operator/host;
there is no published run URL in that record. A local maintainer reproduction
is L2, not a new L4 run.

```bash
./deploy/scripts/run-edge.sh agent-evidence-vectors-axes v1
./deploy/scripts/run-edge.sh agent-evidence-vectors-axes v1 --local
python federation/edges/agent-evidence-vectors-axes/adapter.py run \
  --fixtures federation/fixtures/v1 --out /tmp/axes-strict-result.json --strict-vantage
```

The adapter's default verdict checks the recorded effect, using the reader
through a declared mapping. It does not establish that the plant observation
was independent: every v1 positive says `same_deployment`. Strict vantage
therefore returns `NOT_ESTABLISHED` for the positive effect claims. An
external host running a checker does not change the plant observation's
vantage. Physical safety, capture authenticity and capture completeness
remain outside this result.

## Local integration changes

The upstream reader and the contributor's original external record are not
modified. The testbed maintainer hardened `adapter.py` to require raw APPLIED
status and matching actual/observed values, reject deny-with-approval, avoid
rounding-based equality, and leave missing count/time evidence unestablished.
Regression tests retain every original v1 verdict and reason and cover those
additional mutations. These checks are maintainer tests, not an expansion of
the external contributor's agreed evaluation.

On 2026-10-28 the testbed's mutation suite (`runner.edge_mutations`) found two
problems in the adapter. A case whose observation lacks `observed_setpoint_c` made
`adapter.py` raise `KeyError` and stop the whole run. The comparison below it also
read a missing value as a contradicting one. Both were reported to the maintainer in
[PR #22](https://github.com/darklordVirtual/autonomous-ot-control/pull/22#issuecomment-6458546497).
The testbed maintainer then repaired the adapter, not the vendored reader, whose
digest is unchanged:

- an observation without a numeric value is left out of the bundle and answered
  `NOT_ESTABLISHED`;
- the vantage is read defensively;
- a case the adapter cannot read is answered `NOT_ESTABLISHED` with its reason,
  instead of ending the run.

Every v1 verdict and reason is unchanged (12/12, 6/6), as the regression tests
require. On the mutation set the edge now kills 420/420 mutants with 100 % exact
verdicts; before the repair it was 406/420 and 97.3 %. The historical v1 record
is unchanged.

On 2026-10-28 (SDD-OT-002 SEC-01) the maintainer pinned this edge's
`entrypoint.image` by digest
(`python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9`,
the image every sandboxed run of it used), because a tag can move. The edge now
always runs in the container sandbox (`runner.edge_sandbox`), including beside
governed runs. Its v1 result in the sandbox is unchanged.

Dependabot proposes package updates only. A requirements-only bump fails
the integration test: version, vendored bytes, digest and current edge pin
must be reviewed and updated together. No dependency is installed at runtime;
the sandbox runs this stdlib-only reader with network access disabled.

## Maintainer #177 follow-up

The wrapper now rejects malformed case shapes, duplicate JSON members,
non-finite numbers, reviewer refusal on approved review decisions, and
conflicting proposal bindings (including native nested REMORA paths).
These are local maintainer integration checks also present in the reference
wrapper, not additional independent checks by the upstream reader. The pinned
`invocation_axes_reader.py` is unchanged. Existing v1 verdicts and reasons are
preserved. See `docs/federation/mutation-crosswalk.md` for the development-only
operator comparison; this does not expand the submitted v1 evaluation.
