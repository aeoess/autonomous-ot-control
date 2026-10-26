#!/usr/bin/env bash
# Pack one evidence directory with a SHA-256 listing. Refuses to overwrite.
set -euo pipefail
dir="${1:?usage: export-evidence.sh artifacts/<run-id>}"
[[ -f "$dir/manifest.json" ]] || { echo "no manifest.json in $dir" >&2; exit 1; }
out="${dir%/}.tar.gz"
[[ ! -e "$out" ]] || { echo "$out exists; refusing to overwrite" >&2; exit 1; }
( cd "$dir" && sha256sum -- * > SHA256SUMS )
tar -czf "$out" -C "$(dirname "$dir")" "$(basename "$dir")"
sha256sum "$out"
