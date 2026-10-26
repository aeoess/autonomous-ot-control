# Integration contracts — REMORA governed dispatch (PR 2, OT-003 + OT-004)

This document records the seams between `autonomous-ot-control` and the pinned
REMORA upstream, and the custody/fencing boundary that keeps the simulated
actuator reachable only through REMORA's protected dispatcher. It is a design +
integration record, not a claim that the governed path has been run end to end.
See `docs/SDD-OT-001.md` for the full specification and invariants (INV-01..12).

## Status at this PR

| Capability | Status |
|---|---|
| Signed ToolSpec: model, canonical digest, deployment attestation, bounds mirror of the guard | `TESTED_LOCALLY` |
| Deployment-owned callable applies exactly one governed effect; idempotent; fails closed | `TESTED_LOCALLY` |
| Governed runner proposes via SDK, never self-approves, fails closed, `UNKNOWN` not retried | `TESTED_LOCALLY` |
| Token custody + plant-private fencing (actuator credential only in twin + REMORA) | `TESTED_LOCALLY` (structural) |
| REMORA execution image build + registry hook (`deploy/Dockerfile.remora-ot`) | `IMPLEMENTED` — needs the pinned upstream to build |
| One authorized effect **through REMORA's dispatcher**; SDK-only actor causes zero effect | `NOT_ESTABLISHED` — requires the built REMORA image |

Statuses are promoted only by new evidence on a pinned build, never by editing a
table (SDD §14).

## The REMORA seam (D-03)

`remora.sdk` is the only supported client surface. This repo imports it in two
places and nowhere imports REMORA internals (`remora.enforcement`, …) as a
library:

- `runner/governed.py` — the proposing actor. Builds a `remora.sdk.ToolCall`,
  calls `client.assess(...)`, and on `ACCEPT` delegates to
  `client.execute_accepted(token, call)`. It holds only a role-restricted
  operator token; it never dispatches to the actuator itself.
- `adapter/verifier.py` — optional effect recording via the SDK's
  `build_postcondition` / `record_effect` when present.

The server-side registration hook is a **deployment integration**, not an SDK
API. `adapter/registry.register(registrar, …)` is duck-typed against the minimal
`Registrar` protocol. Before the REMORA image's governed-dispatch claim is
promoted beyond `IMPLEMENTED`, an integration test must reconcile that protocol
against the real registrar at the pinned commit
(`upstream/remora.lock.json` → `2594d08b…`), exercised through
`REMORA_TOOL_REGISTRY_MODULE=adapter.registry`.

The exact `remora.sdk` symbol names (`RemoraClient`, `ToolCall`,
`DecisionAction`, `execute_accepted`, `build_postcondition`, `record_effect`)
and the `DecisionAction` value set (`ACCEPT` / `VERIFY` / `ESCALATE` /
`ABSTAIN` / `REFUSE`) follow the SDD illustration and `docs/sdk.md`; they must be
reconciled with the pinned SDK types (a `sdk-contract-drift` check, AT-19) before
hosted use.

## Contracts

- **Signed ToolSpec** — `contracts/ot-toolspec-v1.json` (schema:
  `contracts/ot-toolspec-v1.schema.json`). Deployment-owned, carries the single
  WRITE tool `set_zone_setpoint`, its bounds `[19.0, 23.0] °C @ 0.1`, and the
  cumulative limits. These limits **must** equal `twin/guard.py`'s config
  (`deploy/scripts/preflight.sh` fails the build otherwise, D-05). The committed
  spec is unsigned; the HMAC attestation is applied at deploy time with
  `OT_TOOLSPEC_SIGNING_KEY` and is never committed. REMORA's own binding of the
  signed ToolSpec is authoritative and separate.
- **Actuation command** — `ot-actuation-command/v1` (unchanged from PR 1). The
  `operation_id` is REMORA's single-use execution identity, carried verbatim as
  the twin's idempotency key (INV-04). The adapter never accepts an agent-supplied
  operation id.
- **Effect receipt / observation** — `effect-receipt-v1`, `twin-observation-v1`
  (unchanged). `adapter/verifier.py` binds an observation to a REMORA operation;
  `EFFECT_UNOBSERVABLE` / `EFFECT_VERIFIER_FAILED` are never a retry permit.

## Custody and fencing (OT-004)

| Service | Networks | Holds `TWIN_ACTUATOR_TOKEN`? | Role |
|---|---|---|---|
| `twin` | observe, plant-private | yes (to verify) | simulator; actuator listener binds the fixed plant-private address only |
| `remora` | authority, plant-private | yes (to present) | protected dispatcher; the only caller of the write route |
| `postgres` | authority | no | REMORA authority/ledger state |
| `governed` (proposing runner) | observe, authority | no | proposes via SDK with an operator token; reads only |
| `runner` (smoke/baseline) | observe | no | untrusted boundary probes |

The actuator write route is reachable only from `plant-private`, which only
`twin` and `remora` join; the credential is injected only into those two
services. A proposing runner, a Worker, or a contributor container therefore has
neither a route nor a credential to the actuator even with a guessed URL
(AT-05/06/07). `tests/boundary/test_custody_and_fencing.py` pins this so an edit
cannot silently widen it. A Docker Compose network is not a complete security
boundary (host/Docker-socket controls still matter); no privileged containers or
Docker socket are mounted into any untrusted service.

## Deferred to later PRs

- Scored governed-vs-baseline E2E and effect-verification evidence — OT-005 (PR 3).
- Miniflare Worker + SQLite Durable Object — OT-006 (PR 4).
- Federation capsule, hosted Cloudflare staging — OT-007/008 (PR 5–6).
