"""Usage of each offline edge across passes, per edge and per contributor.

    python -m runner.edge_usage --root artifacts [--root ...] [--out <dir>]

Collects every `usage.json` that `runner.edge_pass` wrote beside a run and
adds them up. Counting rules:

- `verifier_runs` counts every start of an edge, a repeat included; it is use,
  not value. Nothing here is an external API call: edges run locally, offline.
- `cases_processed` counts answered trails per run; `distinct_cases` counts
  each trail (case digest) once per edge version, so re-running the same
  input adds use but no new trails.
- Findings are keyed by (edge version, case digest, claim, verdict) and
  counted once however often the same input is re-run.
- `edge_only_findings` are trails an edge did not establish where the
  reference verifier did: candidates for review, not confirmed findings.
  Live trails have no independent ground truth; confirmed, unique and false
  findings come from sealed fixture sets (run records) or seeded mutations.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from runner.edge_pass import USAGE_SCHEMA


def load(roots: list[Path]) -> list[dict[str, Any]]:
    out = []
    for root in roots:
        for f in sorted(root.rglob("usage.json")):
            try:
                u = json.loads(f.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if u.get("schema") == USAGE_SCHEMA:
                out.append({**u, "_file": str(f)})
    return out


def aggregate(usages: list[dict[str, Any]]) -> dict[str, Any]:
    edges: dict[str, dict[str, Any]] = {}
    seen_findings: dict[str, set[tuple[str, str, str | None, str | None]]] = defaultdict(set)
    seen_cases: dict[str, set[tuple[str, str]]] = defaultdict(set)
    seen_candidates: dict[str, set[tuple[str, str]]] = defaultdict(set)
    passes: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for u in usages:
        for r in u["edges"]:
            eid = r["edge_id"]
            e = edges.setdefault(eid, {
                "edge_id": eid, "project": r.get("project"), "maintainer": r.get("maintainer"),
                "versions": set(), "verifier_runs": 0, "runs_ok": 0, "runs_failed": 0, "runtime_ms": 0.0,
                "cases_processed": 0, "not_established": 0})
            e["versions"].add(r["edge_digest"])
            passes[eid].add((u["run_id"], u["input_digest"]))
            e["verifier_runs"] += r.get("verifier_runs", 1)
            e["runs_ok" if r.get("run_ok") else "runs_failed"] += 1
            e["runtime_ms"] += r.get("runtime_ms") or 0.0
            e["cases_processed"] += r.get("cases_processed", 0)
            e["not_established"] += r.get("not_established", 0)
            for f in r.get("findings") or []:
                seen_findings[eid].add((r["edge_digest"], f["case_digest"], f.get("claim_id"), f.get("verdict")))
            for d in (r.get("vs_reference") or {}).get("edge_only_findings", []):
                seen_candidates[eid].add((r["edge_digest"], d))
            seen_cases[eid].update((r["edge_digest"], d) for d in r.get("answered_case_digests") or [])
    rows = []
    for eid, e in sorted(edges.items()):
        runs = e["runs_ok"] + e["runs_failed"]
        rows.append({
            "edge_id": eid, "project": e["project"], "maintainer": e["maintainer"],
            "versions": sorted(e["versions"]), "passes": len(passes[eid]),
            "verifier_runs": e["verifier_runs"], "runs_ok": e["runs_ok"], "runs_failed": e["runs_failed"],
            "availability": round(e["runs_ok"] / runs, 4) if runs else None,
            "runtime_ms": round(e["runtime_ms"], 1), "cases_processed": e["cases_processed"],
            "distinct_cases": len(seen_cases[eid]),
            "not_established": e["not_established"],
            "distinct_findings": len(seen_findings[eid]),
            "edge_only_findings_for_review": len(seen_candidates[eid]),
            "findings_confirmed": None, "unique_findings": None, "false_findings": None})
    by_contributor: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = by_contributor.setdefault(r["maintainer"] or "?", {"maintainer": r["maintainer"], "edges": [],
                                                              "verifier_runs": 0, "cases_processed": 0})
        c["edges"].append(r["edge_id"])
        c["verifier_runs"] += r["verifier_runs"]
        c["cases_processed"] += r["cases_processed"]
    return {"schema": "ot-edge-usage-summary/v1", "usage_files": len(usages), "edges": rows,
            "by_contributor": sorted(by_contributor.values(), key=lambda c: str(c["maintainer"])),
            "note": "Use is not value: verifier_runs and cases_processed count work done, and an edge run is "
                    "local and offline, not an API call. Findings are counted once per edge version and input. "
                    "Confirmed, unique and false findings need a ground truth and are null here."}


def render_md(s: dict[str, Any]) -> str:
    L = ["## Edge usage across passes", "",
         f"{s['usage_files']} pass(es). {s['note']}", "",
         "| Edge | Maintainer | Versions | Passes | Verifier runs | Available | Runtime ms | Cases processed | "
         "Distinct cases | Not established | Distinct findings | Edge-only, for review |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in s["edges"]:
        av = "—" if r["availability"] is None else f"{r['availability']:.0%}"
        L.append(f"| `{r['edge_id']}` | {r['maintainer']} | {len(r['versions'])} | {r['passes']} | "
                 f"{r['verifier_runs']} | {av} | {r['runtime_ms']} | {r['cases_processed']} | {r['distinct_cases']} | "
                 f"{r['not_established']} | {r['distinct_findings']} | {r['edge_only_findings_for_review']} |")
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.edge_usage")
    p.add_argument("--root", action="append", required=True, help="directory searched for usage.json (repeatable)")
    p.add_argument("--out", default=None, help="write usage-summary.json and usage-summary.md here")
    a = p.parse_args(argv)
    s = aggregate(load([Path(r) for r in a.root]))
    md = render_md(s)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "usage-summary.json").write_text(json.dumps(s, indent=2) + "\n", "utf-8")
        (out / "usage-summary.md").write_text(md, "utf-8")
    print(md)
    return 0 if s["usage_files"] else 1


if __name__ == "__main__":
    sys.exit(main())
