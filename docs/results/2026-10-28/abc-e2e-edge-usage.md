## Edge usage across passes

3 pass(es). Use is not value: verifier_runs and cases_processed count work done, and an edge run is local and offline, not an API call. Findings are counted once per edge version and input. Confirmed, unique and false findings need a ground truth and are null here.

| Edge | Maintainer | Versions | Passes | Verifier runs | Available | Runtime ms | Cases processed | Distinct cases | Not established | Distinct findings | Edge-only, for review |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `agent-evidence-vectors-axes` | astrogilda | 1 | 3 | 3 | 100% | 333.6 | 134 | 134 | 0 | 0 | 0 |
| `template-reference-verifier` | darklordVirtual | 1 | 3 | 3 | 100% | 545.4 | 134 | 134 | 0 | 0 | 0 |
