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
| Separate read / actuator / control listeners and credentials | `TESTED_LOCALLY` |
| Runner and harness cannot reach the actuator over Docker networks (AT-05) | `TESTED_DOCKER` |
| Read-only effect observer (`same_test_deployment` vantage) + ToolSpec v5 signed postconditions | `TESTED_LOCALLY` |
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
| Miniflare / Cloudflare autonomous runtime ([architecture/autonomous-runtime-cloudflare.md](architecture/autonomous-runtime-cloudflare.md), MCP OpenAPI) | `SPECIFIED` (`NOT_ESTABLISHED` runtime) |
| Real-building savings, physical OT safety | `NOT_ESTABLISHED` — synthetic model only |

## Not established, by design

- No physical OT/BMS write path exists in this repository, and simulation
  results never unlock a live connector (INV-12).
- Every Docker result is one deployment seen from inside that deployment.
  Nothing here shows what a real plant does, or that REMORA's policy is the
  right policy for a real site.
