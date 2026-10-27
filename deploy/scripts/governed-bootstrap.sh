#!/usr/bin/env bash
# Prepare the governed profile with LOCAL DEV values (OT-003..OT-005):
#
#   1. append any missing dev secrets to the gitignored .env (twin tokens,
#      PostgreSQL password, REMORA role tokens, REMORA signing keys);
#   2. create the two Ed25519 signer seeds in deploy/.keys/ (gitignored, 0600):
#      the ToolSpec signer and the cumulative-limit authority;
#   3. build the pinned REMORA image and, in a container with no network and
#      no runtime credentials, sign the ToolSpec bundle and the limits policy
#      into deploy/.generated/;
#   4. write the PUBLIC verification values into .env.
#
# REMORA never receives a seed: it verifies with public keys only, so it cannot
# author a larger limit or a different tool contract. Re-run safe: existing
# secrets and seeds are kept; the artifacts are re-signed (step 3 measures the
# current callables, so any adapter edit needs a re-run).
#
# Never commit real tokens, keys, seeds or signed artifacts. A real deployment
# injects secrets from a secret manager and signs on an offline signer host.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"

touch .env
chmod 600 .env

append_if_missing() {
  local key="$1" val="$2"
  grep -q "^${key}=" .env || printf '%s=%s\n' "$key" "$val" >> .env
}
set_value() {  # replace or append: for values derived from the signed artifacts
  local key="$1" val="$2"
  grep -v "^${key}=" .env > .env.tmp || true
  printf '%s=%s\n' "$key" "$val" >> .env.tmp
  mv .env.tmp .env
  chmod 600 .env
}
rand() { "$PY" -c "import secrets;print('dev-'+secrets.token_urlsafe(32))"; }

for k in TWIN_READ_TOKEN TWIN_ACTUATOR_TOKEN TWIN_CONTROL_TOKEN \
         REMORA_PG_PASSWORD \
         REMORA_OPERATOR_TOKEN REMORA_APPROVER_TOKEN REMORA_SUPERVISOR_TOKEN REMORA_VIEWER_TOKEN \
         REMORA_PDP_SIGNING_KEY REMORA_LEASE_SIGNING_KEY REMORA_AUDIT_SIGNING_KEY \
         REMORA_ENVELOPE_SIGNING_KEY; do
  append_if_missing "$k" "$(rand)"
done
append_if_missing REMORA_PG_DB remora
append_if_missing REMORA_PG_USER remora

mkdir -p deploy/.keys deploy/.generated
chmod 700 deploy/.keys
for seed in toolspec limits; do
  f="deploy/.keys/${seed}.seed"
  if [[ ! -s "$f" ]]; then
    ( umask 077; "$PY" -c "import secrets;print(secrets.token_hex(32))" > "$f" )
  fi
done

# Build directly, not through compose: compose would first interpolate the
# public values this script has not produced yet. The commit comes from the
# lock file, the single source of the upstream pin.
commit="$("$PY" -c "import json;print(json.load(open('upstream/remora.lock.json'))['commit'])")"
docker build -q -f deploy/Dockerfile.remora-ot \
  --build-arg REMORA_REPO="${REMORA_REPO:-https://github.com/darklordVirtual/REMORA-research}" \
  --build-arg REMORA_COMMIT="$commit" \
  -t autonomous-ot-control/remora-ot:local . >/dev/null

# Sign with the image's own REMORA, offline: no network, none of the runtime's
# environment (docker run, not compose run, so no service env is injected).
out="$(docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  -e OT_TOOLSPEC_SIGNING_SEED="$(cat deploy/.keys/toolspec.seed)" \
  -e OT_LIMITS_SIGNING_SEED="$(cat deploy/.keys/limits.seed)" \
  -v "$PWD/deploy/.generated:/out" \
  --entrypoint python autonomous-ot-control/remora-ot:local \
  /app/sign_deployment.py --out /out)"

while IFS='=' read -r key val; do
  case "$key" in
    REMORA_TOOLSPEC_VERIFY_KEYS|REMORA_TOOLSPEC_PINNED_DIGEST|REMORA_LIMITS_TRUSTED_KEYS)
      set_value "$key" "$val" ;;
    *) echo "governed-bootstrap: unexpected signer output: ${key}" >&2; exit 1 ;;
  esac
done <<< "$out"

echo "governed-bootstrap: .env, deploy/.keys/ and deploy/.generated/ are ready (all gitignored)."
