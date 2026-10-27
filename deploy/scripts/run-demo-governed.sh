#!/usr/bin/env bash
# Governed demonstration (runner/demo.py) on a fresh stack: the building-services
# twin on the verified Trondheim week, REMORA in the loop for every read and
# write, then an offline replay and the savings comparison against P0.
#
#   DAYS=7 ./deploy/scripts/run-demo-governed.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
export MSYS_NO_PATHCONV=1
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-aoc-demo}"
export TWIN_CONFIG=/app/twin/fixtures/three-zone-hvac-v1.json
export TWIN_SCENARIO=/app/scenarios/trondheim-week-2026w03-hvac-v1.json
COMPOSE=(docker compose -f compose.yaml -f compose.governed.yaml)
RUN_ID="${RUN_ID:-demo-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
mkdir -p artifacts

./deploy/scripts/preflight.sh
# Accelerated-simulation limits (see the policy file comment); LIM-01 covers the real ones.
LIMITS_POLICY=ot_limits.demo-accelerated.policy.json PYTHON="$PY" ./deploy/scripts/governed-bootstrap.sh
export OT_UID="$(id -u)" OT_GID="$(id -g)"
export OT_GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo UNKNOWN)$(git diff --quiet 2>/dev/null || echo +dirty)"

"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
trap '"${COMPOSE[@]}" logs --no-color remora twin > "artifacts/${RUN_ID}-stack.log" 2>&1 || true; "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true' EXIT
"${COMPOSE[@]}" up --build -d --wait postgres remora twin harness

"$PY" -m runner.demo --run-id "$RUN_ID" --out artifacts --project "$COMPOSE_PROJECT_NAME" --days "${DAYS:-7}" --strategy "${STRATEGY:-P1}"
