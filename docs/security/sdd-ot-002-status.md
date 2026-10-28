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
   - AT-09 and AT-12 fault injection.
4. Simulator fidelity (PR #30 with holdout validation, the hourly model).

| WP | Gate | State | Evidence |
|---|---|---|---|
| SEC-01 Federation sandbox (SR-01, SR-02, SR-03) | G-A | **closed** | `runner/edge_sandbox.py`; ST-01..ST-03 in `tests/unit/test_edge_sandbox.py` (each fails without its fix). The template and Probity edges pass v1 in the sandbox, and `edge_pass` runs both sandboxed on the 14 trails of `observer-e2e-20261028T012704Z` |
| SEC-02 Dispatch and evidence correctness (SR-09, SR-10, SR-11, SR-23) | G-A | open | |
| SEC-03 Twin interlocks and durable state (SR-13, SR-14, SR-15) | G-A | open | |
| SEC-04 Supply chain and image hygiene (SR-21, SR-22) | G-A | open | |
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
| F-04 … F-25 | | SEC-02 … SEC-13 | open (see [SDD-OT-002](../SDD-OT-002.md) §3) |
