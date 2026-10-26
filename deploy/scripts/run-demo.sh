#!/usr/bin/env bash
# One-command demo (SDD §1 MVP, PR 1 scope): build, start twin, run boundary
# smoke and the baseline scenario from the untrusted runner, export evidence.
# Exit code is non-zero on any failed case. Each run writes a fresh directory.
set -euo pipefail
cd "$(dirname "$0")/../.."

./deploy/scripts/preflight.sh

if [[ ! -f .env ]]; then
  python3 - > .env <<'PY'
import secrets
for k in ("TWIN_READ_TOKEN", "TWIN_ACTUATOR_TOKEN", "TWIN_CONTROL_TOKEN"):
    print(f"{k}=dev-{secrets.token_urlsafe(32)}")
PY
  chmod 600 .env
  echo "run-demo: generated local dev tokens in .env (gitignored)"
fi

export OT_UID="$(id -u)" OT_GID="$(id -g)"
export OT_GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo UNKNOWN)$(git diff --quiet 2>/dev/null || echo +dirty)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
mkdir -p artifacts

docker compose up --build -d --wait twin
trap 'docker compose logs --no-color twin > "artifacts/${RUN_ID}-twin.log" 2>&1 || true' EXIT

docker compose --profile run run --rm runner runner.smoke --run-id "${RUN_ID}-smoke" --out artifacts
docker compose --profile run run --rm runner runner.baseline --run-id "${RUN_ID}-baseline" \
  --config /app/twin/fixtures/three-zone-v1.json --scenario /app/scenarios/baseline-day-v1.json --out artifacts

echo "run-demo: evidence in artifacts/${RUN_ID}-smoke and artifacts/${RUN_ID}-baseline"
