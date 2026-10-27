# autonomous-ot-control

Simulation-only digital-twin testbed in which a digital caretaker proposes
bounded building-automation actions, [REMORA](https://github.com/darklordVirtual/REMORA-research)
authorizes or refuses them under signed contracts and cumulative limits, a
protected dispatcher sends approved writes to a simulated actuator, and a
separate read-only observer verifies the effect.
Specification: [`docs/SDD-OT-001.md`](docs/SDD-OT-001.md).

> **Scope:** `SIMULATED_CONTROLLED_PILOT`. No physical OT/BMS write path exists
> in this repository. Simulation results never unlock a live connector (INV-12).

## Status

**PR 1 (OT-001 + OT-002)** delivered the simulator boundary. **PR 2 (OT-003 +
OT-004)** added the governed safety boundary. **OT-005..OT-011** run the stack
end to end: the pinned REMORA (`123a5fd0`) in production mode with PostgreSQL,
a signed ToolSpec v5 bundle (with signed postconditions) REMORA enforces and
pins by digest, signed cumulative limits across all 10 supervisory write tools,
the P3 caretaker profile engine with independent validator and FDD, verified
2025 Nord Pool prices and ERA5 weather across all five Norwegian price areas,
Statsbygg PA 0802 TFM mapping, and the opt-in federation onboarding kit.

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
| Strategies P0/P1/P2/P3 over 2025 in all five price areas, verified local prices and weather ([report](docs/results/2026-10-27/areas-2025.html)) | `TESTED_LOCALLY` |
| Caretaker P3: overlays, profiles, independent validator, fallback, FDD; A/B/C with seeded faults ([report](docs/results/2026-10-27/abc-2025.html), ADR-009) | `TESTED_LOCALLY` — all 10 supervisory tools wired to REMORA (`runner.demo --strategy P3`) |
| TFM (Statsbygg PA 0802): register of every system and component code, parser and validator, every data point mapped to a TFM object ([docs/tfm.md](docs/tfm.md), ADR-010) | `TESTED_LOCALLY` — fictitious building `+DEMO01`; a real site's TFM needs its approved inventory (D-08) |
| Federation onboarding kit (`federation/`, `v1` frozen fixtures, sandboxed runner, CI workflow) | `TESTED_DOCKER` — see [`docs/federation/README.md`](docs/federation/README.md) |
| Miniflare / Cloudflare autonomous runtime (`docs/architecture/autonomous-runtime-cloudflare.md`, MCP OpenAPI) | `SPECIFIED` (`NOT_ESTABLISHED` runtime) |
| Real-building savings, physical OT safety | `NOT_ESTABLISHED` — synthetic model only |

`TESTED_DOCKER` means one host, one REMORA replica, Docker networks, synthetic
plant, `same_test_deployment` vantage. It is not a hosted or multi-node claim.

Statuses are promoted only by new evidence, never by editing this table.

## Energy-saving strategies and REMORA synergy (2025, 5 price areas)

The caretaker (`runner/caretaker.py`, ADR-009) combines safety overlays
(`frost_protection`, `iaq_protection`, `icing_protection`, `telemetry_degraded`),
one active base profile (`comfort_priority`, `demand_response`, `preconditioning`,
`night_setback`, `energy_saving`, or `balanced_default`), one named algorithm
per setpoint, an independent validator against [`deploy/ot/site_policy.v1.json`](deploy/ot/site_policy.v1.json),
a hash-pinned sensor-free fallback ([`deploy/ot/caretaker_fallback.v1.json`](deploy/ot/caretaker_fallback.v1.json)),
and zone sensor FDD. Every supervisory change maps to one of the 10 governed
tools in [`docs/datapoints.md`](docs/datapoints.md) and carries its decision
record (`ot-caretaker-decision/v1`) as evidence into REMORA.

| Price area | P0 cost (NOK) | P0 energy (kWh) | P3 saves vs P0 (%) | P3 saves (NOK) | P3raw (no validator, faulted) | Validator value |
|---|---:|---:|---:|---:|---:|---:|
| **NO1 Oslo** | 56 127 | 47 331 | **41.9 %** | 23 517 | 37.4 % | +4.5 pp (0 h tank > 60 °C vs 322 h) |
| **NO2 Kristiansand** | 53 103 | 40 410 | **39.9 %** | 21 208 | 34.5 % | +5.5 pp (0 h tank > 60 °C vs 383 h) |
| **NO5 Bergen** | 48 704 | 43 629 | **39.8 %** | 19 384 | 32.1 % | +7.3 pp (0 h tank > 60 °C vs 452 h) |
| **NO3 Trondheim** | 37 092 | 48 840 | **33.8 %** | 12 539 | 27.3 % | +6.5 pp (0 h tank > 60 °C vs 355 h) |
| **NO4 Tromsø** | 29 997 | 65 101 | **25.5 %** | 7 637 | 16.4 % | +9.5 pp (0 h tank > 60 °C vs 283 h) |

*Synthetic, uncalibrated building (3 seeds, full year 2025, verified local Nord Pool spot prices + ERA5 weather + declared reference grid tariff, taxes excluded). Without the heating-cable lever (ground moisture sensor vs air thermostat), P3 saves 9–14 % kWh across the remaining HVAC and lighting levers. Full reports: [`areas-2025.md`](docs/results/2026-10-27/areas-2025.md) and [`abc-2025.md`](docs/results/2026-10-27/abc-2025.md).*

## Join as a federation participant

External governance, verification, or safety projects can join the testbed as an
opt-in **federation edge** in under five minutes — with zero credentials and no
network access required. Full guide: **[`docs/federation/README.md`](docs/federation/README.md)**.

```bash
# 1. Scaffold a new edge from the reference template
python -m runner.edge_check --init <your-edge-id> --project "<project name>" --maintainer <github-handle>

# 2. Edit federation/edges/<your-edge-id>/adapter.py and edge.json, then validate & run
./deploy/scripts/run-edge.sh <your-edge-id> --local   # fast local check (stdlib only, no Docker)
./deploy/scripts/run-edge.sh <your-edge-id>           # exact CI sandboxed container run (12 pos + 6 neg controls)
```

Open a pull request adding `federation/edges/<your-edge-id>/` and your entry in
[`docs/federation/edge-registry.yaml`](docs/federation/edge-registry.yaml). That PR is your consent record.

## Run

```bash
./deploy/scripts/run-demo.sh        # Docker: build, smoke, baseline, evidence in artifacts/
docker compose down -v
```

Without Docker:

```bash
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
python -m pytest -q
python -m runner.baseline --run-id local-1 --out artifacts
python -m runner.areas --seeds 177 178 179 --out artifacts/areas-2025
```

### Governed acceptance suite & demo (OT-005..OT-010)

```bash
./deploy/scripts/run-governed.sh              # bootstrap, sign, up, acceptance suite, evidence
DAYS=7 ./deploy/scripts/run-demo-governed.sh  # 7-day governed demo with REMORA in the loop
docker compose -f compose.yaml -f compose.governed.yaml down -v
```

`governed-bootstrap.sh` writes local dev secrets to `.env`, keeps two Ed25519
signer seeds in `deploy/.keys/`, and signs the ToolSpec v5 bundle and the limits
policy into `deploy/.generated/` in a container with no network (all three are
gitignored). REMORA receives only the public keys and the pinned bundle digest.
One governed proposal from the CLI:

```bash
docker compose -f compose.yaml -f compose.governed.yaml --profile cli run --rm governed \
  runner.governed --setpoint 20.5 --intent-ref WORK-ORDER-001
```

The proposing runner holds only an operator SDK token and the read token — no
actuator credential, no plant-private route. The actuator credential lives only
in the `twin` and `remora` services.

## Layout

```
twin/        model, plant (HVAC/tank/HP/AHU/lighting/cables), tfm (PA 0802), datapoints, guard, api, server, effect_observer
adapter/     REMORA-free helpers: contracts (signed ToolSpec), actuation (effect primitives), verifier (SDK effect API)
deploy/ot/   deployment modules loaded inside REMORA: ot_registry, ot_bundle, site_policy, caretaker_fallback, intents, limits, signer
runner/      caretaker (P3), strategies (P0..P2), areas, demo, acceptance, harness_step, edge_check, federation_fixtures
federation/  edges/_template (reference verifier) and fixtures/v1 (12 positive + 6 seeded fault trails)
contracts/   ToolSpec, tool surface, caretaker profile catalog, federation edge/case/result, MCP OpenAPI schemas
scenarios/   verified 2025 price & ERA5 weather series for NO1..NO5, baseline day, stale observation, refusal vectors
docs/        SDD-OT-001, datapoints.md, tfm.md, federation/README.md, results/2026-10-27/, adr/ADR-003..010
upstream/    pinned REMORA commit (123a5fd0)
```

## Trust boundary

| Listener | Bind | Credential | Routes |
|---|---|---|---|
| read | `observe` network | `TWIN_READ_TOKEN` | `/v1/zones`, `/v1/energy`, `/v1/plant`, `/v1/status`, `/v1/operations/{id}` |
| actuator | fixed `plant-private` address only | `TWIN_ACTUATOR_TOKEN` (held only by twin + REMORA dispatcher) | `POST /internal/v1/actuate/setpoint`, `POST /internal/v1/actuate/plant` |
| control | loopback inside twin | `TWIN_CONTROL_TOKEN` | clock advance, stop switch, snapshot |

Networks: `observe` (read-only), `authority` (proposing runner ↔ REMORA ↔
PostgreSQL), `plant-private` (actuator — only `twin` and `remora`). The
proposing/smoke runners receive only the read (and operator SDK) token and never
join `plant-private`, so they have neither a route nor a credential to the
actuator even with a guessed URL (INV-01/INV-02; `tests/boundary/
test_custody_and_fencing.py`). The twin refuses to start with missing, short,
placeholder or duplicate tokens, or a wildcard actuator bind.

## License

Not yet decided (SDD decision `D-01`). REMORA is BUSL-1.1 with its own
Research Grant and is not relicensed by anything here.
