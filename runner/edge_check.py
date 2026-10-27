"""Score an edge's result against the fixture manifest and write its report.

    python -m runner.edge_check --edge federation/edges/<id> --fixtures federation/fixtures/v1 \\
        --result <result.json> --out <dir> [--label reproduction|independent]

Or scaffold / validate an edge locally without Docker:

    python -m runner.edge_check --init <edge-id> --project "<name>" --maintainer <github-handle>
    python -m runner.edge_check --validate federation/edges/<id>

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
import re
import shutil
import sys
from pathlib import Path
from typing import Any

VERDICTS = ("ESTABLISHED", "CONTRADICTED", "NOT_ESTABLISHED")
ROOT = Path(__file__).resolve().parents[1]
EDGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
BOUNDARIES = ("effect_evidence", "corpus_adequacy", "runner_validity",
              "admission_runtime", "tool_definition", "authority_fixture")
MODES = ("offline", "inline")


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


def validate_edge_manifest(edge_dir: Path) -> list[str]:
    """Validate an edge directory and its edge.json against the contract."""
    problems: list[str] = []
    manifest_path = edge_dir / "edge.json"
    if not manifest_path.is_file():
        return [f"{manifest_path} does not exist"]
    try:
        edge = json.loads(manifest_path.read_text("utf-8"))
    except ValueError as e:
        return [f"{manifest_path}: invalid JSON ({e})"]
    req = ("schema", "edge_id", "project", "maintainer", "boundary", "mode",
           "pin", "licence", "claim_ceiling", "entrypoint")
    for k in req:
        if k not in edge:
            problems.append(f"missing required field {k!r}")
    if edge.get("schema") != "ot-edge-manifest/v1":
        problems.append("schema must be 'ot-edge-manifest/v1'")
    eid = str(edge.get("edge_id", ""))
    if not EDGE_ID_RE.match(eid):
        problems.append(f"edge_id {eid!r} must match {EDGE_ID_RE.pattern}")
    if edge_dir.name != "_template" and eid != edge_dir.name:
        problems.append(f"edge_id {eid!r} does not match directory name {edge_dir.name!r}")
    if edge.get("boundary") not in BOUNDARIES:
        problems.append(f"boundary must be one of {BOUNDARIES}")
    if edge.get("mode") not in MODES:
        problems.append(f"mode must be one of {MODES}")
    pin = edge.get("pin")
    if not isinstance(pin, dict) or pin.get("kind") not in ("git_commit", "image_digest", "package") \
            or not str(pin.get("value", "")).strip():
        problems.append("pin must be {kind: git_commit|image_digest|package, value: <non-empty>}")
    ep = edge.get("entrypoint")
    if not isinstance(ep, dict):
        problems.append("entrypoint must be an object")
    elif edge.get("mode") == "offline" and (not isinstance(ep.get("command"), list) or not ep.get("command")):
        problems.append("offline edge entrypoint requires a non-empty command list")
    return problems


def init_edge(edge_id: str, *, project: str, maintainer: str, boundary: str = "effect_evidence",
              mode: str = "offline", root: Path = ROOT) -> Path:
    """Scaffold a new federation edge from _template."""
    if not EDGE_ID_RE.match(edge_id):
        raise ValueError(f"edge_id {edge_id!r} must match {EDGE_ID_RE.pattern}")
    src = root / "federation" / "edges" / "_template"
    dst = root / "federation" / "edges" / edge_id
    if dst.exists():
        raise FileExistsError(f"{dst} already exists")
    shutil.copytree(src, dst)
    edge_path = dst / "edge.json"
    edge = json.loads(edge_path.read_text("utf-8"))
    edge.update({"edge_id": edge_id, "project": project, "maintainer": maintainer,
                 "boundary": boundary, "mode": mode})
    edge_path.write_text(json.dumps(edge, indent=2) + "\n", encoding="utf-8")
    adapter_path = dst / "adapter.py"
    if adapter_path.is_file():
        txt = adapter_path.read_text("utf-8").replace(
            'EDGE_ID = "template-reference-verifier"', f'EDGE_ID = "{edge_id}"')
        adapter_path.write_text(txt, encoding="utf-8")
    return dst


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.edge_check")
    p.add_argument("--init", metavar="EDGE_ID", help="scaffold a new edge from _template")
    p.add_argument("--project", default="My Federation Edge", help="project name for --init")
    p.add_argument("--maintainer", default="github-handle", help="GitHub handle for --init")
    p.add_argument("--boundary", default="effect_evidence", choices=list(BOUNDARIES))
    p.add_argument("--mode", default="offline", choices=list(MODES))
    p.add_argument("--validate", metavar="EDGE_DIR", help="validate an edge manifest and exit")
    p.add_argument("--edge")
    p.add_argument("--fixtures")
    p.add_argument("--result")
    p.add_argument("--out")
    p.add_argument("--label", default="reproduction", choices=["reproduction", "independent"])
    p.add_argument("--command", default="./deploy/scripts/run-edge.sh")
    a = p.parse_args(argv)
    if a.init:
        dst = init_edge(a.init, project=a.project, maintainer=a.maintainer,
                        boundary=a.boundary, mode=a.mode)
        print(f"Created {dst}\nNext: ./deploy/scripts/run-edge.sh {a.init}")
        return 0
    if a.validate:
        probs = validate_edge_manifest(Path(a.validate))
        if probs:
            for pr in probs:
                print(f"error: {pr}", file=sys.stderr)
            return 1
        print(f"{a.validate}: valid ot-edge-manifest/v1")
        return 0
    if not (a.edge and a.fixtures and a.result and a.out):
        p.error("--edge, --fixtures, --result and --out are required when scoring")
    edge_dir = Path(a.edge)
    probs = validate_edge_manifest(edge_dir)
    if probs:
        for pr in probs:
            print(f"manifest error: {pr}", file=sys.stderr)
        return 1
    edge = json.loads((edge_dir / "edge.json").read_text("utf-8"))
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
