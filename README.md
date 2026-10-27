# autonomous-ot-control

Simulation-only digital-twin testbed in which an agent proposes a bounded
temperature-setpoint change, [REMORA](https://github.com/darklordVirtual/REMORA-research)
authorizes or refuses it, a protected dispatcher sends it to a simulated
actuator, and a separate read-only observer checks the effect.
Specification: [`docs/SDD-OT-001.md`](docs/SDD-OT-001.md).

> **Scope:** `SIMULATED_CONTROLLED_PILOT`. No physical OT/BMS write path exists
> in this repository. Simulation results never unlock a live connector (INV-12).

## Status

**PR 1 (OT-001 + OT-002)** delivered the simulator boundary. **PR 2 (OT-003 +
OT-004)** added the governed safety boundary. **OT-005 slice 1** runs it: the
pinned REMORA (`9b1763ca`) in production mode with PostgreSQL, a signed ToolSpec
bundle REMORA enforces and pins by digest, and signed cumulative limits it
refuses to start without. `./deploy/scripts/run-governed.sh` runs the acceptance
suite against that stack from a clean bootstrap; evidence lands in `artifacts/`.

| Capability | Status |
|---|---|
| Deterministic 3-zone RC twin, seeded scenarios | `TESTED_LOCALLY` |
| Local interlock (bounds, delta, interval, rate, freshness, revision, stop switch, alarm freeze) | `TESTED_LOCALLY` |
| Single-use operation IDs incl. replay after restart (twin side) | `TESTED_LOCALLY` |
| Separate read / actuator / control listeners and credentials | `TESTED_LOCALLY` |
| Runner and harness cannot reach the actuator over Docker networks (AT-05) | `TESTED_DOCKER` |
| Read-only effect observer (`same_test_deployment` vantage) | `TESTED_LOCALLY` |
| Rule baseline, scoring, evidence manifest | `TESTED_LOCALLY` |
| Signed ToolSpec enforced by REMORA (schema, target, closure digest, pinned bundle) | `TESTED_DOCKER` |
| Signed cumulative limits, mandatory; ZONE-03 budget refuses what the interlock allows (LIM-01) | `TESTED_DOCKER` |
| Authorized effect **through REMORA's dispatcher**, exactly once; operator cannot approve (AT-02) | `TESTED_DOCKER` |
| Replay refused, also after a REMORA restart (AT-04); tampered call refused (AT-03) | `TESTED_DOCKER` |
| Contract refusals: unknown tool/zone, bounds, extra args, wrong target (AT-08) | `TESTED_DOCKER` |
| Interlock refusal and stop switch: no effect, no replay shortcut (AT-08b, AT-10) | `TESTED_DOCKER` |
| PostgreSQL outage: unready, no effect, no blind resend on recovery (AT-11) | `TESTED_DOCKER` |
| Two concurrent approved writes to one zone: at most one effect (AT-13) | `TESTED_DOCKER` — held by the twin interlock, not by REMORA |
| Typed SDK `approve` on a deferred audit ref (SDK-01) | **FAIL** at the pin — fixed upstream in REMORA-research#1099 |
| Stale observation, timeout after actuation (AT-09, AT-12) | `NOT_ESTABLISHED` — no fault hook in the running twin yet |
| Governed-agent vs baseline comparison (AT-16) | `NOT_ESTABLISHED` |
| Strategies P0/P1/P2/P3 over 2025 in all five price areas, verified local prices and weather ([report](docs/results/2026-10-27/areas-2025.html)) | `TESTED_LOCALLY` — offline, applied directly, not through REMORA |
| Caretaker P3: overlays, profiles, independent validator, fallback, FDD; A/B/C with seeded faults ([report](docs/results/2026-10-27/abc-2025.html), ADR-009) | `TESTED_LOCALLY` — offline; only `set_zone_setpoint` is a governed tool |
| TFM (Statsbygg PA 0802): register of every system and component code, parser and validator, every data point mapped to a TFM object ([docs/tfm.md](docs/tfm.md), ADR-010) | `TESTED_LOCALLY` — fictitious building `+DEMO01`; a real site's TFM needs its approved inventory (D-08) |
| Miniflare / Cloudflare / federation | `NOT_ESTABLISHED` (PR 4–6) |
| Real-building savings, physical OT safety | `NOT_ESTABLISHED` — synthetic model only |

`TESTED_DOCKER` means one host, one REMORA replica, Docker networks, synthetic
plant, `same_test_deployment` vantage. It is not a hosted or multi-node claim.

Statuses are promoted only by new evidence, never by editing this table.

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
```

### Governed acceptance suite (OT-005)

```bash
./deploy/scripts/run-governed.sh      # bootstrap, sign, up, acceptance suite, evidence
docker compose -f compose.yaml -f compose.governed.yaml down -v
```

`governed-bootstrap.sh` writes local dev secrets to `.env`, keeps two Ed25519
signer seeds in `deploy/.keys/`, and signs the ToolSpec bundle and the limits
policy into `deploy/.generated/` in a container with no network (all three are
gitignored). REMORA receives only the public keys and the pinned bundle digest.
One governed proposal from the CLI:

```bash
docker compose -f compose.yaml -f compose.governed.yaml --profile cli run --rm governed   runner.governed --setpoint 20.5 --intent-ref WORK-ORDER-001
```

The proposing runner holds only an operator SDK token and the read token — no
actuator credential, no plant-private route. The actuator credential lives only
in the `twin` and `remora` services.

## Layout

```
twin/       model, clock, scenarios, guard (local interlock), api (3 listeners), server, effect_observer, ctl
adapter/    REMORA-free helpers: contracts (signed ToolSpec), actuation (effect primitive), verifier (SDK effect API)
deploy/ot/  deployment modules loaded inside the REMORA image: ot_registry (register_tools), ot_bundle, intents, limits
runner/     baseline, scoring, evidence, smoke, governed (SDK proposer), acceptance + harness_step (OT-005)
contracts/  ot-tool-v1, ot-toolspec-v1, twin-observation-v1, effect-receipt-v1 JSON Schemas
scenarios/  baseline day, stale observation, refusal vectors
deploy/     Dockerfiles (twin, runner, remora-ot, governed) and scripts (deploy/ot: REMORA deployment modules, signer); compose.yaml + compose.governed.yaml at repo root
docs/       SDD-OT-001, integration-contracts, adr/
upstream/   pinned REMORA commit
```

## Trust boundary

| Listener | Bind | Credential | Routes |
|---|---|---|---|
| read | `observe` network | `TWIN_READ_TOKEN` | zones, energy, status, operation log |
| actuator | fixed `plant-private` address only | `TWIN_ACTUATOR_TOKEN` (held only by twin + REMORA dispatcher) | `POST /internal/v1/actuate/setpoint` |
| control | loopback inside twin | `TWIN_CONTROL_TOKEN` | clock advance, stop switch |

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
