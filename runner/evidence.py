"""Evidence bundle writer (INV-08).

Each run writes into a *fresh* directory; an existing directory is an error,
so an old report can never be passed off as a new result. Claims carry an
explicit status; anything not exercised by this run is NOT_ESTABLISHED.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from twin.state import canonical_digest, canonical_json

EVIDENCE_SCHEMA = "ot-evidence-manifest/v1"
CLAIM_STATUSES = ("IMPLEMENTED", "TESTED_LOCALLY", "TESTED_DOCKER", "HOSTED_VERIFIED",
                  "EXTERNALLY_REPRODUCED", "NOT_ESTABLISHED")
REPO_ROOT = Path(__file__).resolve().parents[1]


def git_sha() -> str:
    env = os.environ.get("OT_GIT_SHA", "").strip()
    if env:
        return env
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                             text=True, check=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True,
                               text=True, timeout=10).stdout.strip()
        return out.stdout.strip() + ("+dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def upstream_pin() -> dict[str, Any]:
    p = REPO_ROOT / "upstream" / "remora.lock.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {"status": "NOT_ESTABLISHED"}


def _sha256_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def write_bundle(out_dir: str | Path, *, suite_id: str, run_id: str, manifest_extra: dict[str, Any],
                 metrics: dict[str, Any], trace: list[dict[str, Any]], claims: dict[str, str],
                 results: list[dict[str, Any]] | None = None) -> Path:
    for c, s in claims.items():
        if s not in CLAIM_STATUSES:
            raise ValueError(f"claim {c} has invalid status {s}")
    root = Path(out_dir) / run_id
    root.mkdir(parents=True, exist_ok=False)  # fresh directory or fail
    trace_path = root / "trace.jsonl"
    with trace_path.open("wb") as f:
        for row in trace:
            f.write(canonical_json(row) + b"\n")
    metrics_path = root / "metrics.json"
    metrics_path.write_bytes(canonical_json(metrics))
    files = {"trace.jsonl": _sha256_file(trace_path), "metrics.json": _sha256_file(metrics_path)}
    results = results or []
    failed = [r for r in results if r.get("outcome") != "PASS"]
    if results:
        rp = root / "results.json"
        rp.write_bytes(canonical_json(results))
        files["results.json"] = _sha256_file(rp)
    manifest = {
        "schema": EVIDENCE_SCHEMA,
        "suite_id": suite_id,
        "run_id": run_id,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": git_sha(),
        "image_digest": os.environ.get("OT_IMAGE_DIGEST", "NOT_ESTABLISHED"),
        "upstream_remora": upstream_pin(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "trace_digest": canonical_digest(trace),
        "metrics_digest": canonical_digest(metrics),
        "files": files,
        "counts": {"cases": len(results), "passed": len(results) - len(failed),
                   "failed": len(failed), "skipped": 0},
        "claims": dict(sorted(claims.items())),
        "limitations": [
            "Simulation only; no physical OT write path exists in this build.",
            "REMORA governance is not exercised by this bundle unless a claim says so.",
            "Observer vantage is same_test_deployment, not external verification.",
        ],
        **manifest_extra,
    }
    (root / "manifest.json").write_bytes(canonical_json(manifest))
    return root
