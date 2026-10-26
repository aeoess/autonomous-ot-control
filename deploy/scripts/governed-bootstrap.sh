#!/usr/bin/env bash
# Generate LOCAL DEV values for the governed profile (PR 2) into the gitignored
# .env. Never commit real tokens, keys or a signed limits policy. Production
# injects all of these from a secret manager and re-signs the limits policy.
#
# This writes only what the governed Compose profile needs beyond the three twin
# tokens that run-demo.sh already generates: a PostgreSQL password, the REMORA
# role-separated dev tokens, and the four REMORA signing keys. Re-run safe: it
# appends only missing keys.
set -euo pipefail
cd "$(dirname "$0")/../.."

touch .env
chmod 600 .env

append_if_missing() {
  local key="$1" val="$2"
  grep -q "^${key}=" .env || printf '%s=%s\n' "$key" "$val" >> .env
}

rand() { python3 -c "import secrets;print('dev-'+secrets.token_urlsafe(32))"; }

# PostgreSQL authority store.
append_if_missing REMORA_PG_DB remora
append_if_missing REMORA_PG_USER remora
append_if_missing REMORA_PG_PASSWORD "$(rand)"

# REMORA role-separated dev tokens (operator cannot approve; approver/supervisor
# are admins; viewer is read-only).
append_if_missing REMORA_OPERATOR_TOKEN "$(rand)"
append_if_missing REMORA_APPROVER_TOKEN "$(rand)"
append_if_missing REMORA_SUPERVISOR_TOKEN "$(rand)"
append_if_missing REMORA_VIEWER_TOKEN "$(rand)"

# REMORA signing keys (local dev values).
append_if_missing REMORA_PDP_SIGNING_KEY "$(rand)"
append_if_missing REMORA_LEASE_SIGNING_KEY "$(rand)"
append_if_missing REMORA_AUDIT_SIGNING_KEY "$(rand)"
append_if_missing REMORA_ENVELOPE_SIGNING_KEY "$(rand)"

echo "governed-bootstrap: wrote any missing dev values to .env (gitignored)."
echo "Enforced cumulative limits (deploy/ot/ot_limits.policy.json) require"
echo "signing with REMORA scripts/sign_limit_policy.py and REMORA_REQUIRE_LIMITS=1;"
echo "they are OFF by default here."
