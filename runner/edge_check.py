"""Run, score and record a federation edge against a sealed fixture set.

Self-service, in one process and with the standard library only:

    python -m runner.edge_check --init <edge-id> --project "<name>" --maintainer <github-handle>
    python -m runner.edge_check --validate federation/edges/<id>
    python -m runner.edge_check --check-fixtures federation/fixtures/v1
    python -m runner.edge_check --run federation/edges/<id> --fixtures federation/fixtures/v1 --out <dir> \\
        [--operator PRODUCER|EXTERNAL] [--host PRODUCER_CONTROLLED|EXTERNAL] [--run-ref <url>]

Scoring an adapter result that was produced elsewhere (the sandboxed Docker
path in deploy/scripts/run-edge.sh):

    python -m runner.edge_check --edge federation/edges/<id> --fixtures federation/fixtures/v1 \\
        --result <result.json> --out <dir> [--operator ..] [--host ..] [--command "<what ran>"]

Records and the results table:

    python -m runner.edge_check --check-records          # validate federation/records/**.json
    python -m runner.edge_check --results                # regenerate docs/federation/RESULTS.md
    python -m runner.edge_check --check-results          # fail if RESULTS.md differs from the records

A run passes only if the fixture set verifies against its own manifest (every
case file digest, the package digest), the result names that fixture set by
manifest SHA-256, every case has exactly one verdict, and no negative control
is ESTABLISHED. Positive cases that are not ESTABLISHED are false refusals:
counted and reported, never hidden, and they do not fail the run.

Every run writes `run-record.json` (contracts/edge-run-record-v1.schema.json).
The independence level in it is derived from the edge's declared evaluator,
the operator and the host; it is never declared by the author.

Runner contract (the properties tests/unit/test_federation_runner_contract.py
establishes for the --run path): a fresh working directory per run, success
exit 0, verifier failure exit non-zero with no report and no PASS, an existing
output refused, a dependency failure non-zero, a killed verifier non-zero, and
a malformed result failing closed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

VERDICTS = ("ESTABLISHED", "CONTRADICTED", "NOT_ESTABLISHED")
ROOT = Path(__file__).resolve().parents[1]
EDGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
BOUNDARIES = ("effect_evidence", "corpus_adequacy", "runner_validity",
              "admission_runtime", "tool_definition", "authority_fixture")
MODES = ("offline", "inline")
OPERATORS = ("PRODUCER", "EXTERNAL")
HOSTS = ("PRODUCER_CONTROLLED", "EXTERNAL")
LEVELS = ("L0_SELF_TEST", "L1_REPRODUCTION", "L2_SECOND_IMPLEMENTATION",
          "L3_INDEPENDENT_RECOMPUTATION", "L4_INDEPENDENT_HOST_RUN")
EVALUATOR_KINDS = ("reference", "second_implementation")
RECORDS_DIR = ROOT / "federation" / "records"
RESULTS_MD = ROOT / "docs" / "federation" / "RESULTS.md"
ADAPTER_TIMEOUT_S = 600

#: What the --run path guarantees; each key is pinned by a test in
#: tests/unit/test_federation_runner_contract.py. The Docker path in
#: deploy/scripts/run-edge.sh inherits the scoring half of these.
RUNNER_CONTRACT = {
    "fresh_working_directory": True,
    "verifier_success_exit_zero": True,
    "verifier_failure_exit_nonzero": True,
    "no_report_on_failure": True,
    "no_pass_on_failure": True,
    "existing_output_refused": True,
    "dependency_failure_exit_nonzero": True,
    "killed_verifier_nonzero_or_unknown": True,
    "malformed_result_fails_closed": True,
}

#: Nothing a green run shows, whatever the edge. Repeated in every record.
TESTBED_NON_CLAIMS = [
    "that the simulated plant behaves like any real building (synthetic, uncalibrated model)",
    "physical OT safety or a real-building saving (INV-12: no physical write path exists)",
    "that REMORA's policy is the right policy for a real site",
    "anything beyond one deployment seen from inside it (same_test_deployment vantage)",
    "endorsement of the testbed by the edge, or of the edge by the testbed",
]


# -- digests -------------------------------------------------------------------

def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def package_digest(files: list[dict[str, str]]) -> str:
    """SHA-256 over '<path> <sha256>\\n' per file, sorted by path; the manifest is never hashed into it."""
    lines = "".join(f"{f['path']} {f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return "sha256:" + sha256_bytes(lines.encode("utf-8"))


def read_manifest(fixtures: Path) -> tuple[dict[str, Any], str]:
    raw = (fixtures / "manifest.json").read_bytes()
    return json.loads(raw), sha256_bytes(raw)


def check_fixture_set(fixtures: Path) -> list[str]:
    """Verify a sealed fixture set against its manifest. Empty list means it verifies."""
    problems: list[str] = []
    mp = fixtures / "manifest.json"
    if not mp.is_file():
        return [f"{mp} does not exist"]
    try:
        manifest, _ = read_manifest(fixtures)
    except ValueError as e:
        return [f"manifest.json is not valid JSON ({e})"]
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return ["manifest has no 'files' list: the set is not sealed (python -m runner.federation_fixtures --seal <dir>)"]
    listed = {f["path"] for f in files}
    for f in files:
        p = fixtures / f["path"]
        if not p.is_file():
            problems.append(f"listed file missing: {f['path']}")
        elif sha256_file(p) != f["sha256"]:
            problems.append(f"digest mismatch: {f['path']}")
    on_disk = {str(p.relative_to(fixtures)).replace("\\", "/") for p in (fixtures / "cases").glob("*.json")}
    for extra in sorted(on_disk - listed):
        problems.append(f"file not listed in the manifest: {extra}")
    if manifest.get("package_digest") != package_digest(files):
        problems.append("package_digest does not match the listed files")
    case_ids = {c["case_id"] for c in manifest.get("cases", [])}
    for cid in sorted(case_ids):
        if f"cases/{cid}.json" not in listed:
            problems.append(f"case {cid} has no listed file")
    return problems


# -- scoring -------------------------------------------------------------------

def score(manifest: dict[str, Any], manifest_sha: str, result: Any) -> dict[str, Any]:
    problems: list[str] = []
    if not isinstance(result, dict):
        result = {}
        problems.append("result is not a JSON object")
    if result.get("schema") != "ot-edge-result/v1":
        problems.append("result schema is not ot-edge-result/v1")
    if result.get("fixtures_sha256") != manifest_sha:
        problems.append("result was not produced on this fixture set (fixtures_sha256 differs)")
    expected = {c["case_id"]: c for c in manifest["cases"]}
    seen: dict[str, dict[str, Any]] = {}
    verdicts = result.get("verdicts")
    if not isinstance(verdicts, list):
        verdicts = []
        problems.append("result has no verdicts list")
    for v in verdicts:
        if not isinstance(v, dict):
            problems.append("a verdict is not an object")
            continue
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

    def verdict_of(c: dict[str, Any]) -> str | None:
        return seen.get(c["case_id"], {}).get("verdict")

    caught = [c for c in neg if verdict_of(c) == "CONTRADICTED"]
    passed_negatives = [c for c in neg if verdict_of(c) == "ESTABLISHED"]
    established = [c for c in pos if verdict_of(c) == "ESTABLISHED"]
    if passed_negatives:
        problems.append(f"{len(passed_negatives)} negative control(s) ESTABLISHED: "
                        f"{[c['fault'] for c in passed_negatives]}")
    if not seen:
        problems.append("no verdicts")
    claims = sorted({c.get("claim_id") for c in neg if c.get("claim_id")})
    by_claim: dict[str, dict[str, int]] = {}
    for claim in claims:
        mine = [c for c in neg if c.get("claim_id") == claim]
        by_claim[claim] = {
            "negatives": len(mine),
            "caught": sum(1 for c in mine if verdict_of(c) == "CONTRADICTED"),
            "passed": sum(1 for c in mine if verdict_of(c) == "ESTABLISHED"),
        }
    cases = [{"case_id": c["case_id"], "kind": c["kind"], "fault": c.get("fault"),
              "claim_id": c.get("claim_id"), "expected_verdict": c.get("expected_verdict"),
              "verdict": verdict_of(c), "reason": seen.get(c["case_id"], {}).get("reason")}
             for c in sorted(expected.values(), key=lambda c: c["case_id"])]
    ok = not problems
    return {"ok": ok, "problems": problems,
            "positives": len(pos), "positives_established": len(established),
            "false_refusals": len(pos) - len(established),
            "negatives": len(neg), "negatives_caught": len(caught),
            "negatives_not_established": len(neg) - len(caught) - len(passed_negatives),
            "negatives_passed": len(passed_negatives),
            "by_fault": {f: sum(1 for c in caught if c["fault"] == f) for f in sorted({c["fault"] for c in neg})},
            "by_claim": by_claim,
            "claims_established": [k for k, v in by_claim.items() if ok and v["caught"] == v["negatives"] > 0],
            "cases": cases}


# -- independence --------------------------------------------------------------

def derive_level(evaluator: dict[str, Any], operator: str, host: str) -> str:
    """The level follows from what was run, by whom and where. Nobody declares it."""
    reference = evaluator.get("kind") == "reference" or bool(evaluator.get("imports_reference_verifier"))
    if reference:
        return "L0_SELF_TEST" if operator == "PRODUCER" else "L1_REPRODUCTION"
    if evaluator.get("imports_testbed_code") or operator == "PRODUCER":
        return "L2_SECOND_IMPLEMENTATION"
    return "L3_INDEPENDENT_RECOMPUTATION" if host == "PRODUCER_CONTROLLED" else "L4_INDEPENDENT_HOST_RUN"


# -- manifests -----------------------------------------------------------------

def validate_edge_manifest(edge_dir: Path) -> list[str]:
    """Validate an edge directory and its edge.json against the contract (stdlib only)."""
    problems: list[str] = []
    manifest_path = edge_dir / "edge.json"
    if not manifest_path.is_file():
        return [f"{manifest_path} does not exist"]
    try:
        edge = json.loads(manifest_path.read_text("utf-8"))
    except ValueError as e:
        return [f"{manifest_path}: invalid JSON ({e})"]
    req = ("schema", "edge_id", "project", "maintainer", "boundary", "mode",
           "pin", "licence", "claim_ceiling", "claims", "evaluator", "entrypoint")
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
    claims = edge.get("claims")
    if not isinstance(claims, list) or not claims:
        problems.append("claims must be a non-empty list of {claim_id, claim_ceiling}")
    else:
        for c in claims:
            if not isinstance(c, dict) or not c.get("claim_id") or not c.get("claim_ceiling"):
                problems.append("every claim needs claim_id and claim_ceiling")
                break
    ev = edge.get("evaluator")
    if not isinstance(ev, dict) or ev.get("kind") not in EVALUATOR_KINDS \
            or not isinstance(ev.get("imports_reference_verifier"), bool) \
            or not isinstance(ev.get("imports_testbed_code"), bool):
        problems.append("evaluator must be {kind: reference|second_implementation, "
                        "imports_reference_verifier: bool, imports_testbed_code: bool}")
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
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    edge_path = dst / "edge.json"
    edge = json.loads(edge_path.read_text("utf-8"))
    edge.update({"edge_id": edge_id, "project": project, "maintainer": maintainer,
                 "boundary": boundary, "mode": mode,
                 "repository": "https://github.com/<you>/<your-project>",
                 "pin": {"kind": "git_commit", "value": "<commit of your adapter>"},
                 "licence": "<your licence>"})
    edge_path.write_text(json.dumps(edge, indent=2) + "\n", encoding="utf-8")
    adapter_path = dst / "adapter.py"
    if adapter_path.is_file():
        txt = adapter_path.read_text("utf-8").replace(
            'EDGE_ID = "template-reference-verifier"', f'EDGE_ID = "{edge_id}"')
        adapter_path.write_text(txt, encoding="utf-8")
    return dst


# -- running an adapter locally ------------------------------------------------

def _refuse_existing_output(out: Path) -> None:
    for name in ("result.json", "report.md", "run-record.json", "score.json"):
        if (out / name).exists():
            raise FileExistsError(f"{out / name} exists: a run never overwrites an earlier one")


def run_adapter(edge_dir: Path, fixtures: Path, out: Path) -> tuple[int, str]:
    """Execute the edge's entrypoint locally with a fresh working directory. Returns (exit code, stderr tail)."""
    edge = json.loads((edge_dir / "edge.json").read_text("utf-8"))
    cmd: list[str] = []
    for arg in edge["entrypoint"]["command"]:
        if arg == "python":
            arg = sys.executable
        for prefix, target in (("/edge", edge_dir), ("/fixtures", fixtures), ("/out", out)):
            if arg == prefix or arg.startswith(prefix + "/"):
                arg = str(target.resolve()) + arg[len(prefix):]
        cmd.append(arg)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=False)
    try:
        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=ADAPTER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"adapter killed after {ADAPTER_TIMEOUT_S}s"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tail = (proc.stderr or "")[-4000:]
    if proc.returncode != 0:
        (out / "adapter-stderr.txt").write_text(tail, encoding="utf-8")
    return proc.returncode, tail


# -- records -------------------------------------------------------------------

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_record(edge: dict[str, Any], fixtures: Path, manifest: dict[str, Any], manifest_sha: str,
                 s: dict[str, Any], *, operator: str, host: str, command: str, run_ref: str | None,
                 started_at: str, completed_at: str) -> dict[str, Any]:
    level = derive_level(edge["evaluator"], operator, host)
    ceilings = [c["claim_ceiling"] for c in edge["claims"]]
    rel = str(fixtures.resolve().relative_to(ROOT)).replace("\\", "/") if fixtures.resolve().is_relative_to(ROOT) \
        else str(fixtures)
    return {
        "schema": "ot-edge-run-record/v1",
        "record_id": f"{edge['edge_id']}-{started_at.replace(':', '').replace('-', '')}",
        "edge": {k: edge[k] for k in ("edge_id", "project", "maintainer", "boundary", "mode", "pin",
                                      "licence", "evaluator", "claims") if k in edge}
        | {"repository": edge.get("repository")},
        "fixtures": {"fixture_set": manifest["fixture_set"], "path": rel,
                     "manifest_sha256": manifest_sha, "package_digest": manifest.get("package_digest"),
                     "testbed_commit": manifest.get("testbed_commit", "UNKNOWN")},
        "operator": operator, "host": host, "independence_level": level,
        "environment": f"Python {platform.python_version()} on {platform.platform()}",
        "command": command, "started_at": started_at, "completed_at": completed_at,
        "status": "PASS" if s["ok"] else "FAIL",
        "result": {k: s[k] for k in ("ok", "problems", "positives", "positives_established", "false_refusals",
                                     "negatives", "negatives_caught", "negatives_not_established",
                                     "negatives_passed", "by_fault", "by_claim", "claims_established")},
        "cases": s["cases"],
        "establishes": [f"{c}: every seeded fault for this claim was caught at level {level}"
                        for c in s["claims_established"]],
        "does_not_establish": ceilings + TESTBED_NON_CLAIMS,
        "ceiling": {"implies_endorsement": False, "implies_production_safety": False,
                    "implies_broader_validity": False, "confers_authority": False},
        "runner_contract": dict(RUNNER_CONTRACT),
        "run_ref": run_ref,
    }


def validate_record(rec: Any) -> list[str]:
    """Structural and digest checks on a run record (stdlib; the JSON Schema is the contract)."""
    p: list[str] = []
    if not isinstance(rec, dict) or rec.get("schema") != "ot-edge-run-record/v1":
        return ["not an ot-edge-run-record/v1"]
    for k in ("record_id", "edge", "fixtures", "operator", "host", "independence_level", "environment",
              "command", "started_at", "completed_at", "status", "result", "cases", "establishes",
              "does_not_establish", "ceiling", "runner_contract"):
        if k not in rec:
            p.append(f"missing {k}")
    if p:
        return p
    if rec["operator"] not in OPERATORS or rec["host"] not in HOSTS:
        p.append("operator or host outside the vocabulary")
    ev = rec["edge"].get("evaluator") or {}
    if rec["independence_level"] != derive_level(ev, rec["operator"], rec["host"]):
        p.append("independence_level is not the level derived from evaluator, operator and host")
    if any(rec["ceiling"].get(k) is not False for k in
           ("implies_endorsement", "implies_production_safety", "implies_broader_validity", "confers_authority")):
        p.append("ceiling booleans must all be false")
    if rec["status"] not in ("PASS", "FAIL") or rec["status"] != ("PASS" if rec["result"].get("ok") else "FAIL"):
        p.append("status disagrees with result.ok")
    fx = rec["fixtures"]
    fdir = ROOT / fx.get("path", "")
    if fdir.is_dir():
        manifest, sha = read_manifest(fdir)
        if sha != fx.get("manifest_sha256") or manifest.get("package_digest") != fx.get("package_digest"):
            p.append("record names a fixture set whose manifest or package digest differs from the repository's")
    else:
        p.append(f"fixture set {fx.get('path')} is not in this repository")
    if rec["status"] == "PASS" and rec["result"].get("negatives_passed", 1) != 0:
        p.append("a PASS with a negative control ESTABLISHED")
    return p


def load_records(records_dir: Path = RECORDS_DIR) -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for path in sorted(records_dir.glob("*/*.json")):
        out.append((path, json.loads(path.read_text("utf-8"))))
    return out


def render_results(records: list[tuple[Path, dict[str, Any]]]) -> str:
    lines = ["# Federation edge results", "",
             "Generated by `python -m runner.edge_check --results` from the run records under",
             "`federation/records/`. Do not edit: CI refuses a table that differs from the records.",
             "", "A row says what was checked, against which sealed fixture set, by whom and where.",
             "The independence level is derived from the edge's declared evaluator, the operator and",
             "the host (`runner.edge_check.derive_level`), never declared. No row implies endorsement,",
             "production safety or authority; every record repeats what it does not establish.",
             "Negative results and false refusals stay here.", "",
             "| Date | Edge | Boundary | Fixtures (package digest) | Testbed | Positives | Negatives caught | Status | Level | Operator / host | Run ref |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    if not records:
        lines.append("| | *no records yet* | | | | | | | | | |")
    for path, r in records:
        fx, res = r["fixtures"], r["result"]
        pd = (fx.get("package_digest") or "")[7:19]
        ref = r.get("run_ref") or f"`{path.relative_to(ROOT).as_posix()}`"
        lines.append(f"| {r['started_at'][:10]} | `{r['edge']['edge_id']}` | `{r['edge']['boundary']}` | "
                     f"`{fx['fixture_set']}` (`{pd}…`) | `{fx.get('testbed_commit', 'UNKNOWN')[:7]}` | "
                     f"{res['positives_established']} / {res['positives']} | "
                     f"{res['negatives_caught']} / {res['negatives']} | {r['status']} | "
                     f"`{r['independence_level']}` | {r['operator']} / {r['host']} | {ref} |")
    claims: dict[str, dict[str, Any]] = {}
    for _, r in records:
        for claim, v in r["result"].get("by_claim", {}).items():
            c = claims.setdefault(claim, {"records": 0, "highest": None, "disputed": 0})
            c["records"] += 1
            if r["status"] == "PASS" and v["caught"] == v["negatives"] > 0:
                lvl = r["independence_level"]
                if c["highest"] is None or LEVELS.index(lvl) > LEVELS.index(c["highest"]):
                    c["highest"] = lvl
            if v.get("passed", 0):
                c["disputed"] += 1
    lines += ["", "## By claim", "",
              "Highest independence level at which every seeded fault for the claim was caught in a",
              "passing run. `disputed` counts records where a seeded fault for the claim went uncaught.", "",
              "| Claim | Records | Highest level | Disputed |", "|---|---|---|---|"]
    for claim, c in sorted(claims.items()):
        lines.append(f"| `{claim}` | {c['records']} | `{c['highest'] or '—'}` | {c['disputed']} |")
    if not claims:
        lines.append("| *none yet* | | | |")
    lines.append("")
    return "\n".join(lines)


# -- report --------------------------------------------------------------------

def render(edge: dict[str, Any], manifest: dict[str, Any], manifest_sha: str, s: dict[str, Any],
           level: str, operator: str, host: str, command: str) -> str:
    lines = [f"# Edge report: {edge['edge_id']}", "",
             f"- Project: {edge['project']} (maintainer {edge['maintainer']})",
             f"- Boundary / mode: {edge['boundary']} / {edge['mode']}",
             f"- Edge pin: {edge['pin']['kind']} {edge['pin']['value']}",
             f"- Fixture set: {manifest['fixture_set']} (manifest sha256 {manifest_sha}; "
             f"package {manifest.get('package_digest', 'UNSEALED')})",
             f"- Testbed commit: {manifest.get('testbed_commit', 'UNKNOWN')}",
             f"- Independence level: **{level}** (operator {operator}, host {host})",
             f"- Command: `{command}`", "",
             f"**Result: {'PASS' if s['ok'] else 'FAIL'}**", "",
             "| | Count |", "|---|---|",
             f"| Positive cases ESTABLISHED | {s['positives_established']} / {s['positives']} |",
             f"| Positive cases refused (false refusals) | {s['false_refusals']} |",
             f"| Negative controls caught (CONTRADICTED) | {s['negatives_caught']} / {s['negatives']} |",
             f"| Negative controls NOT_ESTABLISHED | {s['negatives_not_established']} |",
             f"| Negative controls passed (must be 0) | {s['negatives_passed']} |", "",
             "Caught by fault: " + ", ".join(f"{k} {v}" for k, v in s["by_fault"].items()), "",
             "By claim: " + ", ".join(f"{k} {v['caught']}/{v['negatives']}" for k, v in s["by_claim"].items()), "",
             f"Claim ceiling (the edge's own words): {edge['claim_ceiling']}", ""]
    if s["problems"]:
        lines += ["Problems:", *[f"- {p}" for p in s["problems"]], ""]
    return "\n".join(lines)


def score_and_write(edge_dir: Path, fixtures: Path, result_path: Path, out: Path, *, operator: str, host: str,
                    command: str, run_ref: str | None, started_at: str) -> int:
    probs = validate_edge_manifest(edge_dir)
    if probs:
        for pr in probs:
            print(f"manifest error: {pr}", file=sys.stderr)
        return 1
    fx_probs = check_fixture_set(fixtures)
    if fx_probs:
        for pr in fx_probs:
            print(f"fixture error: {pr}", file=sys.stderr)
        return 1
    edge = json.loads((edge_dir / "edge.json").read_text("utf-8"))
    manifest, sha = read_manifest(fixtures)
    try:
        result = json.loads(result_path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        result = {"__malformed__": str(e)}
    s = score(manifest, sha, result)
    if "__malformed__" in result:
        s["problems"].insert(0, f"result is not valid JSON: {result['__malformed__']}")
        s["ok"] = False
        s["claims_established"] = []
    level = derive_level(edge["evaluator"], operator, host)
    out.mkdir(parents=True, exist_ok=True)
    rec = build_record(edge, fixtures, manifest, sha, s, operator=operator, host=host, command=command,
                       run_ref=run_ref, started_at=started_at, completed_at=_now())
    (out / "score.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    (out / "run-record.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    text = render(edge, manifest, sha, s, level, operator, host, command)
    (out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0 if s["ok"] else 1


# -- CLI -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.edge_check")
    p.add_argument("--init", metavar="EDGE_ID", help="scaffold a new edge from _template")
    p.add_argument("--project", default="My Federation Edge", help="project name for --init")
    p.add_argument("--maintainer", default="github-handle", help="GitHub handle for --init")
    p.add_argument("--boundary", default="effect_evidence", choices=list(BOUNDARIES))
    p.add_argument("--mode", default="offline", choices=list(MODES))
    p.add_argument("--validate", metavar="EDGE_DIR", help="validate an edge manifest and exit")
    p.add_argument("--check-fixtures", metavar="DIR", help="verify a sealed fixture set and exit")
    p.add_argument("--run", metavar="EDGE_DIR", help="run the edge's adapter locally, then score it")
    p.add_argument("--edge")
    p.add_argument("--fixtures")
    p.add_argument("--result")
    p.add_argument("--out")
    p.add_argument("--operator", default="PRODUCER", choices=list(OPERATORS))
    p.add_argument("--host", default="PRODUCER_CONTROLLED", choices=list(HOSTS))
    p.add_argument("--run-ref", default=None, help="URL of your published run, if any")
    p.add_argument("--command", default=None, help="what produced the result (recorded verbatim)")
    p.add_argument("--check-records", action="store_true", help="validate every record under federation/records")
    p.add_argument("--results", action="store_true", help="regenerate docs/federation/RESULTS.md from the records")
    p.add_argument("--check-results", action="store_true", help="fail if RESULTS.md differs from the records")
    a = p.parse_args(argv)

    if a.init:
        dst = init_edge(a.init, project=a.project, maintainer=a.maintainer, boundary=a.boundary, mode=a.mode)
        print(f"Created {dst}\nNext: ./deploy/scripts/run-edge.sh {a.init} --local")
        return 0
    if a.validate:
        probs = validate_edge_manifest(Path(a.validate))
        for pr in probs:
            print(f"error: {pr}", file=sys.stderr)
        if not probs:
            print(f"{a.validate}: valid ot-edge-manifest/v1")
        return 1 if probs else 0
    if a.check_fixtures:
        probs = check_fixture_set(Path(a.check_fixtures))
        for pr in probs:
            print(f"error: {pr}", file=sys.stderr)
        if not probs:
            m, sha = read_manifest(Path(a.check_fixtures))
            print(f"{a.check_fixtures}: sealed, manifest sha256 {sha}, {m['package_digest']}")
        return 1 if probs else 0
    if a.check_records or a.results or a.check_results:
        recs = load_records()
        bad = 0
        for path, rec in recs:
            for pr in validate_record(rec):
                print(f"{path.relative_to(ROOT).as_posix()}: {pr}", file=sys.stderr)
                bad += 1
        if bad:
            return 1
        text = render_results(recs)
        if a.results:
            RESULTS_MD.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {RESULTS_MD.relative_to(ROOT).as_posix()} from {len(recs)} record(s)")
        if a.check_results and RESULTS_MD.read_text("utf-8") != text:
            print("docs/federation/RESULTS.md differs from the records; run --results", file=sys.stderr)
            return 1
        if a.check_records and not a.results:
            print(f"{len(recs)} record(s) valid")
        return 0

    if a.run:
        if not (a.fixtures and a.out):
            p.error("--run needs --fixtures and --out")
        edge_dir, fixtures, out = Path(a.run), Path(a.fixtures), Path(a.out)
        probs = validate_edge_manifest(edge_dir) + check_fixture_set(fixtures)
        if probs:
            for pr in probs:
                print(f"error: {pr}", file=sys.stderr)
            return 1
        out.mkdir(parents=True, exist_ok=True)
        _refuse_existing_output(out)
        started = _now()
        rc, tail = run_adapter(edge_dir, fixtures, out)
        if rc != 0:
            print(f"adapter exited {rc}; no report written\n{tail}", file=sys.stderr)
            return 2
        command = a.command or f"python -m runner.edge_check --run {edge_dir.as_posix()} --fixtures {fixtures.as_posix()}"
        return score_and_write(edge_dir, fixtures, out / "result.json", out, operator=a.operator, host=a.host,
                               command=command, run_ref=a.run_ref, started_at=started)

    if not (a.edge and a.fixtures and a.result and a.out):
        p.error("--edge, --fixtures, --result and --out are required when scoring")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("report.md", "run-record.json", "score.json"):
        if (out / name).exists():
            print(f"error: {out / name} exists: a run never overwrites an earlier one", file=sys.stderr)
            return 1
    return score_and_write(Path(a.edge), Path(a.fixtures), Path(a.result), out, operator=a.operator, host=a.host,
                           command=a.command or "./deploy/scripts/run-edge.sh", run_ref=a.run_ref, started_at=_now())


if __name__ == "__main__":
    sys.exit(main())
