#!/usr/bin/env bash
# Run one offline federation edge against the frozen fixtures, sandboxed, and
# score it (docs/federation/README.md, step 4). CI runs exactly this.
#
#   ./deploy/scripts/run-edge.sh <edge-id> [fixture-set]
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
EDGE="${1:?usage: run-edge.sh <edge-id> [fixture-set]}"
SET="${2:-v1}"
EDGE_DIR="federation/edges/${EDGE}"
FIX="federation/fixtures/${SET}"
[[ -f "${EDGE_DIR}/edge.json" ]] || { echo "run-edge: ${EDGE_DIR}/edge.json not found" >&2; exit 2; }
[[ -f "${FIX}/manifest.json" ]] || { echo "run-edge: fixture set ${FIX} not found" >&2; exit 2; }
RUN="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="artifacts/edges/${EDGE}/${RUN}"
mkdir -p "$OUT"
chmod 777 "$OUT"  # the sandboxed edge runs as nobody (65534) and writes only here
IMAGE="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['entrypoint'].get('image','python:3.12-slim'))" "${EDGE_DIR}/edge.json")"
mapfile -t CMD < <("$PY" -c "import json,sys;[print(x) for x in json.load(open(sys.argv[1]))['entrypoint']['command']]" "${EDGE_DIR}/edge.json")

# Sandbox: no network, read-only root, no capabilities, non-root, bounded
# resources; the edge sees only its own directory, the fixtures and an output dir.
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 65534:65534 \
  --pids-limit 128 --memory 512m --cpus 1 --tmpfs /tmp:size=16m \
  -v "$PWD/${EDGE_DIR}:/edge:ro" -v "$PWD/${FIX}:/fixtures:ro" -v "$PWD/${OUT}:/out" \
  "$IMAGE" "${CMD[@]}"

"$PY" -m runner.edge_check --edge "$EDGE_DIR" --fixtures "$FIX" --result "${OUT}/result.json" \
  --out "$OUT" --command "./deploy/scripts/run-edge.sh ${EDGE} ${SET}"
