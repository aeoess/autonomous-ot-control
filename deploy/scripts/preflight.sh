#!/usr/bin/env bash
# Fail closed before anything starts: no committed secrets, pinned upstream, lockfiles.
set -euo pipefail
cd "$(dirname "$0")/../.."
fail() { echo "preflight: $*" >&2; exit 1; }

[[ -f requirements.lock && -f requirements-dev.lock ]] || fail "lockfiles missing"
grep -q -- "--hash=sha256:" requirements.lock || fail "requirements.lock is not hash-pinned"

python3 - <<'PY' || fail "upstream pin invalid"
import json, re, sys
p = json.load(open("upstream/remora.lock.json"))
sys.exit(0 if re.fullmatch(r"[0-9a-f]{40}", p.get("commit", "")) else 1)
PY

if git rev-parse --git-dir >/dev/null 2>&1; then
  if git ls-files --error-unmatch .env >/dev/null 2>&1; then fail ".env is tracked by git"; fi
  if git grep -nIE "(TWIN_(READ|ACTUATOR|CONTROL)_TOKEN)=[A-Za-z0-9_-]{24,}" -- ':!.env.example' ':!deploy/scripts/*' >/dev/null; then
    fail "a twin token value appears in tracked files"
  fi
fi
echo "preflight: ok"
