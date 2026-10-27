# Integration contracts — REMORA governed dispatch (PR 2, OT-003 + OT-004)

This records the seams between `autonomous-ot-control` and the pinned REMORA
upstream, reconciled against the real source (first at `2594d08b`, re-checked
at `9b1763ca`, now pinned at `123a5fd0`, see `upstream/remora.lock.json`), and
the custody/fencing boundary. Since OT-005 slice 1 the governed
path runs end to end in Docker; the acceptance results are in the README and in
each run's `artifacts/<run-id>/` evidence bundle. See
`docs/SDD-OT-001.md` for the full spec and invariants (INV-01..12).

## Findings from the first live governed runs (2026-10-27)

Building the image and running the suite found defects that offline tests could
not. Each is fixed where it belongs; nothing upstream is patched locally (INV-11).

| # | Finding | Where fixed |
|---|---|---|
| 1 | The REMORA image lacked `httpx`, which `ot_registry` imports. The registry is imported at the first dispatch, so the failure came **after** the approval (500, item left in `review_conflict`, no effect). | here: `deploy/ot/requirements-adapter.lock` (hash-pinned, equal to `requirements.lock`) |
| 2 | `remora.sdk` `AuditRef` called `int(None)` on the documented deferred audit ref, so `RemoraClient.approve` raised `TypeError` after a successful approval. | REMORA-research#1099 (acceptance case SDK-01 FAILs at the pin until then) |
| 3 | A `closure-sha256:` digest did not follow a sibling local package: the registry's effect code in `adapter/` was recorded as the constant `unresolved`, so editing it left the signed digest unchanged. | REMORA-research#1100 |
| 4 | Assess ran the limit preview before the signed contract, so the observer was called with an agent-chosen resource (`ZONE-09`, a path) and the refusal named the meter. | REMORA-research#1101; here the observer also accepts only contract zones and UUID operation ids |
| 5 | The default demo failed in CI: compose interpolates every service, so the governed `:?` variables broke the simulator-only run. | here: governed services moved to `compose.governed.yaml` |

Observations recorded, not defects: an interlock or stop-switch refusal spends
the approval (re-execution needs a new approval); AT-13's single effect is held
by the twin interlock (revision/min-interval), not by REMORA; the 0.1 degC
resolution cannot be signed (REMORA's schema validator has no `multipleOf`), so
only the adapter and the interlock enforce it.

## Verified REMORA contracts (first at 2594d08b, re-checked live at 9b1763ca)

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
| `harness` (acceptance step executor) | observe, authority | no | operator, approver and viewer REMORA tokens plus the read token; REMORA separates the roles |

Pinned by `tests/boundary/test_custody_and_fencing.py`.

## Next

1. Repin to a REMORA commit that contains #1099–#1101 and drop `ALLOW_FAILING=SDK-01`.
2. Fault hooks in the twin for AT-09 (stale observation, unit, TFM) and AT-12 (timeout after actuation).
3. AT-16: the same seeded scenario with and without REMORA (and an adversarial agent), scored with exact denominators.
4. Federation capsule (OT-007) for #177 edges: read-only fixtures and evidence exports, never the credential-holding runtime.
