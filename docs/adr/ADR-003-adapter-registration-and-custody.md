# ADR-003 — Adapter registration interface and actuator-credential custody

- Status: Accepted for PR 2 (OT-003 + OT-004); governed-dispatch evidence pending
- Date: 2026-10-26
- Decisions resolved: D-03 (adapter registration interface), D-05 (setpoint limits)
- Related: SDD-OT-001 §3, §4, §8; INV-01, INV-02, INV-03, INV-05, INV-11

## Context

PR 2 adds the REMORA-governed safety boundary without reimplementing any part of
REMORA. Two questions had to be settled: (1) how the deployment-owned OT tool
callables are installed into REMORA's protected execution runtime, and (2) where
the twin's actuator credential lives so that only REMORA's dispatcher can reach
the simulated write route.

## Decision

**Registration (D-03).** The adapter exposes `adapter.registry.register()`,
discovered by REMORA through its deployment registry hook
(`REMORA_TOOL_REGISTRY_MODULE`). It depends on a minimal, duck-typed `Registrar`
protocol — `register_tool(tool_name, effect, callable, spec)` — and registers
exactly the three frozen v1 tools, giving the actuator-backed callable only to
`set_zone_setpoint`. The adapter constructs no grant, lease or policy; REMORA
authorizes and binds every call (INV-03, INV-11). The concrete registrar shape at
the pinned commit is treated as an **integration risk**: it must be reconciled by
a test against the running REMORA execution image before the Docker image's
governed-dispatch claim is promoted beyond `IMPLEMENTED`.

**Limits (D-05).** The signed ToolSpec (`contracts/ot-toolspec-v1.json`) declares
hard bounds and cumulative limits for `set_zone_setpoint`. These must equal the
twin's local interlock config; `deploy/scripts/preflight.sh` fails the build on
any divergence, and a unit test asserts parity. The ToolSpec is deployment-owned
and signed at deploy time (HMAC attestation with `OT_TOOLSPEC_SIGNING_KEY`, never
committed); REMORA's own binding of the signed spec remains authoritative.

**Custody (OT-004).** `TWIN_ACTUATOR_TOKEN` is injected only into the `twin`
service (to verify) and the `remora` service (to present). Only those two join
the `plant-private` network, on which the actuator listener binds a fixed address.
The proposing runner, Workers and contributor containers receive neither the
credential nor a route. This is pinned by structural tests so it cannot regress.

## Consequences

- The simulator + boundary demo (default profiles) still builds and runs with no
  REMORA access; the governed path is opt-in behind the `governed` profile and
  honestly carries `NOT_ESTABLISHED` until the REMORA image is built and the
  dispatcher effect is observed.
- A single source of truth for setpoint limits (guard config) is enforced across
  the ToolSpec, preflight and tests, removing a class of silent divergence.
- The registration protocol is intentionally minimal; if the pinned REMORA
  registrar differs, the reconciliation happens in one place (`adapter/registry`)
  plus an integration test, not scattered through the codebase.

## Alternatives rejected

- Reimplementing REMORA's dispatcher/lease locally (violates INV-11; forbidden by
  SDD §14).
- Letting the proposing runner hold the actuator token for a "direct" fast path
  (violates INV-01/INV-02; defeats the entire boundary).
- Treating `remora.enforcement` internals as a library import (not the supported
  surface; brittle and unsafe).
