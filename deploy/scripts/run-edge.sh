#!/usr/bin/env bash
# Run one offline federation edge against the frozen fixtures, sandboxed, and
# score it (docs/federation/README.md, step 4). CI runs exactly this.
#
#   ./deploy/scripts/run-edge.sh <edge-id> [fixture-set] [--local]
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
EDGE="${1:?usage: run-edge.sh <edge-id> [fixture-set] [--local]}"
SET="v1"
LOCAL=0
for arg in "${@:2}"; do
  if [[ "$arg" == "--local" ]]; then
    LOCAL=1
  else
    SET="$arg"
  fi
done
EDGE_DIR="federation/edges/${EDGE}"
FIX="federation/fixtures/${SET}"
[[ -f "${EDGE_DIR}/edge.json" ]] || { echo "run-edge: ${EDGE_DIR}/edge.json not found" >&2; exit 2; }
[[ -f "${FIX}/manifest.json" ]] || { echo "run-edge: fixture set ${FIX} not found" >&2; exit 2; }
"$PY" -m runner.edge_check --validate "$EDGE_DIR" >/dev/null
RUN="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="artifacts/edges/${EDGE}/${RUN}"
mkdir -p "$OUT"

if [[ "$LOCAL" -eq 1 ]]; then
  "$PY" "${EDGE_DIR}/adapter.py" run --fixtures "$FIX" --out "${OUT}/result.json"
else
  chmod 777 "$OUT"  # the sandboxed edge runs as nobody (65534) and writes only here
  IMAGE="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['entrypoint'].get('image','python:3.12-slim'))" "${EDGE_DIR}/edge.json" | tr -d '\r')"
  # tr -d '\r': a Windows Python prints CRLF, which would end up inside the argv.
  mapfile -t CMD < <("$PY" -c "import json,sys;[print(x) for x in json.load(open(sys.argv[1]))['entrypoint']['command']]" "${EDGE_DIR}/edge.json" | tr -d '\r')

  # Sandbox: no network, read-only root, no capabilities, non-root, bounded
  # resources; the edge sees only its own directory, the fixtures and an output dir.
  MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user 65534:65534 \
    --pids-limit 128 --memory 512m --cpus 1 --tmpfs /tmp:size=16m \
    -v "$PWD/${EDGE_DIR}:/edge:ro" -v "$PWD/${FIX}:/fixtures:ro" -v "$PWD/${OUT}:/out" \
    "$IMAGE" "${CMD[@]}"
fi

"$PY" -m runner.edge_check --edge "$EDGE_DIR" --fixtures "$FIX" --result "${OUT}/result.json" \
  --out "$OUT" --command "./deploy/scripts/run-edge.sh ${EDGE} ${SET}"

