# Integration contracts — REMORA governed dispatch (PR 2, OT-003 + OT-004)

This records the seams between `autonomous-ot-control` and the pinned REMORA
upstream, **reconciled against the real source at commit `2594d08b`** (not from
memory), and the custody/fencing boundary. It is a design + integration record,
not a claim that the governed path has been run end to end. See
`docs/SDD-OT-001.md` for the full spec and invariants (INV-01..12).

## Status at this PR

| Capability | Status |
|---|---|
| Signed ToolSpec: digest, attestation, bounds mirror of the guard | `TESTED_LOCALLY` |
| Actuator effect primitive: one effect, idempotent, 409 refusal raises | `TESTED_LOCALLY` |
| Deployment registry module: real `register_tools(register)` contract | `TESTED_LOCALLY` |
| Effect attestor on the real `build_postcondition`/`verify_effect`/`record_effect` API | `TESTED_LOCALLY` |
| Governed runner: `assess`/`execute_accepted`, never self-approves, fails closed | `TESTED_LOCALLY` |
| Token custody + plant-private fencing (actuator credential only in twin + REMORA) | `TESTED_LOCALLY` (structural) |
| REMORA execution image (`servers.api:app`) + deployment modules | `IMPLEMENTED` — needs pinned upstream to build |
| One authorized effect **through REMORA's dispatcher**; SDK-only actor → zero effect | `NOT_ESTABLISHED` — requires the built image + PostgreSQL + role tokens |

## Verified REMORA contracts (commit 2594d08b)

**Tool registry hook.** `REMORA_TOOL_REGISTRY_MODULE` names a module exposing
`register_tools(register)`; REMORA calls `register(name, callable)` and each
callable is `fn(arguments: dict) -> dict`, closing over its credentials app-side
(`servers/tool_registry_research.py`, `servers/execution_api.py`,
`deploy/ot-pilot/ot_registry.py`). Our module is `deploy/ot/ot_registry.py`; only
`set_zone_setpoint` writes and holds the actuator client. It binds REMORA's own
proposal id (from the `servers.execution_api.CURRENT_PROPOSAL_ID` contextvar —
a version-pinned deployment integration) as the twin's single-use operation id,
and returns the stored outcome on a re-invocation instead of re-applying
(INV-04/INV-10). It builds no grant/lease/policy (INV-11).

**Server + runtime.** The API is `uvicorn servers.api:app` in
`REMORA_ENV=production`, `REMORA_ENABLED_SURFACES=execution`. Fail-closed
prerequisites (verified in `servers/api.py`): `REMORA_PG_DSN` (durable execution
state), `REMORA_CONTROL_PLANE_DSN` (envelope store), `REMORA_API_TOKENS` (a
role-separated token table — operator cannot approve; admin approves),
`REMORA_ENVELOPE_SIGNING_KEY` and the PDP/lease/audit signing keys. Our
`deploy/Dockerfile.remora-ot` and the compose `governed` profile mirror
`deploy/ot-pilot/` exactly, pointing REMORA at our tool set and twin.

**SDK (proposing side).** `remora.sdk` — `ToolCall(tool_name, arguments,
target_environment, intent_ref)`, `RemoraClient.assess(call) -> AssessmentResult`
(`.action: DecisionAction`, `.execution_token` on ACCEPT, `.review_item_id` on
VERIFY/ESCALATE), `execute_accepted(execution_token, call)`. `DecisionAction`
members (from `remora/policy/report.py`) are **ACCEPT, VERIFY, ABSTAIN,
ESCALATE** — there is no REFUSE/DENY; the runner fails closed on ABSTAIN and on
any unmapped action. The base package has no third-party deps, so the governed
image installs it with `--no-deps`.

**Effect API.** `build_postcondition(*, tool_id, target_selector, expected_fields,
comparison_rules=None, reader="", ...)` → `verify_effect(spec, observed, *,
proposal_id, execution_id, toolspec_hash, verifier_identity, ...)` →
`RemoraClient.record_effect(proposal_id, EffectVerificationView)`
(`remora/sdk/effects.py`, `remora/sdk/client.py`). `observed=None` is
UNOBSERVABLE, never MISMATCH, and never a retry permit. `adapter/verifier.py`
uses exactly these signatures.

**Deployment-owned siblings** (modeled on `deploy/ot-pilot/`): a semantic bundle
(`deploy/ot/ot_bundle.py`, `REMORA_SEMANTIC_BUNDLE_MODULE`; strengthens assess,
optional — unset falls back to the registry-only path), an intent/work-order
source (`deploy/ot/ot_intents.json`, `REMORA_INTENT_SOURCE_FILE`), and a
reference cumulative-limits policy (`deploy/ot/ot_limits.policy.json`). The limits
policy is shipped **unsigned**: REMORA verifies a signed envelope, so a metered
profile must sign it with the authority key (`scripts/sign_limit_policy.py`),
publish only the public key in `REMORA_LIMITS_TRUSTED_KEYS`, and set
`REMORA_REQUIRE_LIMITS=1`. It is off by default here.

## Custody and fencing (OT-004)

| Service | Networks | Holds `TWIN_ACTUATOR_TOKEN`? | Role |
|---|---|---|---|
| `twin` | observe, plant-private | yes (to verify) | simulator; actuator listener binds the fixed plant-private address only |
| `remora` | authority, observe, plant-private | yes (to present) | protected dispatcher; the only caller of the write route; reads the twin to bind the revision |
| `postgres` | authority | no | REMORA authority/ledger + envelope store |
| `governed` (proposing runner) | observe, authority | no | proposes via SDK with an operator token; reads only |
| `runner` (smoke/baseline) | observe | no | untrusted boundary probes |

Pinned by `tests/boundary/test_custody_and_fencing.py`.

## Remaining to establish the governed E2E (next round)

1. Build `deploy/Dockerfile.remora-ot` from the pinned upstream; run real PostgreSQL and the role-separated token table.
2. Reconcile the `CURRENT_PROPOSAL_ID` contextvar access and the `register_tools` wiring against the running image (an integration test, D-03).
3. Sign the cumulative-limits policy and enable `REMORA_REQUIRE_LIMITS=1`.
4. Drive VERIFY → reviewer `approve` → `execute` → twin observation → `record_effect`, plus negatives (tampered args, replay, outage, wrong role, timeout/UNKNOWN), retaining full CI evidence.
