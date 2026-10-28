# Observer export end to end: governed run to Probity's edge

Run `observer-e2e-20261028T012704Z`: one simulated day of the governed demo (REMORA in the loop, careful reviewer) on Docker, then `runner.edge_pass` over its exported trails. Chain: REMORA → execution in the twin → effect observer → exported trail (with the observer's sealed record and the capture digest in `ledger.jsonl`) → reference verifier and `agent-evidence-vectors-axes` → report.

The demo itself exited 1: its attacks are injected on day 2, so a one-day run has none and its acceptance check (attacks == blocked) cannot hold. Determinism matched, the audit chain was valid, and 14 writes were applied with EFFECT_VERIFIED. The edge pass, which the script skips after a failed demo, was run by hand on the same work directory.

Acceptance (all three hold):

1. Probity's edge processed the 14 new trails and established all 14 on recorded inputs, every claim from a recorded source (no `missing:` source).
2. No missing observation, digest or execution count was accepted: every source is recorded here, and the pipeline mutation layer (edge-mutations.md) removes each one and gets NOT_ESTABLISHED or CONTRADICTED on all 14 trails.
3. Strict vantage: 0/14 established, as expected for `same_deployment` observations.

The earlier 0/44 of `abc-20261027T204956Z` (trails exported before the observer record) stays as recorded.

## Edges beside the run

14 applied `set_zone_setpoint` trails handed to every offline `effect_evidence` edge; skipped: none. Verdicts never change the run's outcome. *Edge* is each edge's own answer; *recorded inputs* counts an ESTABLISHED only when every claim rests on a record written when it happened; *strict vantage* also requires an independent observation.

| Edge | Evaluator | Ran | Edge E / C / N | Recorded inputs E / C / N | Strict vantage E / C / N | Unanswered |
|---|---|---|---|---|---|---:|
| `template-reference-verifier` | reference | yes | 14 / 0 / 0 | 14 / 0 / 0 | 0 / 0 / 14 | 0 |
| `agent-evidence-vectors-axes` | second_implementation | yes | 14 / 0 / 0 | 14 / 0 / 0 | 0 / 0 / 14 | 0 |

Where each claim's input comes from (trails per source):

| Claim | Source | Trails |
|---|---|---:|
| `call_binding` | twin operation record | 14 |
| `decision_correspondence` | ledger row: REMORA decision and reviewer | 14 |
| `declared_effect_observed` | REMORA effect_verification readback (vantage same_deployment) | 14 |
| `evidence_digest_integrity` | ledger capture digest | 14 |
| `observation_after_dispatch` | observer record sim_time_s (sealed) | 14 |
| `single_dispatch` | REMORA lifecycle: 1 execution_result event(s) | 14 |

Observation vantage recorded: `same_deployment` 14

No edge findings: every available edge established every trail.
