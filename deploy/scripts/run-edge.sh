#!/usr/bin/env bash
# Run one offline federation edge against a sealed fixture set and score it
# (docs/federation/README.md, step 4). CI runs exactly this.
#
#   ./deploy/scripts/run-edge.sh <edge-id> [fixture-set] [--local]
#
# Environment (recorded in run-record.json, never trusted beyond what it says):
#   OPERATOR  PRODUCER (default) | EXTERNAL          who runs it
#   HOST      PRODUCER_CONTROLLED (default) | EXTERNAL   whose machine
#   RUN_REF   URL of your published run, if any
#   PYTHON    interpreter (default python3; on Windows Git Bash: PYTHON=python)
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
EDGE="${1:?usage: run-edge.sh <edge-id> [fixture-set] [--local]}"
SET="v2"   # the latest sealed set; name an older one as the second argument
LOCAL=0
for arg in "${@:2}"; do
  if [[ "$arg" == "--local" ]]; then
    LOCAL=1
  else
    SET="$arg"
  fi
done
# Only these shapes may reach a path (SDD-OT-002 SR-01).
[[ "$EDGE" =~ ^_?[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo "run-edge: invalid edge id: ${EDGE}" >&2; exit 2; }
[[ "$SET" =~ ^v[0-9]{1,4}$ ]] || { echo "run-edge: invalid fixture set: ${SET}" >&2; exit 2; }
EDGE_DIR="federation/edges/${EDGE}"
FIX="federation/fixtures/${SET}"
OPERATOR="${OPERATOR:-PRODUCER}"
HOST_KIND="${HOST:-PRODUCER_CONTROLLED}"
[[ -f "${EDGE_DIR}/edge.json" ]] || { echo "run-edge: ${EDGE_DIR}/edge.json not found" >&2; exit 2; }
[[ -f "${FIX}/manifest.json" ]] || { echo "run-edge: fixture set ${FIX} not found" >&2; exit 2; }
"$PY" -m runner.edge_check --validate "$EDGE_DIR" >/dev/null
"$PY" -m runner.edge_check --check-fixtures "$FIX" >/dev/null
RUN="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="artifacts/edges/${EDGE}/${RUN}"
[[ ! -e "$OUT" ]] || { echo "run-edge: ${OUT} exists; a run never overwrites an earlier one" >&2; exit 2; }
mkdir -p "$OUT"
REF_ARGS=()
[[ -n "${RUN_REF:-}" ]] && REF_ARGS=(--run-ref "$RUN_REF")

# One sandbox for every run (runner.edge_sandbox, SDD-OT-002 SR-01..SR-03): the edge.json is validated, the
# image must be pinned by digest, the container has no network, a read-only root, no capabilities and runs
# as nobody, and writes only to a private scratch directory from which the host takes result.json alone,
# refusing links. --local runs the adapter on this host instead, and only because you name it trusted.
TRUST_ARGS=()
CMD_NOTE="./deploy/scripts/run-edge.sh ${EDGE} ${SET}"
if [[ "$LOCAL" -eq 1 ]]; then
  TRUST_ARGS=(--trust-edge "$EDGE")  # the directory you named, already validated above
  CMD_NOTE="${CMD_NOTE} --local"
fi
"$PY" -m runner.edge_check --run "$EDGE_DIR" --fixtures "$FIX" --out "$OUT" "${TRUST_ARGS[@]}" \
  --operator "$OPERATOR" --host "$HOST_KIND" "${REF_ARGS[@]}" --command "$CMD_NOTE"
