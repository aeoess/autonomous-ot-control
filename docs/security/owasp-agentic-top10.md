# OWASP Top 10 for Agentic Applications: how the testbed answers each risk

Tests: [`tests/unit/test_owasp_agentic_top10.py`](../../tests/unit/test_owasp_agentic_top10.py). That
file has 37 tests, offline, on the real code paths. Five of the controls were weakened one at a time
(the learner's bound, refusal-only composition, fail-closed edges, the actuator credential, the
work-order signature), and in each case the test for it failed. Docker-only properties are covered by the
governed acceptance suite (`runner.acceptance`, `AT-xx`, CI job `governed-e2e`).

Scope: a simulated plant and one deployment seen from inside it. Passing these tests shows the controls
behave as stated here. It does not establish physical OT safety, and it does not cover a language-model
agent (the proposers in this repository are deterministic strategies).

| ID | Risk | The testbed's control | Tested by | Not covered |
|---|---|---|---|---|
| ASI01 | Agent Goal Hijack | Decisions read structured fields only: the oracle (`runner.oracle`), the inline edge's work order and step, the careful reviewer's day plan, the work-order check's signed fields. Injected free text (reasons, task text, extra context) is carried, never obeyed. A widened work order fails its Ed25519 signature. | `test_asi01_*` (3) | An LLM proposer reading untrusted documents. REMORA's own intent resolution from `task_text` is REMORA's to test. |
| ASI02 | Tool Misuse | The signed ToolSpec refuses out-of-bounds, off-resolution, unknown-zone, non-numeric and extra (`override_interlock`) arguments. The twin interlock refuses large steps without touching the plant. The oracle's movement budget catches creep by small legal steps. The caretaker's independent validator keeps a defective optimizer off the plant. | `test_asi02_*` (4); AT-08, AT-08b, AT-13 | — |
| ASI03 | Identity & Privilege Abuse | One credential per surface: read cannot actuate, actuator cannot control, and nothing works without a token. Only REMORA's dispatcher holds `TWIN_ACTUATOR_TOKEN`. The proposing runner (the `harness` container) holds the operator, viewer and verifier tokens, never the approver's: that one is only in the `reviewer` container (SDD-OT-002 SR-06; before it, the harness held both, F-06). Work orders count only when the work-order authority signed them (SR-05). Edges never receive a credential. A delegation that widens its parent is refused. | `test_asi03_*` (4); AT-02 (the operator cannot approve), AT-05 (no route to the actuator) | Key rotation and revocation. REMORA's `revoke-principal` is REMORA's suite. |
| ASI04 | Agentic Supply Chain | Every locked dependency is pinned by SHA-256 (`--require-hashes`). REMORA is built from the locked commit and checked after the fetch. A contributed verifier refuses to run when its vendored file is changed. A changed tool contract fails its attestation. The customer MCP surface has no tool with a plant effect and no execute route. | `test_asi04_*` (5); `tests/unit/test_supply_chain.py` (SDD-OT-002 SEC-04); CI `--require-hashes`; Dependabot for pip, docker and actions; Dockerfile `rev-parse` check | An SBOM and signed provenance (SLSA, cosign) for built images: the images are built in CI and never published, so there is nothing to sign yet. |
| ASI05 | Unexpected Code Execution | No `eval`, `exec`, `compile`, `__import__`, `pickle`/`marshal` loads, `yaml.load`, `os.system`/`popen` or `shell=True` in the code that handles agent or edge input (an AST scan of `runner/`, `adapter/`, `twin/`, `deploy/ot/`, `federation/edges/`, `federation/authority/`). Verifiers run with `--network none`, read-only, no capabilities, non-root and resource-limited. Code-shaped values are refused as data. | `test_asi05_*` (3); `tests/unit/test_edge_sandbox.py` (SDD-OT-002 SEC-01) | The testbed's own stand-in authority verifier runs on the host (it imports the testbed); every other edge runs in the container sandbox (`runner.edge_sandbox`), and does not run at all without Docker. |
| ASI06 | Memory / Context Poisoning | The optimal-start learner rejects physically implausible reheat rates (above `MAX_PLAUSIBLE_REHEAT_K_PER_H` = 4 K/h; the episode learns 0.24 to 0.42) and counts them. Whatever it holds, the preheat lead stays within 1 to 8 h. A rewritten exported trail is caught by its capture digest. A changed sealed set or a drifted episode pin is detected. | `test_asi06_*` (4) | No RAG store or long-term agent memory exists here to poison. The learner's state is not persisted across runs. |
| ASI07 | Insecure Inter-Agent Communication | Single-use operation ids at the twin: a replay has no second effect, and a replay with other arguments is a conflict. Work orders are Ed25519-signed, so a spoofed signer is refused. The observer's record is sealed. A garbled, non-JSON or failed edge answer counts as a refusal, never as consent. | `test_asi07_*` (4); AT-03 (tampering after approval), AT-04 (replay, also after a REMORA restart) | Transport authentication between the containers beyond bearer tokens (no mTLS in the compose stack). |
| ASI08 | Cascading Failures | An unreachable edge fails closed and is reported as `edge_unavailable`. An unreachable plant is `EFFECT_UNOBSERVABLE`, never verified. An unavailable shadow edge changes nothing and is counted apart. A crashing verifier does not stop the others (`edge_pass`; per-operator fallback in `edge_mutations`). | `test_asi08_*` (4); AT-10 (stop switch), AT-11 (store outage: no effect, no blind resend), AT-12 (timeout after actuation: unknown, one effect, reconciled from the plant, never repeated) | — |
| ASI09 | Human-Agent Trust Exploitation | No approval and no edge can turn a refusal by REMORA or the reviewer into an effect. The careful reviewer approves only the plan's own values, whatever the agent explains. The cost of a rubber-stamping human is measured (A/B/C arm `B-rubber_stamp`). Every report states what it does not establish, and strict vantage is always shown. | `test_asi09_*` (3); `docs/results/2026-10-28/abc-e2e.md` | How much real operators trust fluent explanations is a human-factors question that no test here can settle. |
| ASI10 | Rogue Agents | The oracle judges every proposal from the work orders and the limits alone, whatever an agent or a layer claims. The proposer dispatches only on REMORA's ACCEPT, through REMORA's token, and never approves itself. A rogue proposal stream meets the same contract and interlock. Arm A measures what an ungoverned agent does. | `test_asi10_*` (3); `test_governed_runner.py`; A/B/C arm A | Detecting a rogue agent's intent before it proposes. The testbed contains it instead. |

## Adversarial benchmark

The ASI01, ASI02, ASI07 and ASI10 techniques are also run as an adversarial benchmark against the automation:
9 attack families and an adaptive adversary, 25,200 attempts against five defence stacks, with the oracle as
ground truth: [adversarial-benchmark.md](adversarial-benchmark.md). It found one thing these tests do not:
in-policy abuse (plan values at the wrong time) starves the legitimate schedule of the plant's change budget,
and only a reviewer that checks the strategy's own step prevents it.

## Changes made with these tests

- **ASI06.** `runner.demo.ReheatLearner` previously folded any observed reheat rate into its memory. It
  now rejects rates above 4 K/h and records them in `rejected`. The committed episode is unchanged
  (`runner.episode check` passes), because no real preheat comes near the bound.

## Keeping it current

Each section of the test file names its control. When a control moves, the matching test must move with
it, and a failing test here is a security regression until shown otherwise.
