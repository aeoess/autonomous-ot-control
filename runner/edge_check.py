"""Score an edge's result against the fixture manifest and write its report.

    python -m runner.edge_check --edge federation/edges/<id> --fixtures federation/fixtures/v1 \\
        --result <result.json> --out <dir> [--label reproduction|independent]

The run passes only if:
- the result names the fixture set it was run on (by manifest SHA-256);
- every case in the manifest has exactly one verdict;
- no negative control is ESTABLISHED (each seeded fault must be caught).

Positive cases that are not ESTABLISHED are reported as false refusals. They
are counted in the report, not hidden, and do not fail the run: an edge may be
stricter than the testbed. A run that answers nothing fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

VERDICTS = ("ESTABLISHED", "CONTRADICTED", "NOT_ESTABLISHED")


def score(manifest: dict[str, Any], manifest_sha: str, result: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    if result.get("schema") != "ot-edge-result/v1":
        problems.append("result schema is not ot-edge-result/v1")
    if result.get("fixtures_sha256") != manifest_sha:
        problems.append("result was not produced on this fixture set (fixtures_sha256 differs)")
    expected = {c["case_id"]: c for c in manifest["cases"]}
    seen: dict[str, dict[str, Any]] = {}
    for v in result.get("verdicts", []):
        if v.get("case_id") in seen:
            problems.append(f"duplicate verdict for {v.get('case_id')}")
        if v.get("verdict") not in VERDICTS:
            problems.append(f"{v.get('case_id')}: unknown verdict {v.get('verdict')!r}")
        seen[v.get("case_id")] = v
    missing = sorted(set(expected) - set(seen))
    extra = sorted(set(seen) - set(expected))
    if missing:
        problems.append(f"{len(missing)} case(s) unanswered: {missing[:5]}")
    if extra:
        problems.append(f"verdicts for unknown cases: {extra[:5]}")
    pos = [c for c in expected.values() if c["kind"] == "positive"]
    neg = [c for c in expected.values() if c["kind"] == "negative"]
    caught = [c for c in neg if seen.get(c["case_id"], {}).get("verdict") == "CONTRADICTED"]
    passed_negatives = [c for c in neg if seen.get(c["case_id"], {}).get("verdict") == "ESTABLISHED"]
    established = [c for c in pos if seen.get(c["case_id"], {}).get("verdict") == "ESTABLISHED"]
    if passed_negatives:
        problems.append(f"{len(passed_negatives)} negative control(s) ESTABLISHED: "
                        f"{[c['fault'] for c in passed_negatives]}")
    if not seen:
        problems.append("no verdicts")
    return {"ok": not problems, "problems": problems,
            "positives": len(pos), "positives_established": len(established),
            "false_refusals": len(pos) - len(established),
            "negatives": len(neg), "negatives_caught": len(caught),
            "negatives_not_established": len(neg) - len(caught) - len(passed_negatives),
            "negatives_passed": len(passed_negatives),
            "by_fault": {f: sum(1 for c in caught if c["fault"] == f) for f in sorted({c["fault"] for c in neg})}}


def render(edge: dict[str, Any], manifest: dict[str, Any], manifest_sha: str, s: dict[str, Any],
           label: str, command: str) -> str:
    lines = [f"# Edge report: {edge['edge_id']}", "",
             f"- Project: {edge['project']} (maintainer {edge['maintainer']})",
             f"- Boundary / mode: {edge['boundary']} / {edge['mode']}",
             f"- Edge pin: {edge['pin']['kind']} {edge['pin']['value']}",
             f"- Fixture set: {manifest['fixture_set']} (manifest sha256 {manifest_sha})",
             f"- Testbed commit: {manifest.get('testbed_commit', 'UNKNOWN')}",
             f"- Label: **{label}**", f"- Command: `{command}`", "",
             f"**Result: {'PASS' if s['ok'] else 'FAIL'}**", "",
             "| | Count |", "|---|---|",
             f"| Positive cases ESTABLISHED | {s['positives_established']} / {s['positives']} |",
             f"| Positive cases refused (false refusals) | {s['false_refusals']} |",
             f"| Negative controls caught (CONTRADICTED) | {s['negatives_caught']} / {s['negatives']} |",
             f"| Negative controls NOT_ESTABLISHED | {s['negatives_not_established']} |",
             f"| Negative controls passed (must be 0) | {s['negatives_passed']} |", "",
             "Caught by fault: " + ", ".join(f"{k} {v}" for k, v in s["by_fault"].items()), "",
             f"Claim ceiling (the edge's own words): {edge['claim_ceiling']}", ""]
    if s["problems"]:
        lines += ["Problems:", *[f"- {p}" for p in s["problems"]], ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.edge_check")
    p.add_argument("--edge", required=True)
    p.add_argument("--fixtures", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--label", default="reproduction", choices=["reproduction", "independent"])
    p.add_argument("--command", default="./deploy/scripts/run-edge.sh")
    a = p.parse_args(argv)
    edge = json.loads((Path(a.edge) / "edge.json").read_text("utf-8"))
    raw = (Path(a.fixtures) / "manifest.json").read_bytes()
    manifest, sha = json.loads(raw), hashlib.sha256(raw).hexdigest()
    result = json.loads(Path(a.result).read_text("utf-8"))
    s = score(manifest, sha, result)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "score.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render(edge, manifest, sha, s, a.label, a.command), encoding="utf-8")
    print(render(edge, manifest, sha, s, a.label, a.command))
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
