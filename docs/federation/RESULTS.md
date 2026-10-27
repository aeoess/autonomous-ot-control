# Federation edge results

One row per merged run of `./deploy/scripts/run-edge.sh`. Negative results
stay here. A **reproduction** was run by this repository's maintainers; an
**independent** run was made by the edge's maintainer on their own
infrastructure, with their report linked. Neither label is an endorsement of
the edge or of the testbed: a row says what was checked against which pinned
fixtures, and no more.

| Date | Edge | Boundary | Fixture set (manifest sha256) | Testbed commit | Positives | Negatives caught | Result | Label | Report |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-27 | `template-reference-verifier` | `effect_evidence` | `v1` (`0c2e7c3e463749041bc069438f102eefc5f239a1ae26b8c5c7d8d95dd5398708`) | `998159e` (fixtures built at `942ba37`) | 12 / 12 | 6 / 6 | PASS | reproduction | `run-edge.sh _template --local`; CI repeats the sandboxed run on every federation pull request |

The template row is a self-test that the fixtures still discriminate (every
seeded fault is caught by a verifier that uses only the published rules). It is
not an external verification.
