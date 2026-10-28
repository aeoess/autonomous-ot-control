# #177 offline mutation crosswalk

Source: [Tetsurohhori's report](https://github.com/aeoess/agent-governance-vocabulary/issues/177#issuecomment-6468878522),
[battery at 6dd487d](https://github.com/aos-standard/catalog/blob/6dd487d/docs/runs/otc-effect-evidence-cold/lax_battery.py).
That report mutated a separate reader over sealed v2 at testbed `3b7b7e8`:
21 reader mutants, 10 caught, 11 survivors. A survivor demonstrates a corpus
gap, not that the testbed implements that fault.

The table maps those faults to current case operators. It is a semantic
crosswalk, not a rerun or a new score of the external reader's 21 mutants.

| External mutation | Current operator(s) |
|---|---|
| M01 ignore outer approval | `dc_not_approved`, `dc_escalate_not_approved` |
| M02 ignore reviewer approval | **new** `dc_reviewer_refused` |
| M03 ignore envelope binding | **new** `cb_envelope_request` |
| M03b ignore evidence binding | **new** `cb_evidence_proposal` (recomputes digest) |
| M04 no-dispatch with applied record | `dc_denied_but_applied`, `dc_not_executed_but_observed` |
| M05 assume execution count | `sd_missing` |
| M06 accept any count | `sd_twice`, `sd_zero` |
| M07 ignore operation id | `cb_other_operation`, `cb_operation_id_case` |
| M08 ignore applied status | `de_twin_rejected` |
| M09 ignore zone | `de_twin_other_zone` |
| M10 ignore actual twin value | **new** `de_twin_value_only` (observation remains correct) |
| M11 ignore observed value | `de_observed_off`, `de_observed_off_tenth_milli` |
| M12 assume observation | `de_observation_missing` |
| M13 assume dispatch time | `oa_dispatch_time_missing`, `oa_dispatch_time_null` |
| M14 assume observation time | `oa_observation_time_missing` |
| M15 require strictly later observation | `pc_observed_at_dispatch` |
| M16 ignore digest | `ed_evidence_changed`, `ed_digest_one_char` |
| M17 ASCII-escape canonical evidence | `pc_non_ascii_evidence`, `ed_digest_ascii_escaped` |
| M18 omit key sorting | **repaired** `pc_evidence_key_order`: case serialization preserves insertion order |
| M19 use default separators | `pc_identity` with the canonical evidence digest |
| M20 accept malformed case | Existing malformed cases plus **new** `malformed_extra_field`, `malformed_boolean_count` |

There are now 43 case operators (37 before this change). Tests additionally
exercise all five recognized binding paths, both review decisions, malformed
numeric/container shapes, duplicate JSON members, invalid JSON and isolation
of malformed cases from valid ones. Missing/null optional evidence remains
distinct from a contradiction. A valid ACCEPT without a reviewer remains valid.

The key-order regression checks the serialized file, rather than the in-memory
dictionary: a hasher without sorting must compute a different digest. Previously
`write_set(sort_keys=True)` silently undid the control before either adapter ran.

Local development evaluation: apply the 43 operators to the 12 sealed-v2
positive trails, write and reread the resulting 516 case files, and run each
current adapter over those files. Both answer all 516 as expected: 432 negative,
missing or malformed variants refused and 84 positive controls established.
`test_both_builtins_answer_every_serialized_development_mutant` reproduces this
evaluation. These are seeded case variants, not 516 distinct faults or a score
for the external reader's 21 source mutants.

Both adapters include maintainer-written admission/binding guards. The external
reader's pinned source is unchanged; results do not establish independent
validation of these guards or any operational authorization property.

Reproduce the regression and mutation-harness checks:

```sh
python -m pytest -q tests/unit/test_federation_admission_gaps.py tests/unit/test_edge_mutations.py tests/unit/test_reference_verifier_evidence.py tests/unit/test_agent_evidence_vectors_edge.py
```

The v1/v2 fixtures and old run records are unchanged. New mutation runs are
separate development evaluations, not expanded consent or new external levels.
