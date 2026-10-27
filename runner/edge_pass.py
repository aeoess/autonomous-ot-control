"""Run every offline effect-evidence edge over a governed run's trails, beside the run.

    python -m runner.edge_pass --work artifacts/<run-id>-work [--out <dir>] [--edge <dir> ...]

ADR-004: an offline edge never sees the live loop. It consumes the run's
exported evidence and returns its own verdicts, which are reported beside the
run and never change its outcome. This is that pass. It turns each applied
`set_zone_setpoint` trail the demo exported (`decisions/<proposal_id>.json`,
with the twin's own operation record) into a federation case, hands the whole
set to every offline `effect_evidence` edge under `federation/edges/` (the
reference template always included), and writes:

- `edges.json`: per edge, availability, counts by verdict, and every case the
  edge did not establish, with its reason and claim;
- `edges.md`: the same as a section a report can include.

A CONTRADICTED or NOT_ESTABLISHED verdict on a live trail is a finding about
the run or about the edge, recorded as such; the pass itself passes whenever
at least one edge ran. An edge that cannot run here (missing dependency, no
image) is reported as unavailable, never as a verdict. Trails of other tools
and refused proposals are counted and left out: the effect_evidence contract
covers applied zone setpoints only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from runner.edge_check import ROOT, run_adapter, sha256_bytes
from runner.federation_fixtures import CLAIMS, canonical_sha256

EDGES_DIR = ROOT / "federation" / "edges"
TEMPLATE = EDGES_DIR / "_template"


def trail_case(rec: dict[str, Any]) -> dict[str, Any]:
    """One applied setpoint trail as an ot-federation-case/v1, twin record or not."""
    row = rec["ledger_row"]
    pid = rec["proposal_id"]
    op = rec.get("twin_operation")
    evidence = rec.get("evidence")
    return {
        "schema": "ot-federation-case/v1", "case_id": f"live-{pid}", "kind": "positive", "fault": None,
        "claim": {"proposal_id": pid, "tool": "set_zone_setpoint", "zone_id": row["zone_id"],
                  "setpoint_c": row["to_c"], "claimed_effect": "EFFECT_VERIFIED"},
        "decision": {"decision": row.get("decision"), "reasons": row.get("reasons") or [],
                     "approved": bool((row.get("reviewer") or {}).get("approved")),
                     "reviewer": row.get("reviewer")},
        "execution": {"executed": True, "dispatched_at_sim_s": row.get("sim_time_s"), "executions": 1},
        "twin_operation": op,
        "observation": ({"observed_setpoint_c": op["new_setpoint_c"], "observed_at_sim_s": int(op["sim_time_s"])}
                        if op else None),
        "remora_evidence": evidence, "remora_envelope": rec.get("envelope"),
        "evidence_sha256": canonical_sha256(evidence),
    }


def build_cases(work: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    cases: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for path in sorted((work / "decisions").glob("*.json")):
        rec = json.loads(path.read_text("utf-8"))
        row = rec.get("ledger_row") or {}
        if not row.get("applied"):
            skipped["not_applied"] += 1
            continue
        if row.get("tool_name", "set_zone_setpoint") != "set_zone_setpoint" or "zone_id" not in row or "to_c" not in row:
            skipped["other_tool"] += 1
            continue
        if rec.get("twin_operation") is None:
            skipped["no_twin_operation_exported"] += 1  # still a case: the edge says NOT_ESTABLISHED
        cases.append(trail_case(rec))
    return cases, skipped


def write_case_set(cases: list[dict[str, Any]], run_id: str, dst: Path) -> str:
    """A fixture-shaped directory for the adapters. Not sealed: these are live trails, not a corpus."""
    (dst / "cases").mkdir(parents=True, exist_ok=True)
    for c in cases:
        (dst / "cases" / f"{c['case_id']}.json").write_text(json.dumps(c, indent=2, sort_keys=True) + "\n", "utf-8")
    manifest = {"schema": "ot-federation-fixtures/v1", "fixture_set": f"{run_id}-live", "kind": "live_run",
                "scope": "Applied set_zone_setpoint trails of one governed run, with the twin's exported "
                         "operation record. Positive by construction; any other verdict is a finding.",
                "claims": [{"claim_id": k, "statement": v} for k, v in CLAIMS.items()],
                "cases": [{"case_id": c["case_id"], "kind": "positive", "fault": None, "claim_id": None,
                           "expected_verdict": "ESTABLISHED"} for c in cases]}
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (dst / "manifest.json").write_bytes(raw)
    return sha256_bytes(raw)


def offline_effect_edges() -> list[Path]:
    """Every registered offline effect_evidence edge with a runnable entrypoint, template first."""
    out = [TEMPLATE]
    for d in sorted(EDGES_DIR.iterdir()):
        if d.name.startswith("_") or not (d / "edge.json").is_file():
            continue
        e = json.loads((d / "edge.json").read_text("utf-8"))
        if e.get("mode") == "offline" and e.get("boundary") == "effect_evidence" \
                and (e.get("entrypoint") or {}).get("command"):
            out.append(d)
    return out


def run_pass(work: Path, out: Path, edges: list[Path], run_id: str) -> dict[str, Any]:
    cases, skipped = build_cases(work)
    case_dir = out / "cases"
    if case_dir.exists():
        shutil.rmtree(case_dir)
    manifest_sha = write_case_set(cases, run_id, case_dir)
    expected = {c["case_id"] for c in cases}
    results = []
    for edge_dir in edges:
        edge = json.loads((edge_dir / "edge.json").read_text("utf-8"))
        edge_out = out / "by-edge" / edge["edge_id"]
        if edge_out.exists():
            shutil.rmtree(edge_out)
        edge_out.mkdir(parents=True)
        entry: dict[str, Any] = {"edge_id": edge["edge_id"], "project": edge.get("project"),
                                 "evaluator": edge.get("evaluator"), "available": False}
        rc, tail = run_adapter(edge_dir, case_dir, edge_out)
        if rc != 0:
            entry.update({"exit": rc, "stderr": tail[-600:]})
            results.append(entry)
            continue
        try:
            result = json.loads((edge_out / "result.json").read_text("utf-8"))
            verdicts = {v["case_id"]: v for v in result["verdicts"]}
        except (OSError, ValueError, KeyError, TypeError) as e:
            entry.update({"exit": 0, "stderr": f"malformed result: {e}"})
            results.append(entry)
            continue
        counts = Counter(v.get("verdict", "?") for v in verdicts.values())
        findings = [{"case_id": cid, "proposal_id": cid.removeprefix("live-"), "verdict": v.get("verdict"),
                     "reason": v.get("reason"), "claim_id": v.get("claim_id")}
                    for cid, v in sorted(verdicts.items()) if v.get("verdict") != "ESTABLISHED"]
        entry.update({"available": True, "fixtures_sha256_matches": result.get("fixtures_sha256") == manifest_sha,
                      "answered": len(expected & set(verdicts)), "unanswered": sorted(expected - set(verdicts)),
                      "counts": dict(counts), "findings": findings})
        results.append(entry)
    summary = {"schema": "ot-edge-pass/v1", "run_id": run_id, "work": str(work), "cases": len(cases),
               "skipped": dict(skipped), "case_set_manifest_sha256": manifest_sha, "edges": results,
               "note": "Offline edges beside the run (ADR-004): their verdicts never change the run's outcome. "
                       "A non-ESTABLISHED verdict on a live trail is a finding about the run or the edge."}
    (out / "edges.json").write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
    (out / "edges.md").write_text(render_md(summary), "utf-8")
    return summary


def render_md(s: dict[str, Any]) -> str:
    L = ["## Edges beside the run", "",
         f"{s['cases']} applied `set_zone_setpoint` trails handed to every offline `effect_evidence` edge; "
         f"skipped: " + (", ".join(f"{k} {v}" for k, v in sorted(s["skipped"].items())) or "none") + ". "
         "Verdicts are the edges' own and never change the run's outcome.", "",
         "| Edge | Evaluator | Ran | Established | Contradicted | Not established | Unanswered |",
         "|---|---|---|---:|---:|---:|---:|"]
    for e in s["edges"]:
        ev = e.get("evaluator") or {}
        kind = ev.get("kind", "?")
        if not e.get("available"):
            L.append(f"| `{e['edge_id']}` | {kind} | no (exit {e.get('exit')}) | | | | |")
            continue
        c = e["counts"]
        L.append(f"| `{e['edge_id']}` | {kind} | yes | {c.get('ESTABLISHED', 0)} | {c.get('CONTRADICTED', 0)} | "
                 f"{c.get('NOT_ESTABLISHED', 0)} | {len(e['unanswered'])} |")
    findings = [(e["edge_id"], f) for e in s["edges"] if e.get("available") for f in e["findings"]]
    if findings:
        L += ["", "Findings (every verdict other than ESTABLISHED, one per edge and trail):", "",
              "| Edge | Proposal | Verdict | Claim | Reason |", "|---|---|---|---|---|"]
        L += [f"| `{eid}` | `{f['proposal_id'][:8]}` | {f['verdict']} | `{f.get('claim_id') or ''}` | {f.get('reason') or ''} |"
              for eid, f in findings]
    else:
        L += ["", "No findings: every available edge established every trail."]
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.edge_pass")
    p.add_argument("--work", required=True, help="the demo's work directory (contains decisions/)")
    p.add_argument("--out", default=None, help="output directory (default: <work>/edges)")
    p.add_argument("--run-id", default=None)
    p.add_argument("--edge", action="append", default=None,
                   help="edge directory to run (repeatable); default: every offline effect_evidence edge")
    a = p.parse_args(argv)
    work = Path(a.work)
    if not (work / "decisions").is_dir():
        print(f"error: {work}/decisions not found", file=sys.stderr)
        return 2
    out = Path(a.out) if a.out else work / "edges"
    out.mkdir(parents=True, exist_ok=True)
    edges = [Path(e) for e in a.edge] if a.edge else offline_effect_edges()
    s = run_pass(work, out, edges, a.run_id or work.name)
    print(render_md(s))
    if not any(e.get("available") for e in s["edges"]):
        print("error: no edge could run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
