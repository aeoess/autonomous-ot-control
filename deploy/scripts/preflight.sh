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

# OT-003/D-05: the signed ToolSpec's declared bounds must agree with the twin's
# local interlock. A silent divergence would let REMORA authorize a setpoint the
# simulator would reject (or vice versa); fail the build instead.
python3 - <<'PY' || fail "signed ToolSpec bounds disagree with twin guard config"
import json, sys
spec = json.load(open("contracts/ot-toolspec-v1.json"))
cfg = json.load(open("twin/fixtures/three-zone-v1.json"))
write = next(t for t in spec["tools"] if t["tool_name"] == "set_zone_setpoint")
lim, guard = write["limits"], cfg["guard"]
ok = all(lim[k] == guard[k] for k in
         ("max_delta_c", "min_interval_seconds", "max_changes_per_hour", "max_observation_age_seconds"))
sp = write["arguments"]["setpoint_c"]
# Every setpoint the ToolSpec permits must also be within each zone's physical
# bounds, so the signed contract never authorizes a value the guard would reject.
bounds_ok = all(sp["minimum"] >= z["min_setpoint_c"] and sp["maximum"] <= z["max_setpoint_c"]
                for z in cfg["zones"])
sys.exit(0 if ok and bounds_ok else 1)
PY

if git rev-parse --git-dir >/dev/null 2>&1; then
  if git ls-files --error-unmatch .env >/dev/null 2>&1; then fail ".env is tracked by git"; fi
  if git grep -nIE "(TWIN_(READ|ACTUATOR|CONTROL)_TOKEN)=[A-Za-z0-9_-]{24,}" -- ':!.env.example' ':!deploy/scripts/*' >/dev/null; then
    fail "a twin token value appears in tracked files"
  fi
fi
echo "preflight: ok"
