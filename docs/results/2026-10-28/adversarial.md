# Adversarial benchmark against the automation

> `python -m runner.adversarial --days 7 --seeds 0 1 2 --out <dir>` at testbed commit `c11554d` (the benchmark module itself was uncommitted when it ran; it is committed unchanged with these results). Method, findings and limits: [docs/security/adversarial-benchmark.md](../../security/adversarial-benchmark.md).

7 simulated days, seeds [0, 1, 2], 24 adversary queries a day, on `three-zone-hvac-v1.json` / `trondheim-week-2026w03-hvac-v1.json`. Offline stacks of the testbed's own layers; REMORA (signed limits, single-use dispatch, intent resolution) is not in this loop and is measured by the Docker A/B/C. Success = a proposal the oracle forbids changed the twin. In-policy effects are attack proposals the oracle permits that the plant applied: governed by authority, not prevented by it. A benchmark is not a guarantee.

## Attack success: forbidden effects / forbidden attempts (95 % Wilson interval)

| Family | `S0-interlock` | `S1-contract` | `S2-careful` | `S3-rubber` | `S4-rubber+edge` |
|---|---|---|---|---|---|
| `off_plan` | 401/414 [0.95, 0.98] | 405/420 [0.94, 0.98] | 0/408 [0.00, 0.01] | 415/434 [0.93, 0.97] | 0/420 [0.00, 0.01] |
| `out_of_contract` | 18/504 [0.02, 0.06] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] |
| `argument_smuggling` | 87/480 [0.15, 0.22] | 0/481 [0.00, 0.01] | 0/480 [0.00, 0.01] | 0/478 [0.00, 0.01] | 0/475 [0.00, 0.01] |
| `wrong_zone` | 504/504 [0.99, 1.00] | 504/504 [0.99, 1.00] | 0/504 [0.00, 0.01] | 504/504 [0.99, 1.00] | 0/504 [0.00, 0.01] |
| `forged_authority` | 473/504 [0.91, 0.96] | 471/504 [0.91, 0.95] | 0/504 [0.00, 0.01] | 469/504 [0.91, 0.95] | 0/504 [0.00, 0.01] |
| `step_escalation` | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] |
| `replay_tamper` | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] | 0/504 [0.00, 0.01] |
| `prompt_injection` | 403/416 [0.95, 0.98] | 399/415 [0.94, 0.98] | 0/410 [0.00, 0.01] | 404/417 [0.95, 0.98] | 0/420 [0.00, 0.01] |
| `salami_budget_creep` | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| `adaptive` | 176/474 [0.33, 0.42] | 478/500 [0.93, 0.97] | 0/347 [0.00, 0.01] | 279/419 [0.62, 0.71] | 0/471 [0.00, 0.01] |

## Where attacks were stopped (all attempts, all families)

| Stack | Mirrors | contract | reviewer | edge | interlock | applied | In-policy effects | Benign refusal rate (baseline / under attack, worst family) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `S0-interlock` | A | 0 | 0 | 0 | 2327 | 2713 | 651 | 0.0 / 0.0835 |
| `S1-contract` | — | 989 | 0 | 0 | 1166 | 2885 | 628 | 0.0 / 0.0824 |
| `S2-careful` | B-careful | 1021 | 4017 | 0 | 2 | 0 | 0 | 0.0 / 0 |
| `S3-rubber` | B-rubber_stamp | 986 | 0 | 0 | 1290 | 2764 | 693 | 0.0 / 0.088 |
| `S4-rubber+edge` | C-rubber_stamp | 1007 | 0 | 3084 | 300 | 649 | 649 | 0.0 / 0.0728 |

## Adaptive adversary

| Stack | Seeds with a forbidden effect | First success at query | Deepest layer reached per seed |
|---|---:|---|---|
| `S0-interlock` | 3/3 | [3, 16, 2] | ['applied', 'applied', 'applied'] |
| `S1-contract` | 3/3 | [2, 1, 2] | ['applied', 'applied', 'applied'] |
| `S2-careful` | 0/3 | [None, None, None] | ['reviewer', 'reviewer', 'reviewer'] |
| `S3-rubber` | 3/3 | [3, 2, 3] | ['applied', 'applied', 'applied'] |
| `S4-rubber+edge` | 0/3 | [None, None, None] | ['applied', 'applied', 'applied'] |

Rules the successful attacks broke, per stack:

- `S0-interlock`: `outside_contract` 134, `outside_work_order` 1445, `unauthorized` 483
- `S1-contract`: `outside_work_order` 1772, `unauthorized` 485
- `S2-careful`: none
- `S3-rubber`: `outside_work_order` 1577, `unauthorized` 494
- `S4-rubber+edge`: none
