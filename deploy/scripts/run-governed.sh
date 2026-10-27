#!/usr/bin/env bash
# Governed acceptance suite (OT-005, SDD §11 Gate A subset) on a fresh stack:
# bootstrap dev secrets and signed artifacts, start PostgreSQL + REMORA + twin +
# harness, run runner.acceptance from the host, export evidence, tear down.
# Exit code is the suite's: any FAIL not listed in ALLOW_FAILING is non-zero.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
export MSYS_NO_PATHCONV=1
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-aoc-governed}"
COMPOSE=(docker compose -f compose.yaml -f compose.governed.yaml)
RUN_ID="${RUN_ID:-governed-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
mkdir -p artifacts

./deploy/scripts/preflight.sh
PYTHON="$PY" ./deploy/scripts/governed-bootstrap.sh
export OT_UID="$(id -u)" OT_GID="$(id -g)"
export OT_GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo UNKNOWN)$(git diff --quiet 2>/dev/null || echo +dirty)"

"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
trap '"${COMPOSE[@]}" logs --no-color remora twin > "artifacts/${RUN_ID}-stack.log" 2>&1 || true' EXIT
"${COMPOSE[@]}" up --build -d --wait postgres remora twin harness

# shellcheck disable=SC2086
"$PY" -m runner.acceptance --run-id "$RUN_ID" --out artifacts --project "$COMPOSE_PROJECT_NAME" \
  ${ALLOW_FAILING:+--allow-failing $ALLOW_FAILING}
