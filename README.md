# autonomous-ot-control

Simulation-only digital-twin testbed in which an agent proposes a bounded
temperature-setpoint change, [REMORA](https://github.com/darklordVirtual/REMORA-research)
authorizes or refuses it, a protected dispatcher sends it to a simulated
actuator, and a separate read-only observer checks the effect.
Specification: [`docs/SDD-OT-001.md`](docs/SDD-OT-001.md).

> **Scope:** `SIMULATED_CONTROLLED_PILOT`. No physical OT/BMS write path exists
> in this repository. Simulation results never unlock a live connector (INV-12).

## Status

This is **PR 1 (OT-001 + OT-002)**: bootstrap, deterministic twin, protected
actuator, read-only observer, boundary smoke and evidence. REMORA is pinned
(`upstream/remora.lock.json`) but **not yet consumed**; governed dispatch
arrives in PR 2 (OT-003 + OT-004).

| Capability | Status |
|---|---|
| Deterministic 3-zone RC twin, seeded scenarios | `TESTED_LOCALLY` |
| Local interlock (bounds, delta, interval, rate, freshness, revision, stop switch, alarm freeze) | `TESTED_LOCALLY` |
| Single-use operation IDs incl. replay after restart (twin side) | `TESTED_LOCALLY` |
| Separate read / actuator / control listeners and credentials | `TESTED_LOCALLY` |
| Runner cannot reach actuator over Docker networks | `IMPLEMENTED` — becomes `TESTED_DOCKER` only via `run-demo.sh` evidence |
| Read-only effect observer (`same_test_deployment` vantage) | `TESTED_LOCALLY` |
| Rule baseline, scoring, evidence manifest | `TESTED_LOCALLY` |
| REMORA-governed dispatch, human review, signed ToolSpec | `NOT_ESTABLISHED` (PR 2) |
| Governed-agent vs baseline comparison | `NOT_ESTABLISHED` (PR 3) |
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

## Layout

```
twin/       model, clock, scenarios, guard (local interlock), api (3 listeners), server, effect_observer, ctl
runner/     baseline, scoring, evidence, smoke (boundary probes from the untrusted side)
contracts/  ot-tool-v1, twin-observation-v1, effect-receipt-v1 JSON Schemas
scenarios/  baseline day, stale observation, refusal vectors
deploy/     Dockerfiles and scripts; compose.yaml at repo root
upstream/   pinned REMORA commit
```

## Trust boundary (PR 1)

| Listener | Bind | Credential | Routes |
|---|---|---|---|
| read | `observe` network | `TWIN_READ_TOKEN` | zones, energy, status, operation log |
| actuator | fixed `plant-private` address only | `TWIN_ACTUATOR_TOKEN` (REMORA dispatcher, PR 2) | `POST /internal/v1/actuate/setpoint` |
| control | loopback inside twin | `TWIN_CONTROL_TOKEN` | clock advance, stop switch |

The runner container receives only the read token and only the `observe`
network. The twin refuses to start with missing, short, placeholder or
duplicate tokens, or a wildcard actuator bind.

## License

Not yet decided (SDD decision `D-01`). REMORA is BUSL-1.1 with its own
Research Grant and is not relicensed by anything here.
