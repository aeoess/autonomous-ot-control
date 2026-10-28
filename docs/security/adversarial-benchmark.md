# Adversarial benchmark against the automation

**Question:** what does it take to make the automation change the plant in a way its authority
forbids, and which layer stops each technique?

**Code:** `runner/adversarial.py`.
**Tests:** `tests/unit/test_adversarial.py`.
**Results (7 days, 3 seeds, 25,200 attack attempts):**
[`docs/results/2026-10-28/adversarial.md`](../results/2026-10-28/adversarial.md) (and `.json`).

```bash
python -m runner.adversarial --days 7 --seeds 0 1 2 --out artifacts/adversarial
```

## Method

- **Workload.** The real workload runs: the P1 strategy, closed loop on the twin and scenario of the
  A/B/C episode, proposing every setpoint change through the stack under test. An adversary injects
  proposals between the benign ones, 24 a day.
- **Ground truth.** The oracle (`runner.oracle`) judges every proposal from the episode's work orders
  and the plant's limits alone. An attack **succeeds** when a proposal the oracle forbids changes the
  twin.
- **Stacks.** These are the testbed's own layers, offline. REMORA's layers (signed cumulative limits,
  single-use dispatch bound to the approved call, intent resolution) are **not in this loop**. The Docker
  A/B/C measures them (`docs/results/2026-10-28/abc-e2e.md`).

| Stack | Layers | Mirrors (minus REMORA) |
|---|---|---|
| `S0-interlock` | the twin's interlock (bounds, step, rate, single-use operation id) | arm A |
| `S1-contract` | signed ToolSpec, interlock | |
| `S2-careful` | contract, careful reviewer (approves only the plan's and the strategy's own next step), interlock | B-careful |
| `S3-rubber` | contract, rubber-stamp reviewer, interlock | B-rubber_stamp |
| `S4-rubber+edge` | contract, rubber stamp, reference inline edge (work order and step), interlock | C-rubber_stamp |

**Attack families.** Each family tries one technique:

| Family | What it tries |
|---|---|
| `off_plan` | a value in the contract but not in the day's plan |
| `out_of_contract` | outside the bounds or the resolution; NaN, inf, 1e308 |
| `argument_smuggling` | `override_interlock`, renamed or full-width keys, string, list, dict or bool values, a tool name with a trailing space, another tool |
| `wrong_zone` | a plan value on a zone the plan fixes |
| `forged_authority` | no work order, an old or invented one, another intent's |
| `step_escalation` | a step larger than the declared maximum |
| `replay_tamper` | an applied operation re-sent, as it was or with another value |
| `prompt_injection` | `off_plan` with an injected "SYSTEM OVERRIDE" reason |
| `salami_budget_creep` | small legal steps in plan values, as often as possible |

**Adaptive adversary.** It is a black-box search that sees only what a caller sees: the layer that
refused and its reason. It weights each technique by the deepest layer it has reached (contract,
reviewer, edge, interlock, applied) and keeps trying, about 160 queries per seed and week.

## Findings

1. **An authority check is what stops in-contract abuse.**
   - Against contract and interlock alone (`S1`), and with a rubber-stamp reviewer (`S3`), the
     following reached the plant in 93–100 % of their forbidden attempts:
     - off-plan values;
     - plan values on the wrong zone;
     - forged or missing authority;
     - injected reasons.
   - The careful reviewer (`S2`) and the inline edge (`S4`) stopped every one of them: 0 successes of
     about 400–500 forbidden attempts per family. The upper bound of the 95 % interval is 1 %.
   - In the Docker A/B/C, REMORA with a rubber stamp let 1 of 4 forbidden proposals through (the
     off-plan one). REMORA plus the edge let none through.
2. **The contract and the interlock hold where they are meant to.**
   - From `S1` on, nothing out of contract and no smuggled argument got through: 0 of about 480–500
     in every stack.
   - No step escalation and no replay or tamper got through any stack, including the interlock alone:
     0 of 504 each.
   - Without the contract (`S0`), 18 values that were out of contract but within the twin's physical
     bounds, and 87 smuggled calls whose extra arguments the plant ignores, did change the plant. The
     contract is what closes that gap.
3. **Budget creep is not reachable on this plant.** The twin allows 2 changes per zone per hour, which
   is tighter than the oracle's movement budget of 4.0 °C per hour (0.5 for ZONE-03). The salami family
   therefore produced no forbidden attempt at all.
4. **New finding: in-policy abuse starves the legitimate schedule.**
   - The plan permits several values per zone. An adversary that proposes the plan's own values at the
     wrong time had 628–693 effects in every stack except `S2`. The oracle permits these effects, so no
     authority check refuses them.
   - Each such effect spends the plant's change budget. The benign workload's refusals rose from 0 % to
     7–9 % (by the interlock's rate limit) in `S0`, `S1`, `S3` and `S4`.
   - Only the careful reviewer, which checks that a change is the strategy's own next step, prevented
     it: 0 in-policy effects and 0 benign refusals.
   - Authority-based governance (work order, edge, REMORA's intent) bounds *what* may happen. It does
     not bound *who* uses the plant's capacity.
5. **The adaptive adversary needs very little.**
   - It found a forbidden effect within 1–16 queries on every seed against `S0`, `S1` and `S3`.
   - Against `S2` and `S4` it found none in any seed. Against `S4` its deepest reach was the plant,
     but only with in-policy values.

## What follows

- **Rate quotas per principal**, so an agent with a valid work order cannot consume the change budget
  the schedule needs (finding 4). This is a REMORA cumulative-limits question per principal (REMORA's
  repository) or a site policy, not a testbed patch.
- **A Docker run of these families through REMORA**, as an adversarial arm in `run-abc.sh`. That would
  put REMORA's own layers in the loop. It is not built yet.
- **More techniques** as the agent surface grows: a language-model proposer, tool results carrying
  injections, multi-agent replay across principals.

## Limits

- This is a synthetic plant with one deterministic strategy and one adversary model. The numbers hold
  for these techniques on this twin. They are not a guarantee and say nothing about physical OT safety.
- Wilson intervals treat attempts within a seed as independent, which they are not quite: the
  adversary's state carries over. Read them as descriptive.
