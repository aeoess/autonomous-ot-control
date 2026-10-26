# autonomous-ot-control

Simulation-only digital-twin testbed in which an agent proposes a bounded
temperature-setpoint change, [REMORA](https://github.com/darklordVirtual/REMORA-research)
authorizes or refuses it, a protected dispatcher sends it to a simulated
actuator, and a separate read-only observer checks the effect.
Specification: [`docs/SDD-OT-001.md`](docs/SDD-OT-001.md).

> **Scope:** `SIMULATED_CONTROLLED_PILOT`. No physical OT/BMS write path exists
> in this repository. Simulation results never unlock a live connector (INV-12).

## Status

**PR 1 (OT-001 + OT-002)** delivered the simulator boundary: bootstrap,
deterministic twin, protected actuator, read-only observer, boundary smoke and
evidence. **PR 2 (OT-003 + OT-004)** adds the REMORA-governed safety boundary:
the signed ToolSpec, the deployment-owned actuator callable and its REMORA
registration seam, the SDK proposing runner, and the token-custody / network
fencing that keeps the actuator reachable only through REMORA's dispatcher. The
governed **code** is implemented and unit-tested offline; the end-to-end
authorized effect **through a running REMORA dispatcher** stays `NOT_ESTABLISHED`
until the pinned REMORA execution image is built (it requires the pinned upstream).

| Capability | Status |
|---|---|
| Deterministic 3-zone RC twin, seeded scenarios | `TESTED_LOCALLY` |
| Local interlock (bounds, delta, interval, rate, freshness, revision, stop switch, alarm freeze) | `TESTED_LOCALLY` |
| Single-use operation IDs incl. replay after restart (twin side) | `TESTED_LOCALLY` |
| Separate read / actuator / control listeners and credentials | `TESTED_LOCALLY` |
| Runner cannot reach actuator over Docker networks | `IMPLEMENTED` — becomes `TESTED_DOCKER` only via `run-demo.sh` evidence |
| Read-only effect observer (`same_test_deployment` vantage) | `TESTED_LOCALLY` |
| Rule baseline, scoring, evidence manifest | `TESTED_LOCALLY` |
| Signed ToolSpec (bounds mirror the guard; deploy-time attestation) | `TESTED_LOCALLY` |
| Deployment-owned callable: one governed effect, idempotent, fail-closed | `TESTED_LOCALLY` |
| SDK proposing runner: never self-approves, fail-closed, `UNKNOWN` not retried | `TESTED_LOCALLY` |
| Actuator-credential custody + plant-private fencing (twin + REMORA only) | `TESTED_LOCALLY` (structural) |
| REMORA execution image + registry hook (`deploy/Dockerfile.remora-ot`) | `IMPLEMENTED` — needs pinned upstream to build |
| Authorized effect **through REMORA's dispatcher**; SDK-only actor → zero effect | `NOT_ESTABLISHED` — requires the built REMORA image |
| Human review flow, governed-agent vs baseline comparison | `NOT_ESTABLISHED` (PR 3) |
| Miniflare / Cloudflare / federation | `NOT_ESTABLISHED` (PR 4–6) |
| Real-building savings | `NOT_ESTABLISHED` — synthetic model only |

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

### Governed profile (PR 2)

The governed path adds PostgreSQL, the protected REMORA execution image and the
SDK proposing runner. Building `deploy/Dockerfile.remora-ot` requires access to
the pinned REMORA upstream, so this path is opt-in and its end-to-end claim is
`NOT_ESTABLISHED` here:

```bash
docker compose --profile governed up --build -d --wait postgres remora twin
docker compose --profile governed run --rm governed \
  runner.governed --setpoint 20.5 --intent-ref WORK-ORDER-001
```

The proposing runner holds only an operator SDK token and the read token — no
actuator credential, no plant-private route. See `docs/integration-contracts.md`.

## Layout

```
twin/       model, clock, scenarios, guard (local interlock), api (3 listeners), server, effect_observer, ctl
adapter/    deployment-owned REMORA bridge: contracts (signed ToolSpec), registry (callable + hook), verifier
runner/     baseline, scoring, evidence, smoke (boundary probes), governed (SDK propose harness)
contracts/  ot-tool-v1, ot-toolspec-v1, twin-observation-v1, effect-receipt-v1 JSON Schemas
scenarios/  baseline day, stale observation, refusal vectors
deploy/     Dockerfiles (twin, runner, remora-ot) and scripts; compose.yaml at repo root
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
