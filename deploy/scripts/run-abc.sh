#!/usr/bin/env bash
# The federation A/B/C on one episode (ADR-004): arm A offline, arms B and C
# on the governed stack, then the comparison and the report.
#
#   DAYS=7 ./deploy/scripts/run-abc.sh
#
# Arms run, in order:
#   A              runner.arm_a (ungoverned, known-negative), offline, no Docker
#   B-careful      runner.demo, REMORA in the loop, careful reviewer
#   B-rubber_stamp runner.demo, rubber-stamp reviewer (what REMORA enforces without a human)
#   C-rubber_stamp runner.demo, rubber-stamp reviewer plus the reference inline edge
#                  (federation/edges/_inline_template, served on this host), so the
#                  edge's contribution is visible
#
# Every governed arm is a fresh stack on the same twin and inputs. The episode
# must match DAYS (scenarios/episodes/<scenario>-p1-<DAYS>d-v1.json). Output:
# artifacts/abc-<run>/abc-report.md|html, abc-summary.json, one work dir per arm.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
DAYS="${DAYS:-7}"
RUN_ID="${RUN_ID:-abc-$(date -u +%Y%m%dT%H%M%SZ)}"
ATTACK_SET="${ATTACK_SET:-v1}"   # v1, or all-rules: one injected class per oracle rule (runner.attacks)
SUFFIX="-v1"; [[ "$ATTACK_SET" == "all-rules" ]] && SUFFIX="-all-rules-v1"
EPISODE="scenarios/episodes/trondheim-week-2026w03-hvac-v1-p1-${DAYS}d${SUFFIX}.json"
OUT="artifacts/${RUN_ID}"
EDGE_PORT="${EDGE_PORT:-8765}"
[[ -f "$EPISODE" ]] || { echo "run-abc: no episode for DAYS=${DAYS}: ${EPISODE} (python -m runner.episode build --days ${DAYS} --attack-set ${ATTACK_SET} --out ${EPISODE})" >&2; exit 2; }
"$PY" -m runner.episode check "$EPISODE"
mkdir -p "$OUT"

# Resumable: an arm whose directory already exists under OUT is kept, so a run
# interrupted by a failing arm can be continued with the same RUN_ID.
if [[ -d "${OUT}/arm-a" ]]; then
  echo "== arm A (offline): already present, kept"
else
  echo "== arm A (offline)"
  "$PY" -m runner.arm_a --episode "$EPISODE" --out "${OUT}/arm-a"
fi

# SHADOW_EDGES: space-separated EDGE_DIR[=URL] specs asked about every proposal of every governed
# arm, before REMORA and without effect (runner.demo --shadow-edge); reported per arm by ab_compare.
SHADOW_ARGS=""
for spec in ${SHADOW_EDGES:-}; do SHADOW_ARGS+=" --shadow-edge ${spec}"; done

run_governed() {  # label, extra demo args...
  local label="$1"; shift
  if [[ -d "${OUT}/${label}" ]]; then
    echo "== arm ${label}: already present, kept"
    return 0
  fi
  echo "== arm ${label}"
  # Leftovers of an interrupted attempt of this arm: its work dir and its evidence bundle.
  rm -rf "artifacts/${RUN_ID}-${label}-work" "artifacts/${RUN_ID}-${label}"
  # A governed arm that is not the careful acceptance may let a forbidden proposal through;
  # that is the measurement, so the demo exits 0 for such arms. Any other failure stops here.
  RUN_ID="${RUN_ID}-${label}" STRATEGY=P1 DAYS="$DAYS" DEMO_ARGS="$* --attack-set ${ATTACK_SET} ${SHADOW_ARGS}" ./deploy/scripts/run-demo-governed.sh
  mv "artifacts/${RUN_ID}-${label}-work" "${OUT}/${label}"
}
run_governed B-careful --reviewer careful
run_governed B-rubber_stamp --reviewer rubber_stamp

echo "== reference inline edge on this host, port ${EDGE_PORT}"
"$PY" federation/edges/_inline_template/server.py --port "$EDGE_PORT" &
EDGE_PID=$!
trap 'kill "$EDGE_PID" 2>/dev/null || true' EXIT
sleep 1
run_governed C-rubber_stamp --reviewer rubber_stamp --inline-edge federation/edges/_inline_template \
  --inline-edge-url "http://127.0.0.1:${EDGE_PORT}/decide"

echo "== compare"
"$PY" -m runner.ab_compare --episode "$EPISODE" --out "$OUT" \
  --arm "A=${OUT}/arm-a" --arm "B-careful=${OUT}/B-careful" --arm "B-rubber_stamp=${OUT}/B-rubber_stamp" \
  --arm "C-rubber_stamp=${OUT}/C-rubber_stamp"
echo "report: ${OUT}/abc-report.md"
# Use of each offline edge across the arms (runner.edge_usage): usage-summary.md beside the per-arm passes.
"$PY" -m runner.edge_usage --root "${OUT}/edges" --out "${OUT}/edges" >/dev/null   && echo "edge usage: ${OUT}/edges/usage-summary.md" || echo "edge usage: no offline edge pass in this run"
