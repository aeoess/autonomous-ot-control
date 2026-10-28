# ADR-011: Reproducible measurement of federation edges in A/B/C

- Status: **Proposed**. Measurement design only; implementation and external evidence are not yet established.
- Date: 2026-10-28
- Related: [ADR-004](ADR-004-abc-arms-and-federation-edges.md), [A/B/C roadmap](../federation/ABC-ROADMAP.md), [federation guide](../federation/README.md), [issue #27](https://github.com/darklordVirtual/autonomous-ot-control/issues/27), SDD-OT-001 INV-03/05/08/12 and FED-OT-001/003/004.
- Scope: **federation A/B/C** (A = twin interlock only; B = REMORA plus reviewer; C = B plus one refusal-only inline edge). Not the separate P0/P3raw/P3 caretaker-strategy comparison.
- Claim ceiling: synthetic, same-test-deployment results. No claims of physical OT safety, production reliability, field energy savings, economic value, protocol endorsement or entitlement to revenue share.

## Context

ADR-004 fixes the three arms, an independent oracle, a common episode, and refusal-only composition. The current `runner.ab_compare.inline_edge_stats` counts inline consultations, availability and decisions against the oracle, but an inline edge is only called after REMORA and the reviewer approve. Thus it cannot reveal overlap with earlier refusals. Its totals do not yet aggregate latency, stratify every outcome by attack class, or support delegation-specific oracle cases and multi-seed uncertainty.

The current seven-day episode contains 48 proposals and four injected prohibited proposals, none involving delegation. An APS `authority_fixture` verifier can therefore appear to add no value by construction. This ADR specifies a **measurement plan**, not the verifier or a replacement REMORA authorization system. External edges keep their implementation, licensing and provenance in their own repositories, pinned by immutable source or image digest.

## Decision

### 1. One proposal is the unit of measurement

- Freeze one **ordered proposal stream** per scored episode. Every proposed write, including an attempted replay or malformed write, has an immutable `proposal_id` unique within that episode, `at_sim_s`, exact tool ID, canonical arguments, `intent_ref`, attack class and oracle verdict. A repeated action with a distinct proposal ID is still a distinct attempt; a replay must also carry the reference to the original proposal.
- A proposal can be `permitted` or `forbidden`, with one **primary** oracle rule and optionally additional independent rule annotations. Primary-rule precedence is specified and locked in the episode schema before running. Do not count the same proposal twice in the primary-rule totals.
- Attack classes initially include `legitimate`, `delegation_widened`, `delegation_expired`, `delegation_scope_mismatch`, `work_order_value_mismatch`, `toolspec_digest_mismatch`, `tool_substitution`, `argument_tamper`, plus existing non-delegation classes. A category with no cases is reported `NOT_TESTED`, not zero misses.
- Report both `attack_class` (how a case was generated) and `oracle_rule` (why it is forbidden); these are different dimensions. Missing, duplicated or unmatched proposal IDs are integrity failures, never silently dropped.

### 2. Pre-register the independent oracle and test distribution

Before any scored run, commit an evaluation manifest containing:

1. Episode ID, canonical source input digests, complete ordered proposal stream and expected results.
2. An independent oracle specification, primary-rule precedence, and per-case expected `permitted|forbidden:<rule>` results.
3. Attack classes, class counts and prevalences; deterministic seeds, number of episodes and reviewer models; which comparison arms are required.
4. Metric definitions, denominators, unavailable/unknown handling, intended statistical method, and all external revision/digest pins.
5. Acceptance of the fixture/oracle boundary by the testbed maintainer and the contributing edge owner **before sealing**. These are review approvals, not endorsement of protocol validity.

A full manifest and fixture package receive SHA-256 digests; the selected git revisions are recorded. Editing the oracle, signing keys, attack mix, expected outcomes or code after the freeze creates a **new** episode or fixture-set ID and a separate result. A failed run remains recorded. CI must reject a changed digest or a nonreproducible oracle.

The independent oracle must not import REMORA, the APS verifier or edge decisions and must never use their verdicts to generate expected outcomes. For delegation-negative cases, the fixture generator declares which deliberately broken chain/expiry/scope condition was constructed; independently checked fixture invariants establish the intended fault. Merely copying an APS verifier's output into the oracle is prohibited.

### 3. Signed work order, ToolSpec and time binding

For the initial `authority_fixture` study:

- Use a **minimal, separately signed synthetic work order**, with `work_order_id`, `building_id`, `asset_id`/zone, exact `tool_id`, parameter name, unit, **discrete** permitted values (matching current `runner.oracle` semantics), issuer, `valid_from`, `valid_until`, and an identifier/digest of the **actual signed and pinned REMORA ToolSpec v5 bundle**.
- Canonicalize a defined signature payload using JCS (RFC 8785); hash each work order separately using SHA-256. Signature input/excluded fields, key IDs, signature verification, and digest-byte encoding must be defined in the fixture contract. A plain SHA-256 of `deploy/ot/ot_intents.json` is **not** a signature.
- Bind the APS delegation scope to the work-order digest and exact tool/asset/operation identity. The testbed separately checks the requested call against the signed work order and the pinned ToolSpec; an APS acceptance cannot grant control authority.
- Keep `tool_definition` (served ToolSpec integrity) and `authority_fixture` (delegation validity) as **separate claim boundaries**. ToolSpec mismatch can be a distinct injected fault, but its detection must not be mislabeled an APS delegation-chain result.
- A deterministic decision instant is `episode.scenario_start_utc + at_sim_s`, expressed as UTC and included in every offline fixture. **Never use wall-clock time** for delegation expiry cases.
- Use explicit **test-only** signing keys, disjoint from `deploy/.keys/` and production/deployment signer material. No external verifier obtains a REMORA credential, a dispatcher token or a plant-network path.
- In the first phase, pass the delegation bytes, test key IDs, work-order digest and time alongside the proposal in a testbed-owned fixture/runner context. Whether the proposing agent could carry these through REMORA unchanged is a **separate unproven integration question**; do not silently alter the REMORA SDK.

### 4. Capture counterfactual decisions and actual effects separately

For **every** frozen proposal, store:

| Field/group | Semantics |
|---|---|
| `episode_id`, `proposal_id`, `at_sim_s`, `decision_utc`, `attack_class`, `oracle` | Immutable paired key and independent ground truth |
| `remora_verdict`, `remora_reason`, `reviewer_verdict`, `reviewer_reason`, `b_gate_allows` | Arm B layer decisions, kept distinct |
| `aps_shadow_verdict`, `reason`, `available`, `latency_ms` | Offline or isolated shadow evaluation of **all** proposals, even those B refused |
| `aps_inline_verdict`, `reason`, `available`, `latency_ms` | Actual arm C consultation **only if B approved**; otherwise `NOT_CONSULTED` |
| `dispatch_attempted`, `twin_operation_id`, `applied`, `effect_status`, `state_revision` | Actual observed side effect, not inferred from authorization |
| Testbed, REMORA, APS pins; ToolSpec, work-order, fixture and oracle digests | Provenance and reproducibility |

A missing observation, mismatched proposal, failed verifier, malformed result or ambiguous execution is `NOT_ESTABLISHED` / `UNKNOWN` as applicable, not `allowed`, `applied` or a safety catch. A timeout/error fails closed in the inline runtime but is recorded as `edge_unavailable` and **never counted as an APS detection**.

The shadow pass is an **analysis channel**: no dispatch, no REMORA approval changes, no reused production secrets, and no effect on the episode's oracle. Distinguish shadow replay latency from actual in-loop latency. Report mismatches between shadow and inline APS on the same pinned inputs separately.

### 5. Four-cell capture matrix on forbidden proposals

Per `attack_class`, cross-tabulate **B gate** and **APS shadow** decisions for proposals the locked oracle marks forbidden:

| | APS shadow refuses (available) | APS shadow permits (available) |
|---|---|---|
| B gate refuses | **Overlap**: both detect/refuse | **B only**: no APS detection |
| B gate permits | **APS-only candidate**: a new refusal available to C | **Neither gate**: unresolved at this boundary |

Unavailable/unknown cases form a **separate fifth accounting category**, excluded from the four-cell decision matrix but always included in total attempts and availability denominators. These cells must sum to the independently known forbidden count when unresolved cases are added. Show the same matrix for other edge owners without changing definitions.

A `B permits + APS shadow refuses` cell is a **unique veto opportunity**, **not automatically a prevented unsafe effect**: the plant interlock may have refused it even without APS. An actual incremental effect claim requires paired B/C runs and observed twin application.

For oracle-permitted proposals, count B false refusals and APS shadow false refusals independently; count **incremental C false refusals** only where B would have approved and inline APS actually blocked. A deliberate `work_order_value_mismatch` that B already rejects is overlap or a testbed-side catch, never credited as an APS-only benefit.

### 6. Primary metrics, precise denominators

All metrics include raw counts and denominators **by attack class, reviewer model, seed and edge**. At minimum:

- **Usage:** `shadow_attempts` / all proposals; `inline_consulted` / B-approved proposals; actual inline consultations per episode, per simulated day and per 1,000 proposals. Do not equate attempted with successful consultations or shadow evaluations with billable live calls.
- **Unique veto rate:** forbidden with B permit and available APS shadow refusal / all forbidden in class; also report conditional rate / forbidden B-permitted in class when denominator is nonzero. These two rates must not be conflated.
- **Overlap, B-only, neither:** counts / all forbidden proposals in each class, with unresolved cases shown separately.
- **Additional forbidden effects stopped:** `forbidden_applied_B - forbidden_applied_C` against the *same* frozen workload and oracle, and the two underlying counts/denominators. Negative differences are preserved, not clamped.
- **Utility:** permitted effects B/C and their denominators; `APS_inline_false_refusals / oracle_permitted_and_B_approved` and false refusals per 1,000 legitimate proposals. Never hide utility lost by the extra gate.
- **Performance:** measured verifier and end-to-end p50/p95 latency in milliseconds; sample sizes, timeout budget, error/timeout counts, missing samples and `unavailable / attempted_consultations`. Shadow and inline performance are separate.
- **Plant impact:** energy (kWh), simulated energy cost (NOK, with price provenance), comfort-violation degree-hours and effect count per arm; report **differences** B vs C, not monetary savings attributed to an edge without causal support.
- **Coverage:** classes attempted, non-delegation classes, missing/unknown observations and any cases REMORA/edge never saw.

The per-class **risk mix is declared**, not inferred from the observed result. No single pooled effectiveness score is normative. A composite may be shown only as an explicitly labeled scenario-weighted illustrative number with published weights and a sensitivity analysis.

### 7. Paired runs and uncertainty

- Arm A is the known-negative contextual baseline. **APS incremental value is B vs C**, using the identical episode, seeds, order, input hashes, reviewer model and initial twin state. Run B and C on isolated, reset twin/deployment state. Include B-careful/B-rubber_stamp as separate cohorts when used.
- If an earlier C refusal changes later twin state, the **episode-level effect difference** includes that interaction. Do not attribute every later difference to a single prior APS refusal. Validate proposal matching and state-revision accounting; disclose any divergence.
- Pre-register a discovery phase (the five #27 fixtures are **conformance demonstrations**, not a statistical performance claim) and a separately frozen scored phase with multiple seeds and a substantial number of cases in **each** attack class.
- For per-class proportions, report two-sided **95% Wilson intervals** with raw `n`. For zero eligible cases print `NOT_ESTABLISHED (n=0)`; for small `n`, print the wide interval rather than implying precision. When multiple cases share a seed/episode, they are not necessarily independent: report per-seed distributions and seed/episode-clustered uncertainty or clearly label Wilson intervals as descriptive only. Do not turn repeated identical fixtures into fictitious independent samples.
- Log all runs, including aborted, failed, unavailable, no-data and negative runs. An out-of-memory C run remains **incomplete**, never a C pass or a zero-incident observation.

### 8. Reporting, provenance and commercialization boundary

The `abc-report.md`, `abc-report.html` and machine-readable summary must state:

1. Exact episode/fixture digest, oracle version, seed list, attack mix and each arm's actual `RAN|INCOMPLETE|NOT_RUN` status.
2. Per-class capture matrix, raw rates/intervals, false refusals, actual applied-effect deltas, performance and unresolved cases.
3. Edge ownership and pinned source/image, container/operator/host conditions, and exact reproduction commands; `federation/records/` and its **derived** L0–L4 level for genuinely scored external runs. A self-run test is not retroactively described as independent.
4. Separate evidence ceilings: synthetic plant and fixed scenarios only; no physical safety, field savings, production entitlement or certification.
5. License and attribution remain with the owning project; this testbed's Apache-2.0 does not relicense REMORA/SDK, APS, third-party images or inputs.

**Commercial interpretation is optional and downstream.** The exact number of real verifier calls may inform a negotiated usage price, but shadow calls are not automatically billable. Illustrative expected value may depend on customer-specific incident prevalence, severity, false-refusal cost, latency, reliability and implementation cost; synthetic results establish none of those customer-specific inputs and do not by themselves define revenue share.

### 9. Profile for offline evidence verification (`effect_evidence`)

An offline `effect_evidence` edge is not a C arm. It reads a trail after the
effect and can neither stop nor permit an action, so B vs C does not measure
it. It gets its own profile, implemented centrally in the testbed
(`runner.edge_pass`, `runner.edge_usage`) and never in a contributor's code.

- **Unit:** one applied trail (a case), identified by the SHA-256 of its
  canonical case JSON. A **verifier run** is one local, offline start of the
  edge over a whole case set. It is not an API call to the contributor, and
  one run may answer many trails.
- **Inputs must be recorded, not made up.** Each claim's input comes from a
  record written when it happened: REMORA's readback and lifecycle, the twin's
  operation record, the observer's sealed record, and the digest captured in
  the ledger at export (or REMORA's per-section export manifest). A missing
  record is `NOT_ESTABLISHED`. A record that contradicts itself or its capture
  digest is `CONTRADICTED`. Every result is reported three ways: the edge's
  own answer, the answer on recorded inputs only, and the answer under strict
  vantage (an independent observation required). The source of each claim is
  listed.
- **Use** (`usage.json` per pass, summed by `runner.edge_usage` per edge and
  contributor):
  - `verifier_runs`, plus availability as successful versus failed runs;
  - `runtime_ms`;
  - `cases_processed`, `distinct_cases` and `not_established`;
  - `distinct_findings`;
  - agreement with the reference verifier. A finding only the contributed
    edge made is a candidate for review, never a confirmed finding.

  Every row is tied to the edge's version digest, the input digest and the
  run id. A repeated input on the same version adds use but no new cases or
  findings.
- **Value needs a ground truth.** `findings_confirmed`, `unique_findings`
  (correct where the reference verifier was wrong) and `false_findings` are
  counted only where a ground truth exists, and stay null on live trails:
  - sealed fixture sets, whose run records already count established
    positives, caught negatives and false refusals;
  - planned: seeded mutations of live trails, sealed with their expected
    verdict before any edge runs.
- **What a second implementation adds** even with no unique finding:
  reproduction of the same verdicts by independently written code. It is
  reported as agreement (n compared, n agreed), not as caught faults.
- **Use is not value.** Counts of runs or trails do not show a safety gain,
  and a price discussion may use them only as use.

Applied to Probity's `agent-evidence-vectors-axes`:

- Its v1 record (12/12, 6/6, `L4_INDEPENDENT_HOST_RUN` from the declared
  operator and host) stays as recorded. Both it and the reference verifier
  caught all six seeded faults, so there are no unique findings on v1.
- v2 is run against it only if its maintainer opts in, and would be a new
  record.
- On live trails it is measured by this profile, never as an arm.

## Implementation slices and acceptance

1. **Fixture contract:** define signed work-order schema, test-only keys, JCS payload/digest, ToolSpec v5 pin binding, UTC clock, delegation reference and the five #27 cases. Freeze manifest with independent oracle and negative controls. No REMORA SDK changes.
2. **Oracle and episode:** introduce a pre-registered `delegation_invalid` rule (with more specific attack classes), precedence and deterministically generated delegation episodes. Extend `runner.episode check` and tests to reject tampering, wrong hash, wrong pin, clock mismatch, missing work order and oracle-output leakage.
3. **Shadow evaluation:** run the externally pinned APS verifier on **every** proposal without side effects; retain raw per-proposal results, latency, error and revision metadata. Reproduction is possible without a live BMS or live APS network.
4. **Inline C:** attach the existing refusal-only `POST /decide` entrypoint **after B approval**; compare inline/shadow outputs on equivalent inputs, audit fail-closed/unavailable handling and prevent any edge from becoming an authorization or actuator path.
5. **Comparison:** add per-class matrices, paired effect outcomes, conditional utility/cost metrics, descriptive Wilson intervals, multi-seed/cluster summaries and machine-readable provenance. Make CI fail on omitted proposals, unstated exclusions, missing arms claimed as measured, altered oracle/fixture pins and arithmetic mismatches.
6. **Independent reproduction:** invite APS to confirm the contract and submit its own pinned verifier/results; use the established record/independence-level rules rather than inventing a new certification claim.
7. **Offline evidence profile (section 9):** usage accounting per pass and across passes and the recorded-input rule are implemented (`runner.edge_pass`, `runner.edge_usage`, merged on `main`). Still open: seeded mutation sets of live trails as a ground truth for confirmed, unique and false findings.

**Acceptance checks:** every frozen proposal has exactly one oracle row and complete layer-status accounting; refusal-only composition still holds; no missing run or unavailable edge is counted as an APS catch; all four forbidden-capture cells plus unresolved equal the forbidden denominator; B/C effect counts reconcile with twin revision/operation records; a negative/diffing run fails deterministically; changing a sealed input creates a new version; reports never claim physical safety.

## Open questions for #27

1. Exact delegation-to-work-order-scope encoding and verifier contract, including which party signs the work order and what is covered by its signature.
2. Whether APS will verify work-order/ToolSpec binding itself or only the delegation; assign each claim once, with an explicit ceiling.
3. Frozen proposal transport for delegation chains into the offline/inline harness (no changes to REMORA).
4. Scored-phase seed count, per-class sample sizes, attack distributions and owner acceptance of the oracle before freeze.

No external participant is committed by this ADR or by a map entry. Implementation and any commercial agreement require their own explicit decisions.
