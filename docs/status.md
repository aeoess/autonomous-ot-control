# Capability status

What this repository has established, by capability, and the evidence level
behind each line. Statuses are promoted only by new evidence, never by editing
this table. The README carries a short summary; this is the full record.

| Status | Meaning |
|---|---|
| `TESTED_LOCALLY` | Exercised by the unit or integration suite (`python -m pytest`) on one host, no Docker. |
| `TESTED_DOCKER` | Exercised on one host, one REMORA replica, Docker networks, synthetic plant, `same_test_deployment` vantage. Not a hosted or multi-node claim. |
| `SPECIFIED` | Written down (SDD, ADR, contract), not run. |
| `NOT_ESTABLISHED` | Nothing here shows it. |

## Delivery history

**PR 1 (OT-001 + OT-002)** delivered the simulator boundary. **PR 2 (OT-003 +
OT-004)** added the governed safety boundary. **OT-005..OT-012** run the stack
end to end: the pinned REMORA (`123a5fd0`) in production mode with PostgreSQL,
a signed ToolSpec v5 bundle (with signed postconditions) REMORA enforces and
pins by digest, signed cumulative limits across all 10 supervisory write tools,
the P3 caretaker profile engine with independent validator and FDD, verified
2025 Nord Pool prices and ERA5 weather across all five Norwegian price areas,
Statsbygg PA 0802 TFM mapping, and the opt-in federation onboarding kit.

## Capabilities

| Capability | Status |
|---|---|
| Deterministic 3-zone RC + HVAC building-services twin (`twin/plant.py`), seeded scenarios | `TESTED_LOCALLY` |
| All 10 supervisory write tools + interlocks `IL-01..IL-11` (zones, AHU, lighting, cables, tank, heat pump) | `TESTED_LOCALLY` |
| Single-use operation IDs incl. replay after restart (twin side) | `TESTED_LOCALLY` |
| Restart continues from the complete state: snapshot v2 carries the HVAC setpoints, plant state, point history (min-interval interlock), last plant points, heat and electricity meters and the tariff/season/surface state; a v1 snapshot is refused for the hvac model and migrated explicitly (event `snapshot_migrated`) for the simple one (finding of 2026-10-27 after PR #20; `tests/unit/test_twin_restart_state.py`) | `TESTED_LOCALLY` — before the fix a twin resumed from its snapshot kept revision 1 and an APPLIED record for cooling 24.0 while the cooling setpoint was back at 23.5, `point_history_s` was empty, and a change the interlock had just refused was accepted at the same simulated instant |
| Separate read / actuator / control listeners and credentials | `TESTED_LOCALLY` |
| Runner and harness cannot reach the actuator over Docker networks (AT-05) | `TESTED_DOCKER` |
| Read-only effect observer (`same_test_deployment` vantage) + ToolSpec v5 signed postconditions | `TESTED_LOCALLY` |
| Observer binds an effect to the operation that claims it: same tool, same target, same applied value, or `EFFECT_MISMATCH` whatever the plant shows (finding of 2026-10-27, `tests/unit/test_effect_observer_binding.py`) | `TESTED_LOCALLY` — before the fix an applied operation's id passed as proof of another tool's or zone's effect when the desired state already existed |
| Attestor keeps the observer's binding refusal through SDK verification and the recording boundary: a refused binding is an attested `EFFECT_MISMATCH` with the refusal as reason, never re-decided by comparing the plant's current value, and no `observed` fields are attached for a server to recompute from (finding of 2026-10-27 after PR #20; `tests/sdk/test_attestor_binding_sdk.py` with the real SDK at pin `123a5fd0`, `tests/unit/test_verifier_binding.py` without it) | `TESTED_LOCALLY` — before the fix the observer said `EFFECT_MISMATCH` (wrong tool, wrong zone, earlier value) and `verification.status` still came back `EFFECT_VERIFIED` from the real SDK; `attest_tool` downgraded the same refusal to `EFFECT_UNOBSERVABLE`. Server acceptance of an attested MISMATCH is not tested here |
| Rule baseline, scoring, evidence manifest | `TESTED_LOCALLY` |
| Signed ToolSpec enforced by REMORA (schema, target, closure digest, pinned bundle) | `TESTED_DOCKER` |
| Signed cumulative limits, mandatory; ZONE-03 budget refuses what the interlock allows (LIM-01) | `TESTED_DOCKER` |
| Authorized effect **through REMORA's dispatcher**, exactly once; operator cannot approve (AT-02) | `TESTED_DOCKER` |
| Replay refused, also after a REMORA restart (AT-04); tampered call refused (AT-03) | `TESTED_DOCKER` |
| Contract refusals: unknown tool/zone, bounds, extra args, wrong target (AT-08) | `TESTED_DOCKER` |
| Interlock refusal and stop switch: no effect, no replay shortcut (AT-08b, AT-10) | `TESTED_DOCKER` |
| PostgreSQL outage: unready, no effect, no blind resend on recovery (AT-11) | `TESTED_DOCKER` |
| Two concurrent approved writes to one zone: at most one effect (AT-13) | `TESTED_DOCKER` — held by the twin interlock, not by REMORA |
| Typed SDK `approve` on a deferred audit ref (SDK-01) | `TESTED_DOCKER` — fixed at pin `123a5fd0` (REMORA-research#1099) |
| Stale observation, timeout after actuation (AT-09, AT-12) | `NOT_ESTABLISHED` — TFM & stale checks tested in caretaker; no live fault hook in twin yet |
| Strategies P0/P1/P2/P3 over 2025 in all five price areas, verified local prices and weather ([report](results/2026-10-27/areas-2025.html)) | `TESTED_LOCALLY` |
| Caretaker P3: overlays, profiles, independent validator, fallback, FDD; A/B/C with seeded faults ([report](results/2026-10-27/abc-2025.html), ADR-009) | `TESTED_LOCALLY` — all 10 supervisory tools wired to REMORA (`runner.demo --strategy P3`) |
| TFM (Statsbygg PA 0802): register of every system and component code, parser and validator, every data point mapped to a TFM object ([tfm.md](tfm.md), ADR-010) | `TESTED_LOCALLY` — fictitious building `+DEMO01`; a real site's TFM needs its approved inventory (D-08) |
| Federation onboarding kit (`federation/`, `v1` frozen fixtures, sandboxed runner, CI workflow) | `TESTED_DOCKER` — see [federation/README.md](federation/README.md) and [federation/RESULTS.md](federation/RESULTS.md) |
| Reference verifier requires the evidence each claim needs: missing dispatch time, observation time or execution count is `NOT_ESTABLISHED`, a trail that says nothing executed while the twin recorded APPLIED is `CONTRADICTED`; fixture set `v2` adds those four negatives and the scorer counts a negative as caught only on the manifest's expected verdict (finding of 2026-10-27 after PR #20; `tests/unit/test_reference_verifier_evidence.py`) | `TESTED_LOCALLY` — before the fix all four schema-valid mutations came back `ESTABLISHED` (defaults of 1 execution and observation-at-dispatch, a skipped time check, a skipped authorization check when `executed` was false). Offline verifier only; no bypass of the manifest's hash check or of actuator authorization was shown |
| Miniflare / Cloudflare autonomous runtime ([architecture/autonomous-runtime-cloudflare.md](architecture/autonomous-runtime-cloudflare.md), MCP OpenAPI) | `SPECIFIED` (`NOT_ESTABLISHED` runtime) |
| Real-building savings, physical OT safety | `NOT_ESTABLISHED` — synthetic model only |

## Not established, by design

- No physical OT/BMS write path exists in this repository, and simulation
  results never unlock a live connector (INV-12).
- Every Docker result is one deployment seen from inside that deployment.
  Nothing here shows what a real plant does, or that REMORA's policy is the
  right policy for a real site.
