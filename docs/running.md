# Running the testbed

Requires Python 3.11 or newer (CI runs 3.11 and 3.12) and, for the Docker
paths, Docker Compose v2. Everything below is simulation only; see
[`SECURITY.md`](../SECURITY.md).

## Simulator only

Docker: build, smoke test, rule baseline, evidence manifest in `artifacts/`.

```bash
./deploy/scripts/run-demo.sh
docker compose down -v
```

Without Docker (hash-locked dependencies, unit and integration tests, baseline
day, five price areas over 2025):

```bash
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
python -m pytest -q
python -m runner.baseline --run-id local-1 --out artifacts
python -m runner.areas --seeds 177 178 179 --out artifacts/areas-2025
```

## Governed acceptance suite and demo (REMORA in the loop)

```bash
./deploy/scripts/run-governed.sh              # bootstrap, sign, up, acceptance suite, evidence
DAYS=7 ./deploy/scripts/run-demo-governed.sh  # 7-day governed demo (STRATEGY=P1 default, or P3)
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

The proposing runner holds only an operator SDK token and the read token: no
actuator credential, no plant-private route. The actuator credential lives only
in the `twin` and `remora` services.

Each run leaves its evidence under `artifacts/<run-id>/`; the committed
results of 2026-10-27 are under [`results/2026-10-27/`](results/2026-10-27/).

## Federation edges

```bash
python -m runner.edge_check --init <edge-id> --project "<name>" --maintainer <github-handle>
./deploy/scripts/run-edge.sh <edge-id> --local   # stdlib only, no Docker
./deploy/scripts/run-edge.sh <edge-id>           # sandboxed container, exactly as CI
```

Guide: [federation/README.md](federation/README.md).

## Trust boundary

| Listener | Bind | Credential | Routes |
|---|---|---|---|
| read | `observe` network | `TWIN_READ_TOKEN` (`/v1/health` is unauthenticated) | `/v1/zones`, `/v1/zones/{zone_id}`, `/v1/energy`, `/v1/plant`, `/v1/status`, `/v1/operations/{id}` |
| actuator | fixed `plant-private` address only | `TWIN_ACTUATOR_TOKEN` (held only by twin + REMORA dispatcher) | `POST /internal/v1/actuate/setpoint`, `POST /internal/v1/actuate/plant` |
| control | loopback inside twin | `TWIN_CONTROL_TOKEN` | clock advance, stop switch, snapshot |

Networks: `observe` (read-only), `authority` (proposing runner, REMORA,
PostgreSQL), `plant-private` (actuator: only `twin` and `remora`). The
proposing and smoke runners receive only the read (and operator SDK) token and
never join `plant-private`, so they have neither a route nor a credential to
the actuator even with a guessed URL (INV-01/INV-02;
`tests/boundary/test_custody_and_fencing.py`). The twin refuses to start with
missing, short, placeholder or duplicate tokens, or a wildcard actuator bind.

## Upstream pin

REMORA is consumed at commit `123a5fd0` (REMORA-research#1099 included),
recorded in [`upstream/remora.lock.json`](../upstream/remora.lock.json) and
built into `deploy/Dockerfile.remora-ot` and `deploy/Dockerfile.governed`;
`remora.sdk` is installed from that same checkout. Override with
`REMORA_COMMIT` in `compose.governed.yaml` only together with a new acceptance
run. Nothing upstream is patched locally (INV-11). The seams with upstream are
recorded in [integration-contracts.md](integration-contracts.md).
