# SDD-OT-002: Security hardening from simulated testbed to operational software

- **Status: PROPOSED** (2026-10-28).
- **Origin:** an external review (three independent read-only reviews) of commit `d8439c6`.
- **Implementation status:** [docs/security/sdd-ot-002-status.md](security/sdd-ot-002-status.md).
  A finding stays open until its fix and its acceptance test are on `main`.

This SDD lists what must change in code, keys, transport, deployment and process before the testbed can be
offered as commercial software that touches real building OT. It extends [SDD-OT-001](SDD-OT-001.md), whose
INV-01 to INV-12 stay in force.

In the current phase the testbed stays a simulator. The work to do now is the part that makes it safe for
external federation participants to test against (gate G-A), plus selected G-C items that make the
evidence it produces sharper. G-E items are scoped here, not scheduled.

## 1. Scope and bar

The bar moves from "no bypass inside one Docker deployment" to "a customer can run this against their
building and a third party can audit it". SDD-OT-001 Gate E (physical integration) stays a separate safety
case.

| Dimension | Testbed today | Commercial / operational bar |
|---|---|---|
| Deployment | One host, one compose stack, internal networks, operator is the author | Customer or Luftfiber-hosted, multi-site, multi-tenant, operated by people who did not write it |
| Identity | Three static bearer tokens generated into `.env` | Per-service identity (mTLS or signed tokens), rotation, revocation, no long-lived shared secrets |
| Transport | Plain HTTP between containers; isolation by Docker network only | Authenticated and encrypted on every hop |
| Keys | Ed25519 work-order signer and HMAC ToolSpec key supplied at deploy time | Managed key custody, key ids in every signature, documented rotation |
| Actuator | Simulated twin; interlocks in the same process | Real BMS/WebPort adapter in its own trust domain with independent field interlocks |
| Agent | Deterministic strategies (P0–P3) | LLM proposer is likely: prompt injection, tool-definition drift and memory poisoning become live risks |
| Evidence | Hash-chained records, offline verifiers, run records in git | Tamper-evident storage with retention, exportable for incident review and regulator |
| Assurance | OWASP-agentic tests, AT-01..13, federation edges | IEC 62443-4-1 style SDL, SBOM and signed images, pen test, NIS2 incident process |

**In scope:** `twin/`, `runner/`, `adapter/`, `deploy/`, `federation/`, `.github/` and the contracts.

**Out of scope:** REMORA internals (INV-11: changes go upstream), the physical safety case, and commercial
terms.

**Severity:**

| Severity | Meaning |
|---|---|
| Critical | An unauthorised or duplicate effect is possible, or a credential can be compromised |
| High | A control that the product's security story depends on is missing or bypassable |
| Medium | A defence-in-depth gap or an operational weakness |
| Low | Hygiene |

## 2. What is already strong

These controls are kept as they are. The rest of this SDD builds on them.

- Credential custody and the three-network split (AT-05).
- Refuse-only composition (INV-10, ASI09).
- Fail-closed behaviour on edge timeout, store outage, unknown tool or missing limits (INV-05, AT-11).
- Single-use operation ids, with replay tests after restart (AT-04), and the twin snapshot v2 fix.
- Signed ToolSpec v5 pinned by digest, signed cumulative limits, and Ed25519-signed work orders.
- Observer binding.
- Supply chain hygiene: `--require-hashes`, a locked REMORA commit, Dependabot, an AST scan, and hardened
  containers.
- Honest evidence levels.

## 3. Findings at d8439c6

| ID | Sev. | Area | Finding | Where | Failure scenario |
|---|---|---|---|---|---|
| F-01 | Critical | Federation sandbox | `entrypoint.image` from a contributed `edge.json` is placed unvalidated in `docker run` flag position; `validate_edge_manifest` checks only `command` | `deploy/scripts/run-edge.sh`; `runner/edge_check.py` | `image: "--user"` plus flags in `command` gives root with the host root filesystem on the CI runner, and on the maintainer's workstation (`.env`, `deploy/.keys/`, Docker socket) |
| F-02 | High | Federation sandbox | Edge output dir is `chmod 777` and mounted rw; host-side `exists()` is false for a dangling symlink, so the no-overwrite guard passes and `write_text` follows the link | `run-edge.sh`; `edge_check.py` | Edge creates `/out/report.md -> ~/.ssh/authorized_keys` |
| F-03 | High | Federation sandbox | Every registered edge is executed as a plain host subprocess by `edge_pass`/`edge_mutations`, which `run-demo-governed.sh` and `run-abc.sh` call after each governed run | `runner/edge_pass.py`; `runner/edge_check.py` | Any merged edge runs arbitrary code with the operator's environment |
| F-04 | High | Supply chain | The REMORA image (holds the actuator token) installs REMORA extras from PyPI without pins or hashes | `deploy/Dockerfile.remora-ot` | A compromised transitive release lands in the dispatcher |
| F-05 | High | Authority | Work orders are an unsigned JSON table rewritten by the proposing host process; REMORA labels the result `signed_work_order:<sha256>` although nothing is signed | `deploy/ot/ot_bundle.py`; `runner/demo.py` | A compromised proposer publishes a `PLAN-<day>` with any in-contract values |
| F-06 | High | Authority | In the runs that produce the A/B/C and P3 evidence, the approver is the same host process as the proposer (harness holds OPERATOR+APPROVER+VERIFIER+VIEWER) | `runner/demo.py`; `compose.governed.yaml` | Self-approval; the OWASP table's "operator token only" claim holds for the governed CLI, not for harness |
| F-07 | High | Twin interlocks | IL-01 deadband is checked only when cooling is written; IL-09 tank check only on `set_heat_pump_limit` | `twin/guard.py` | cooling 24→23, then heating 21→22→23: simultaneous heating and cooling |
| F-08 | High | Twin state | Snapshot is restored without integrity check and without re-validating setpoints, operations, stop switch or rate history | `twin/model.py` | Doctored twin-state volume: setpoint 45 °C resumes silently, past op ids replayable, stop switch released |
| F-09 | High | Transport | No TLS on any listener; tokens, signing keys and the PG DSN in cleartext and in container env | `twin/server.py`; `compose.governed.yaml` | Acceptable inside one compose host; not on a building LAN |
| F-10 | Medium | Dispatch | Replay short-circuit returns APPLIED from `/v1/operations/<id>` before comparing the arguments now being dispatched | `deploy/ot/ot_registry.py` | Re-invocation with different arguments records a success receipt |
| F-11 | Medium | Evidence | AT-08/08b/11 treat a transport error (`status: None`) as a refusal | `runner/acceptance.py`; `runner/harness_step.py` | PASS for refusals that never occurred |
| F-12 | Medium | Dispatch | Dispatcher clamps out-of-contract setbacks (16–18 °C) to 19.0 instead of refusing, and does not attach the caretaker decision record | `runner/demo.py` | The evidence says the validator approved X, the plant received Y |
| F-13 | Medium | Contracts | Site policy and fallback "hash pins" are computed from whatever file is on disk | `runner/caretaker.py` | Edit `site_policy.v1.json` on the proposer host |
| F-14 | Medium | Contracts | Bounds for 9 of 10 tools hardcoded in three places; signed ToolSpec per-tool limits not read; adapter spec unsigned and selected by env var | `adapter/contracts.py`; `deploy/ot/sign_deployment.py` | Tightening the contract file changes nothing |
| F-15 | Medium | Evidence | Observer rewrites `verified_at` to `max(t_disp, now)` when the observation precedes dispatch | `adapter/verifier.py` | "Observed after dispatch" is asserted, not observed |
| F-16 | Medium | Evidence | Run records are unsigned; a hand-written L4 with `run_ref: null` validates (one is in-tree) | `runner/edge_check.py` | A contributor claims an external PASS that never ran |
| F-17 | Medium | Twin robustness | Unbounded growth of rejected-op records with raw arguments; snapshot rewrite and fsync per op; body parsed before auth; no uvicorn limits; NaN in args commits then 500s on replay | `twin/model.py`; `twin/api.py`; `twin/server.py` | O(n²) disk, memory pressure, health flaps |
| F-18 | Medium | Twin durability | Effect applied in memory before persist; a persist failure returns 500 after the plant changed and leaves the op id reusable after restart | `twin/model.py` | INV-04 broken across a crash window |
| F-19 | Medium | Twin interlocks | Interlocks are evaluated at write time only; `alarm_freeze` also blocks the corrective write; fixture bounds outside the band | `twin/guard.py` | Governed path to an IAQ or icing hazard; twin locks itself out |
| F-20 | Medium | Hardening | `remora` lacks `x-hardening`; base images by floating tag; Actions pinned by tag; Dependabot covers one edge dir; preflight scans only `TWIN_*`; `.env.tmp` 0644 and not ignored | compose, Dockerfiles, `.github/`, scripts | Persistence after compromise; tag-move supply chain |
| F-21 | Medium | Twin ops | Stop switch engage/release share one token and a free-text actor; virtual clock vs wall clock; occupancy from schedule | `twin/api.py`; `twin/guard.py` | Must change before a real adapter |
| F-22 | Low | Evidence | `manifest_extra` can override counts/claims; bundles unsigned; `run_id` accepts `..` | `runner/evidence.py` | Consistent rewrite by anyone with file access |
| F-23 | Low | Limits | Count-only limits for supply air and tank bound frequency, not magnitude | `deploy/ot/ot_limits.policy.json` | Creep by legal steps on non-zone tools |
| F-24 | Low | Reports | `report_html.py` interpolates fields without escaping | `runner/report_html.py` | Stored XSS in the evidence report |
| F-25 | Low | Misc | Inline-edge budget per phase; edge URL unauthenticated; wildcard-bind guard misses `0`, `::0`; `compare_digest` on unequal lengths; health leaks `sim_time_s`; risk tier mismatch | various | Hygiene |

Two observations hold across all three reviews.

- Several controls protect a boundary that the evidence-producing path does not use (F-03, F-06, F-14).
- The federation kit is the largest attack surface, because it is where third-party code is invited in
  (F-01, F-02, F-03, F-16).

## 4. Security requirements (normative)

Gates: **G-A** must hold before the next external federation round. **G-C** must hold before hosted staging
(SDD-OT-001 Gate C). **G-E** must hold before any physical adapter is written.

| SR | Gate | Requirement | Closes |
|---|---|---|---|
| SR-01 | G-A | Every field of a contributed `edge.json` that reaches a shell, a docker argument or a file path is validated against an allowlist before use. `entrypoint.image` matches `^[a-z0-9][a-z0-9./_-]*(:[A-Za-z0-9_.-]+)?@sha256:[0-9a-f]{64}$`, equals `pin.value` when `pin.kind == image_digest`, and never begins with `-` | F-01 |
| SR-02 | G-A | The edge sandbox writes to a volume or tmpfs that is copied out after exit. The host refuses any symlink, hard link or non-regular file in the output, opens result files with `O_NOFOLLOW` and outputs with `O_CREAT\|O_EXCL` | F-02 |
| SR-03 | G-A | No federation adapter executes on a host that holds a credential. `edge_pass`, `edge_mutations` and `edge_check --run` use the same container sandbox, or refuse unless the edge id is on an explicit `--trust-edge` allowlist | F-03 |
| SR-04 | G-A | Run records carry an Ed25519 signature (maintainer key L0–L2; contributor key from `edge.json` for L3–L4); `run_ref` mandatory for any EXTERNAL claim; edge and cases cross-checked against the in-tree manifest | F-16 |
| SR-05 | G-C | Work orders are signed artifacts issued by an authority principal that is not the proposer; REMORA verifies signature and key id; the authority tag is `signed_work_order` only when a signature was verified, otherwise `intent_table`; the intent file is read-only to every proposing process | F-05 |
| SR-06 | G-C | The APPROVER credential never resides in a process that holds the OPERATOR credential; until then every report states "reviewer = same process as proposer" | F-06 |
| SR-07 | G-C | Out-of-contract proposals are refused and routed to fallback or escalation, never clamped; site policy validated against the signed ToolSpec at load; the caretaker decision record is attached to every governed write | F-12 |
| SR-08 | G-C | All per-tool bounds, resolutions and cumulative limits have one source, the signed contract; the adapter refuses to register on spec digest mismatch; site policy and fallback signed or pinned by expected digest | F-13, F-14 |
| SR-09 | G-A | A replay response is returned only after the canonical arguments of the current call are compared with the stored request digest; mismatch raises `operation_id_reused` | F-10 |
| SR-10 | G-A | A "refused" verdict in any acceptance or adversarial case requires an HTTP status and a decision body from REMORA; transport failure is NOT_RUN or FAIL, never PASS | F-11 |
| SR-11 | G-A | Verification records are never edited to satisfy ordering; an observation that precedes dispatch is `EFFECT_UNOBSERVABLE` and re-observed | F-15 |
| SR-12 | G-C | Evidence manifests are signed by a key the runner does not hold; reserved manifest keys cannot be overridden; `run_id` matches `^[A-Za-z0-9_-]{1,64}$` | F-22 |
| SR-13 | G-A | Cross-point interlocks are evaluated on the resulting state tuple on every write that touches either side (heating/cooling deadband, tank/heat pump) | F-07 |
| SR-14 | G-A | State persistence is a write-ahead journal (decision appended and fsynced before the effect; persist failure fatal); snapshots carry an HMAC under a dedicated key; on restore every setpoint is re-validated and a `snapshot_restored` event is emitted | F-08, F-18 |
| SR-15 | G-A | Per-tool typed argument models (`extra="forbid"`, `allow_inf_nan=False`, bounded sizes); authentication before body read; a request body cap; uvicorn limits; rejected operations store a digest, not raw arguments; operation log bounded | F-17 |
| SR-16 | G-E | A continuous safety supervisor re-evaluates hazardous states every tick; zone limits validated inside the alarm band; an operator-only override route | F-19 |
| SR-17 | G-E | Stop-switch release requires a second principal; actor bound to the credential; monotonic server clock; measured presence | F-21 |
| SR-18 | G-C | Cumulative change limits for every quantity with a per-step size (supply air, tank, heat pump) | F-23 |
| SR-19 | G-C | Every hop authenticated and encrypted (mTLS with a private CA, or TLS plus HTTP message signatures) | F-09 |
| SR-20 | G-C | Secrets delivered as files, never as container environment; `.env*` ignored and created 077; preflight scans every generated secret name; key ids in every signature; rotation documented and tested once | F-09, F-20 |
| SR-21 | G-A | All images install with `--require-hashes --no-deps`, the REMORA image included; base images by digest; Actions by commit SHA; Dependabot for pip, docker and actions; SBOM and signed images in CI | F-04, F-20 |
| SR-22 | G-A | Every container, `remora` included, runs under `x-hardening` with a read-only root and root-owned code | F-20 |
| SR-23 | G-A | Every string interpolated into an HTML report passes through the escaper; CSS classes from a fixed map | F-24 |
| SR-24 | G-C | STOP conditions wired to alerts; an incident runbook; a published vulnerability SLA | — |
| SR-25 | G-E | The physical adapter is its own trust domain and validates every command against its own bounds and interlocks | — |

## 5. Work packages

| WP | Title | Requirements | Gate | Effort (d) | Done when |
|---|---|---|---|---:|---|
| SEC-01 | Federation sandbox hardening | SR-01, SR-02, SR-03 | G-A | 1.5 | A hostile `edge.json` (flag injection, dangling symlink, host-run adapter) is refused in `tests/unit/test_edge_sandbox.py`; `run-demo-governed.sh` runs edges in containers |
| SEC-02 | Dispatch and evidence correctness | SR-09, SR-10, SR-11, SR-23 | G-A | 1.5 | Replay with different args raises; AT-08/08b/11 fail when REMORA is down; `verified_at` never rewritten; report escaper test |
| SEC-03 | Twin interlocks and durable state | SR-13, SR-14, SR-15 | G-A | 4 | Symmetric IL-01/IL-09 tests; WAL journal with crash test; HMAC snapshot with tamper test; typed per-tool models; body and uvicorn limits; load test at 10k rejected ops |
| SEC-04 | Supply chain and image hygiene | SR-21, SR-22 | G-A | 2 | REMORA image builds with `--require-hashes`; base images by digest; Actions by SHA; Dependabot for pip/docker/actions; SBOM and cosign; `remora` under `x-hardening` |
| SEC-05 | Signed run records | SR-04 | G-A | 2 | Unsigned or `run_ref: null` EXTERNAL record fails `validate_record`; the in-tree record re-recorded or downgraded |
| SEC-06 | One source of truth for contracts | SR-07, SR-08, SR-18 | G-C | 3 | Hardcoded bounds removed; adapter refuses on digest mismatch; clamping removed; change limits for supply air and tank |
| SEC-07 | Signed work orders and separated approver | SR-05, SR-06, SR-12 | G-C | 5 | REMORA verifies the work-order signature; harness split into proposer and reviewer; evidence manifest signed; OWASP table corrected |
| SEC-08 | Service identity and secrets | SR-19, SR-20 | G-C | 4 | mTLS in compose; secrets via files; rotation runbook executed once |
| SEC-09 | Operations: incident, SLA, alerting | SR-24 | G-C | 2 | STOP conditions emit alerts; runbook; SLA |
| SEC-10 | LLM proposer threat model | ASI01/ASI06 | G-C | 2 | Threat model, tool-definition digest per call, no write path from the model to the intent table, adversarial benchmark with injection payloads |
| SEC-11 | Safety supervisor and operator controls | SR-16, SR-17 | G-E | 5 | Continuous re-evaluation; override route; two-person release; monotonic clock; measured occupancy |
| SEC-12 | Adapter SDD input | SR-25 | G-E | 1 | A design input for the Gate E SDD |
| SEC-13 | External review | all G-A/G-C | G-C | — | A third party reviews the authority chain and sandbox |

## 6. Acceptance tests

Every test is a negative that must fail before its fix and pass after.

| Test | SR | Setup | Expected |
|---|---|---|---|
| ST-01 | SR-01 | `edge.json` with `image: "--user"` and flags in `command` | `validate_edge_manifest` lists the problem; nothing runs `docker run` |
| ST-02 | SR-02 | The edge writes `/out/report.md` as a dangling symlink | Host refuses; target does not exist afterwards |
| ST-03 | SR-03 | Edge adapter that writes a marker file to `$HOME` | `edge_pass` on a governed run: marker absent, edge reported as sandboxed |
| ST-04 | SR-04 | L4 record, `run_ref: null`, no signature | `validate_record` fails with `unsigned_external_claim` |
| ST-05 | SR-05 | Work order unsigned or signed by the proposer's key | REMORA refuses with `authority_unverified` |
| ST-06 | SR-06 | Structural test over `compose.governed.yaml` | No service lists both OPERATOR and APPROVER tokens |
| ST-07 | SR-07 | Caretaker proposes 17.0 °C with ToolSpec minimum 19.0 | No dispatch; `refused:out_of_contract`; revision unchanged |
| ST-08 | SR-08 | Edit cooling max to 26.0 in the contract only | Signer, adapter and twin all read 26.0; stale pinned digest refuses to start |
| ST-09 | SR-09 | Dispatch P twice, the second with a different setpoint | Second raises `operation_id_reused`; no APPLIED receipt |
| ST-10 | SR-10 | AT-08, AT-08b, AT-11 with REMORA stopped | All NOT_RUN or FAIL; zero PASS rows |
| ST-11 | SR-11 | Observation 5 s before dispatch | `EFFECT_UNOBSERVABLE`; `verified_at` equals the observation's `observed_at` |
| ST-12 | SR-12 | `write_bundle(manifest_extra={"counts": {...}})` | Reserved key refused; manifest signature verifies |
| ST-13 | SR-13 | cooling 24→23, then heating 21→22, then 22→23 | Second heating write refused (IL-01); tank raise while `hp_max_fraction < 1` refused (IL-09) |
| ST-14 | SR-14 | Doctored snapshot; kill between journal append and effect | Restore refuses (HMAC) or re-validates; the op id returns the journaled decision |
| ST-15 | SR-15 | 500 kB arguments, NaN value, list `zone_id`, unauthenticated malformed JSON | 413 / 422 / 422 / 401 before body parse; snapshot grows by less than 1 kB per rejected op |
| ST-16 | SR-16 | AHU disabled while unoccupied, then occupancy begins | Supervisor restores or alarms within one tick |
| ST-17 | SR-17 | Release attempted by the engaging principal | Refused; a second principal succeeds |
| ST-18 | SR-19 | Bearer token without a client certificate | TLS handshake refused |
| ST-19 | SR-20 | `docker inspect` for TOKEN= and SIGNING_KEY= | No matches |
| ST-20 | SR-21 | Build the REMORA image with a lockfile hash altered | Build fails; SBOM and signature published |
| ST-21 | SR-23 | Result with `outcome: "x\" onmouseover=\"alert(1)"` | Escaped; class from the fixed map |
| ST-22 | SR-18 | Four 1.0 K supply-air steps in one hour | Fourth refused by cumulative change limit |

ST-01..ST-04, ST-09..ST-11, ST-13..ST-15 and ST-20..ST-22 are G-A.

## 7. Decisions and open questions

| ID | Decision | Recommended default |
|---|---|---|
| D-10 | Service identity | mTLS with a private CA from the bootstrap script |
| D-11 | Work-order authority principal | A separate authority service holding the issuer key |
| D-12 | Approver in a commercial deployment | Customer-side reviewer with its own token and host |
| D-13 | Key custody | `/run/secrets` for compose; KMS or HSM for hosted |
| D-14 | Federation trust model | Open participation; sandbox always, signed records, image allowlist, a maintainer L0 reproduction before an external record is published |
| D-15 | Evidence retention | Append-only object storage for hosted; hash chains locally |
| D-16 | LLM proposer | Not before SEC-07 and SEC-10 |
| D-17 | REMORA changes upstream | Yes, per INV-11 |

Open questions:

- Does REMORA at pin `123a5fd0` expose a hook to verify a work-order signature at assess time?
- Can the harness split be done without duplicating the demo's day loop?
- Which framework will the first pilot be asked for?
- Should the in-tree L4 record be downgraded to L2 until re-run under SR-04, or re-recorded with the
  contributor's signature?

Compliance mapping (IEC 62443-3-3, 62443-4-1/4-2, NIS2 Art. 21/23, OWASP agentic, GDPR) is in the source
document. It is approximate and meant to steer the work packages, not to replace a gap assessment.
