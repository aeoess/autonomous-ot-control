# Mutation and benchmark report for offline effect-evidence edges

Seeds: 14 fully recorded applied trails from `observer-e2e-20261028T012704Z-work`. 37 case operators, 518 case mutants; case set manifest `61096e9beea5d09d…`. Expected verdicts come from each operator and the case contract, written before any edge ran. Scores hold for these operators on these simulated trails; they are not a guarantee.

Testbed commit `a419c737a637` (mutation tool not yet merged when run); host: Windows 11, contended (other processes using most of 15.6 GB RAM); benchmark times are indicative only.

## Case layer (what the edge reads)

| Edge | Whole set ran | Kill rate | Exact accuracy | Accepted missing evidence | False refusals | Runtime ms |
|---|---|---:|---:|---:|---:|---:|
| `template-reference-verifier` | yes | 420/420 (100.0%) | 100.0% | 0 | 0/98 | 247.3 |
| `agent-evidence-vectors-axes` | no (per operator) | 406/420 (96.7%) | 97.3% | 0 | 0/98 | 186.9 |

Per operator (correct / n):

| Operator | `template-reference-verifier` | `agent-evidence-vectors-axes` |
|---|---:|---:|
| `cb_operation_id_case` | 14/14 | 14/14 |
| `cb_other_operation` | 14/14 | 14/14 |
| `cb_record_missing` | 14/14 | 14/14 |
| `dc_denied_but_applied` | 14/14 | 14/14 |
| `dc_escalate_not_approved` | 14/14 | 14/14 |
| `dc_executed_missing` | 14/14 | 14/14 |
| `dc_not_approved` | 14/14 | 14/14 |
| `dc_not_executed_but_observed` | 14/14 | 14/14 |
| `de_claim_plus_half` | 14/14 | 14/14 |
| `de_claim_plus_milli` | 14/14 | 14/14 |
| `de_claim_plus_tenth_milli` | 14/14 | 14/14 |
| `de_observation_missing` | 14/14 | 14/14 |
| `de_observed_off` | 14/14 | 14/14 |
| `de_observed_off_tenth_milli` | 14/14 | 14/14 |
| `de_observed_value_missing` | 14/14 | 0/14 |
| `de_twin_other_zone` | 14/14 | 14/14 |
| `de_twin_rejected` | 14/14 | 14/14 |
| `ed_digest_ascii_escaped` | 14/14 | 14/14 |
| `ed_digest_one_char` | 14/14 | 14/14 |
| `ed_digest_uppercase` | 14/14 | 14/14 |
| `ed_evidence_changed` | 14/14 | 14/14 |
| `oa_dispatch_time_missing` | 14/14 | 14/14 |
| `oa_dispatch_time_null` | 14/14 | 14/14 |
| `oa_fifteen_minutes_early` | 14/14 | 14/14 |
| `oa_observation_time_missing` | 14/14 | 14/14 |
| `oa_one_second_early` | 14/14 | 14/14 |
| `pc_evidence_key_order` | 14/14 | 14/14 |
| `pc_extra_reviewer_field` | 14/14 | 14/14 |
| `pc_identity` | 14/14 | 14/14 |
| `pc_integer_setpoint` | 14/14 | 14/14 |
| `pc_non_ascii_evidence` | 14/14 | 14/14 |
| `pc_observed_an_hour_later` | 14/14 | 14/14 |
| `pc_observed_at_dispatch` | 14/14 | 14/14 |
| `sd_missing` | 14/14 | 14/14 |
| `sd_string_one` | 14/14 | 14/14 |
| `sd_twice` | 14/14 | 14/14 |
| `sd_zero` | 14/14 | 14/14 |

`agent-evidence-vectors-axes` against the reference: only this edge correct on 0, only the reference correct on 14 mutants.

`agent-evidence-vectors-axes` did not complete the whole set; run per operator, it crashed on `de_observed_value_missing` (those mutants count as unanswered, i.e. wrong).

`agent-evidence-vectors-axes` wrong on: `de_observed_value_missing` 14. Example: expected ['NOT_ESTABLISHED'], got UNANSWERED (None).

## Pipeline layer (raw trail and ledger, counted by runner.edge_pass)

14 trails per operator, mutated before the pass builds the cases.

| Operator | Class | Expected | `agent-evidence-vectors-axes` | `template-reference-verifier` |
|---|---|---|---|---|
| `p_capture_missing_manifest_ok` | positive | ESTABLISHED | 14/14 | 14/14 |
| `p_evidence_edited_after_capture` | negative | CONTRADICTED | 14/14 | 14/14 |
| `p_section_edited_no_capture` | negative | CONTRADICTED | 14/14 | 14/14 |
| `p_observer_record_tampered` | negative | CONTRADICTED | 14/14 | 14/14 |
| `p_observer_disagrees_with_readback` | negative | CONTRADICTED | 14/14 | 14/14 |
| `p_double_execution` | negative | CONTRADICTED | 14/14 | 14/14 |
| `p_observer_record_removed` | missing | NOT_ESTABLISHED | 14/14 | 14/14 |
| `p_lifecycle_incomplete` | missing | NOT_ESTABLISHED | 14/14 | 14/14 |
| `p_readback_missing` | missing | NOT_ESTABLISHED | 14/14 | 14/14 |
| `p_twin_record_missing` | missing | NOT_ESTABLISHED | 14/14 | 14/14 |

## Benchmark (median of 3 runs, one process start per run)

| Edge | Cases | OK | Median ms | ms per case | Cases per s |
|---|---:|---|---:|---:|---:|
| `template-reference-verifier` | 100 | yes | 108.2 | 1.082 | 924.3 |
| `template-reference-verifier` | 1000 | yes | 394.2 | 0.394 | 2536.8 |
| `template-reference-verifier` | 5000 | yes | 1635.1 | 0.327 | 3058.0 |
| `agent-evidence-vectors-axes` | 100 | yes | 124.5 | 1.245 | 803.2 |
| `agent-evidence-vectors-axes` | 1000 | yes | 527.0 | 0.527 | 1897.4 |
| `agent-evidence-vectors-axes` | 5000 | yes | 2303.9 | 0.461 | 2170.2 |
