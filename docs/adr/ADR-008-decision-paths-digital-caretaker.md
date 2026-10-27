# ADR-008: Decision paths for the digital caretaker (ACCEPT, VERIFY, ABSTAIN, ESCALATE)

- Status: Proposed. The owner set the direction on 2026-10-27; the configuration slice is next, and the upstream change needs its own REMORA PR
- Date: 2026-10-27
- Related: ADR-006 (plan approval, D-16), ADR-007 (tools, interlocks), ARCH-OT-008 (caretaker loop, MCP); REMORA `remora/policy/decision_engine.py`

## Owner direction (2026-10-27)

- **Reads ACCEPT.**
- **Writes VERIFY.** Verification gathers data first: day-ahead prices, the forecast, current observations, the expected effect, and the plan the write belongs to.
- **ESCALATE** when a human in the loop is genuinely needed.

## What REMORA does today (pinned `9b1763ca`, read from the code)

| Path | Reachable today? | How |
|---|---|---|
| ACCEPT for a **read** | Yes | `grounded_read_accept` or `low_consequence_accept`. Both need `action_type` read-only, risk tier `low` and a non-`prod` target. Our target is `simulated_pilot`. The tool must be declared in `REMORA_TOOL_METADATA_FILE`, which the testbed has not done. |
| ACCEPT for a **write** | **No** | The probabilistic ACCEPT exits are forced to VERIFY by the server's hard-coded execution profile. The deterministic exits are read-only. No standing, batch or plan approval exists. |
| VERIFY for a write | Yes, one human approval per write | Best case `authority_resolved_review` (medium tier, `schema_valid` declared, values grounded in the work-order text, intent resolved). The required role is `reviewer` at low or medium tier, `domain_expert` at high, `senior_authority` at critical. |
| ESCALATE | Yes | Hard guards (tainted or untrusted arguments, a forbidden tool), the tool contradicting the goal, `rollback_available=false` at high tier, an uncertain state transition, critical production writes |
| ABSTAIN | Yes | Argument scope invalid, a required argument that nothing can supply, a tool outside the capability set, no intent authority at low or medium tier |

**Why every testbed write so far was VERIFY with `senior_authority`.** `set_zone_setpoint` is not in `REMORA_TOOL_METADATA_FILE`, so its tier fell back to critical. That produced `evidence_insufficient` and the critical role. This is a testbed configuration gap, not REMORA behaviour, and slice 1 below fixes it.

## Decision

### Slice 1: configuration only, on the pinned REMORA

1. **A tool metadata file** (`deploy/ot/ot_tool_metadata.json`, `REMORA_TOOL_METADATA_FILE`), generated from the data-point catalogue:

   | Tools | Action | Tier | Other metadata |
   |---|---|---|---|
   | read tools (`read_zone_state`, `read_building_energy`, the planned point and report reads) | `read` | `low` | `schema_valid: true` |
   | routine bounded writes: zone heating, cooling, CO₂ and lux setpoints | `write` | `medium` | `schema_valid: true` (the signed ToolSpec already refuses schema violations), `rollback_available: true` (the previous setpoint is restorable) |
   | plant-level writes: tank, heat-pump cap, supply air, AHU on/off, cable mode | `write` | `high` | `rollback_available` stated per tool |

   High tier means REMORA demands a `domain_expert` and refuses without evidence. That is ESCALATE-like behaviour by design: these are the writes whose cascades (ADR-007 CS-02, CS-06, CS-07) can cost money or create a hazard.

2. **Work orders that carry the facts.** The caretaker writes each day plan as a REMORA work order (`ResolvedIntent`):
   - `task_text` contains the literal values and windows, so REMORA's grounding marks them grounded;
   - `action_spans` use REMORA's effect vocabulary;
   - `source_spans` and `target_entities` name the zones.

   The plan's evidence goes beside it in R2, and the work order cites it by SHA-256: price and forecast snapshots, the latest observations, the expected NOK and comfort per step, and the procedure each step belongs to (ARCH-OT-008 §6.1). The reviewer approves facts, not a bare number.

3. **Result.**
   - Reads ACCEPT.
   - Routine writes VERIFY with role `reviewer` and reason `authority_resolved_review`.
   - Plant-level writes VERIFY at `domain_expert` or ESCALATE.
   - Anything contradicting the plan, tainted, or outside the contract ESCALATEs or ABSTAINs.

   That meets the owner's direction for reads and for ESCALATE. For writes it still means one human approval per call.

### Slice 2: an upstream REMORA change (own PR, protected path)

To let a routine write be **verified by data instead of a human, once a human approved the plan it belongs to**, REMORA needs a new deterministic path. The working name is `plan_bound_verified_write`. It would ACCEPT a write only if every one of these holds:

- action `write`, tier `low` or `medium`, signed ToolSpec enforced, every argument grounded, scope valid;
- the call names an approved plan (an approved review item of a plan work order, still within its TTL);
- the plan window contains the call:
  - the same zone and tool;
  - a value within the plan's stated band;
  - the execution time within the plan's time window;
- the evidence the plan cites is fresh and verified, with the age bounds stated in the plan:
  - the price snapshot for that hour;
  - an observation of the target younger than its bound;
  - the forecast age;
- the cumulative limits preview passes;
- no newer contradicting fact is known: stop switch, alarm, interlock state, or a price snapshot that changed.

Any one failure falls back to VERIFY with the failing check named. The path issues the usual single-use ACCEPT token, so dispatch, limits, the audit chain and effect verification are unchanged. The design questions for that PR:

- how a plan approval is bound into each step's authorization context;
- how evidence freshness is checked by REMORA, not asserted by the caller;
- which CAPs it touches (CAP-003, CAP-013, CAP-031).

**Until that lands, D-16 stays open.** The caretaker runs with one approval per write, which is slow, and the ledger shows it.

### How the caretaker uses each outcome (ARCH-OT-008 §6.2)

| Outcome | Caretaker action | MCP customer sees |
|---|---|---|
| ACCEPT (read) | reads immediately | the value, sample time and quality |
| ACCEPT (plan-bound write, after slice 2) | `execute_accepted` at the planned time, then verifies the effect | "done as planned", with the effect and evidence |
| VERIFY | parks the step and notifies the reviewer, attaching the evidence; executes after approval | the reasons, the facts and the expected effect; `confirm_change` if their role suffices |
| ESCALATE | parks the step, raises an alert, names the required role; never auto-retries | "needs a domain expert": who and why |
| ABSTAIN | records the refusal and its reason; the plan step is skipped | the reason, and what would make it acceptable |

## Consequences

- Reads stop waiting for humans as soon as slice 1 is deployed.
- Writes stay human-approved until slice 2 exists. REMORA's safety case does not depend on the caretaker being right about its facts, because REMORA re-checks them itself.
- Slice 2 is the change that turns the testbed into a caretaker that acts on its own within a plan a human approved. It is a REMORA capability change and is reviewed as one, not patched locally (INV-11).
