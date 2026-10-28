# SDD-OT-002 implementation status

Requirements and findings: [docs/SDD-OT-002.md](../SDD-OT-002.md).

A finding is **closed** only when two things are on `main`: its fix, and an acceptance test that fails
without the fix. Until then it is **open**. "Not now" means a deliberate decision for the simulator phase,
with the reason given.

Order of work, as decided for this phase:

1. SEC-01.
2. SEC-02 and the critical twin interlocks of SEC-03.
3. The A/B/C additions:
   - attacks on authority, signed work orders and a separate reviewer (a bounded SEC-07);
   - all eight oracle rules attacked;
   - AT-09 and AT-12 fault injection (**done**: both PASS on the governed stack).
4. Simulator fidelity (PR #30 with holdout validation, the hourly model).

| WP | Gate | State | Evidence |
|---|---|---|---|
| SEC-01 Federation sandbox (SR-01, SR-02, SR-03) | G-A | **closed** | `runner/edge_sandbox.py`; ST-01..ST-03 in `tests/unit/test_edge_sandbox.py` (each fails without its fix). The template and Probity edges pass v1 in the sandbox, and `edge_pass` runs both sandboxed on the 14 trails of `observer-e2e-20261028T012704Z` |
| SEC-02 Dispatch and evidence correctness (SR-09, SR-10, SR-11, SR-23) | G-A | **closed** | ST-09 `tests/unit/test_ot_registry.py` (setpoint and plant tools; the adapter's request digests equal the twin's); ST-10 `tests/unit/test_acceptance_transport.py` (AT-08/08b/11 FAIL with REMORA down; without the guard AT-11 passed an outage); ST-11 `tests/unit/test_verifier_ordering.py`; ST-21 `tests/unit/test_report_html_escaping.py`. Each fails without its fix |
| SEC-03 Twin interlocks and durable state (SR-13, SR-14, SR-15) | G-A | **closed** | ST-13 `tests/unit/test_twin_interlocks_symmetric.py`; ST-14 `tests/unit/test_twin_state_integrity.py` (HMAC under `TWIN_SNAPSHOT_KEY`, re-validation without it, persist failure rolls back and fails closed). The P3 caretaker meets no new IL-01 refusal (28 days, NO1 and NO4), so published results are unaffected. Governed acceptance suite on the real stack with the key set: 14/14 PASS. ST-15 `tests/unit/test_twin_request_limits.py` (22 of its 27 cases fail on the old code; the other five guard against regressions): each listener authenticates before it reads a body (401, body unread) and reads at most 16 kB (413, chunked bodies counted too). Each plant tool has a typed argument model (`extra="forbid"`, no NaN or infinities, bounded names); bounds and interlocks stay in the guard and still answer with a REJECTED record. A 422 names the fields and never echoes the input, so a NaN can no longer turn a 422 into a 500. A refused operation keeps its request digest, not the caller's arguments: 10,000 refusals grow the snapshot by under 1 kB each. The log is bounded at `MAX_OPERATIONS` and then refuses new operations (503) without evicting any, so no operation id can be replayed. uvicorn runs with limits on concurrency, backlog, keep-alive and header size. The operation log is an append-only journal next to the snapshot (`tests/unit/test_twin_journal.py`, 14 of 15 fail on the old code): one chained line per operation (HMAC under `TWIN_SNAPSHOT_KEY`, an unkeyed hash chain without it), so the snapshot no longer grows with the log; 400 operations leave it within 256 bytes. The snapshot is written first and carries the newest record, so a crash before the journal line is recovered from it on restart (event `journal_recovered`); a torn last line counts as absent; any other change to the journal refuses the resume. The snapshot itself is still rewritten and fsynced once per operation and per clock step, now at a fixed size. The compose stacks set no `TWIN_SNAPSHOT_PATH`, so the journal is tested in the unit tests, not on the running stack. Governed acceptance suite after SR-15: 14/14 PASS |
| SEC-04 Supply chain and image hygiene (SR-21, SR-22) | G-A | **closed**, except SBOM and image signing | `tests/unit/test_supply_chain.py`, which fails on the old files (7 of 7). The REMORA image installs everything from `deploy/ot/requirements-remora.lock` with `--require-hashes`; ST-20 was checked by hand: with both of fastapi's hashes changed, the build fails. Base images and postgres are pinned by digest, Actions by commit SHA, and Dependabot covers pip, docker and actions. `remora` runs under `x-hardening` with a read-only root and root-owned `/app`; postgres is the documented exception, because its entrypoint switches user. The governed acceptance suite passes 14/14 with the hardened `remora`. Open: an SBOM and cosign signatures, which wait until images are published |
| SEC-05 Signed run records (SR-04) | G-A | open | |
| SEC-06 One source of truth for contracts (SR-07, SR-08, SR-18) | G-C | open | |
| SEC-07 Signed work orders and separated approver (SR-05, SR-06, SR-12) | G-C | open; a bounded version is planned for the testbed | |
| SEC-08 Service identity and secrets (SR-19, SR-20) | G-C | not now | mTLS and PKI wait for distributed staging |
| SEC-09 Operations (SR-24) | G-C | not now | |
| SEC-10 LLM proposer threat model | G-C | not now | No LLM proposer is planned before SEC-07 |
| SEC-11 Safety supervisor (SR-16, SR-17) | G-E | not now | G-E |
| SEC-12 Adapter SDD input (SR-25) | G-E | not now | No physical adapter in this phase |
| SEC-13 External review | G-C | not now | |

## Findings

| Finding | Severity | WP | State |
|---|---|---|---|
| F-01 Docker argument injection via `entrypoint.image` | Critical | SEC-01 | **closed** (ST-01) |
| F-02 Writes through a dangling symlink in the edge output | High | SEC-01 | **closed** (ST-02) |
| F-03 Edges run as host subprocesses | High | SEC-01 | **closed** (ST-03) |
| F-10 Replay returns APPLIED before comparing arguments | Medium | SEC-02 | **closed** (ST-09) |
| F-11 Transport error counted as a refusal | Medium | SEC-02 | **closed** (ST-10) |
| F-15 Observer re-dates `verified_at` | Medium | SEC-02 | **closed** (ST-11) |
| F-24 Unescaped HTML report | Low | SEC-02 | **closed** (ST-21) |
| F-07 IL-01/IL-09 checked on one side only | High | SEC-03 | **closed** (ST-13) |
| F-08 Snapshot restored without integrity or re-validation | High | SEC-03 | **closed** (ST-14) |
| F-18 Effect applied before persist | Medium | SEC-03 | **closed** (ST-14) |
| F-04 REMORA image installs unpinned | High | SEC-04 | **closed** |
| F-20 Hardening gaps (remora without anchor, floating tags, Actions by tag, narrow Dependabot and preflight, `.env.tmp`) | Medium | SEC-04 | **closed**, except SBOM and image signing |
| F-17 Twin robustness (unbounded refused-op records, body read before auth, no limits, NaN 500s) | Medium | SEC-03 (SR-15) | **closed** (ST-15) |
| F-05, F-06, F-09, F-12 … F-14, F-16, F-19, F-21 … F-23, F-25 | | SEC-05 … SEC-13 | open (see [SDD-OT-002](../SDD-OT-002.md) §3) |
