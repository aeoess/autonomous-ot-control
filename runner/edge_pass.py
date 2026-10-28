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
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from runner.edge_check import ROOT, run_adapter, sha256_bytes
from runner.federation_fixtures import CLAIMS, canonical_sha256

EDGES_DIR = ROOT / "federation" / "edges"
TEMPLATE = EDGES_DIR / "_template"


INDEPENDENT_VANTAGE_PREFIX = "independent"


def _capture_rows(work: Path) -> dict[str, dict[str, Any]]:
    """Capture digests from the ledger (ledger.jsonl), a separate file from the trails, by proposal id."""
    path = work / "ledger.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("proposal_id") and isinstance(r.get("capture"), dict):
                    rows[r["proposal_id"]] = r["capture"]
    return rows


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _execution_results(evidence: Any, pid: str) -> tuple[int | None, str]:
    """REMORA's own count of execution_result events for the proposal; None when the lifecycle is incomplete."""
    lc = evidence.get("lifecycle") if isinstance(evidence, dict) else None
    events = lc.get("events") if isinstance(lc, dict) else None
    if not isinstance(events, list):
        return None, "missing: no REMORA lifecycle in the exported evidence"
    declared = (evidence.get("manifest") or {}).get("event_count")
    if declared is not None and declared != len(events):
        return None, f"missing: lifecycle holds {len(events)} of {declared} events"
    n = sum(1 for e in events if isinstance(e, dict) and e.get("event") == "execution_result"
            and (e.get("payload") or {}).get("proposal_id", pid) == pid)
    return n, f"REMORA lifecycle: {n} execution_result event(s)"


def _remora_readback(evidence: Any, pid: str) -> dict[str, Any] | None:
    """The last effect observation REMORA recorded for the proposal (effect_verification history)."""
    ev = evidence.get("effect_verification") if isinstance(evidence, dict) else None
    hist = ev.get("history") if isinstance(ev, dict) else None
    found = [h for h in hist or [] if isinstance(h, dict) and h.get("proposal_id") == pid
             and isinstance(h.get("observed"), dict) and _is_num(h["observed"].get("setpoint_c"))]
    return found[-1] if found else None


def _observer_record(rec: dict[str, Any], capture: dict[str, Any] | None, pid: str,
                     findings: list[dict[str, str]]) -> dict[str, Any] | None:
    """The observer's own sealed record, used only when its seal and the ledger's capture digest hold."""
    eo = rec.get("effect_observation")
    if not isinstance(eo, dict):
        return None
    body = {k: v for k, v in eo.items() if k != "observation_digest"}
    if eo.get("observation_digest") != "sha256:" + canonical_sha256(body):
        findings.append({"claim_id": "observation_after_dispatch", "verdict": "CONTRADICTED",
                         "reason": "the observer record does not match its own seal"})
        return None
    want = (capture or {}).get("effect_observation_sha256")
    if want is not None and want != canonical_sha256(eo):
        findings.append({"claim_id": "observation_after_dispatch", "verdict": "CONTRADICTED",
                         "reason": "the observer record differs from the digest captured in the ledger"})
        return None
    if eo.get("operation_id") != pid:
        findings.append({"claim_id": "observation_after_dispatch", "verdict": "CONTRADICTED",
                         "reason": "the observer record belongs to another operation"})
        return None
    return eo


def _evidence_digest(evidence: Any, capture: dict[str, Any] | None,
                     findings: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    """evidence_sha256 for the case, and where it comes from.

    Preferred: the digest the harness wrote into the ledger when it exported the
    trail. Otherwise REMORA's own export manifest (one SHA-256 per section,
    recorded by REMORA at export): when every section still matches, the
    evidence is the one REMORA exported. With neither, the digest can only be
    recomputed from the evidence itself, which proves nothing: the claim then
    counts as NOT_ESTABLISHED whatever an edge answers. REMORA's observed_sha256
    is a digest of one observed value, not of the evidence, and is not used here.
    """
    whole = canonical_sha256(evidence)
    cap = (capture or {}).get("evidence_sha256")
    if cap:
        return cap, {"source": "ledger capture digest", "recorded": True}
    sections = (evidence.get("manifest") or {}).get("section_sha256") if isinstance(evidence, dict) else None
    if not isinstance(sections, dict) or not sections:
        return whole, {"source": "missing: no capture digest and no REMORA export manifest", "recorded": False}
    bad = sorted(n for n, d in sections.items() if n not in evidence or canonical_sha256(evidence[n]) != d)
    if bad:
        findings.append({"claim_id": "evidence_digest_integrity", "verdict": "CONTRADICTED",
                         "reason": f"section(s) {', '.join(bad)} differ from REMORA's export manifest"})
        return whole, {"source": "REMORA export manifest (mismatch)", "recorded": False}
    uncovered = sorted(set(evidence) - set(sections) - {"manifest"})
    if uncovered:
        return whole, {"source": f"missing: section(s) {', '.join(uncovered)} not in REMORA's export manifest",
                       "recorded": False}
    return whole, {"source": "REMORA export manifest (per-section SHA-256)", "recorded": True}


def trail_case(rec: dict[str, Any], capture: dict[str, Any] | None = None
               ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, str]]]:
    """One applied setpoint trail as an ot-federation-case/v1, with the source of every claim's input.

    Nothing an edge checks is made up here: each member comes from a record
    written when it happened (the ledger row, REMORA's lifecycle and effect
    verification, the twin's operation record, the observer's sealed record).
    A member with no such source is left out, and the case contract then makes
    a verifier answer NOT_ESTABLISHED for that claim
    (contracts/federation-case-v1.schema.json). Returns the case, the source
    per claim, and input findings (a record that contradicts itself or its
    capture digest).
    """
    row = rec["ledger_row"]
    pid = rec["proposal_id"]
    op = rec.get("twin_operation")
    evidence = rec.get("evidence")
    findings: list[dict[str, str]] = []
    basis: dict[str, dict[str, Any]] = {
        "decision_correspondence": {"source": "ledger row: REMORA decision and reviewer", "recorded": True},
        "call_binding": ({"source": "twin operation record", "recorded": True} if op
                         else {"source": "missing: no twin operation record exported", "recorded": False})}

    n, n_src = _execution_results(evidence, pid)
    execution: dict[str, Any] = {"dispatched_at_sim_s": row.get("sim_time_s")}
    if n is not None:
        execution.update(executed=n > 0, executions=n)
    else:
        execution["executed"] = bool(row.get("applied")) and \
            (row.get("execute") or {}).get("twin_status", "APPLIED") == "APPLIED"
    basis["single_dispatch"] = {"source": n_src, "recorded": n is not None}

    readback = _remora_readback(evidence, pid)
    observation: dict[str, Any] | None = None
    if readback is not None:
        observation = {"observed_setpoint_c": readback["observed"]["setpoint_c"]}
        basis["declared_effect_observed"] = {
            "source": f"REMORA effect_verification readback (vantage {readback.get('vantage') or 'unrecorded'})",
            "recorded": True}
    else:
        basis["declared_effect_observed"] = {"source": "missing: no readback in REMORA's effect verification",
                                             "recorded": False}
    eo = _observer_record(rec, capture, pid, findings)
    if observation is not None and eo is not None and _is_num(eo.get("sim_time_s")):
        observation["observed_at_sim_s"] = int(eo["sim_time_s"])
        if _is_num(eo.get("observed_setpoint_c")) and eo["observed_setpoint_c"] != observation["observed_setpoint_c"]:
            findings.append({"claim_id": "declared_effect_observed", "verdict": "CONTRADICTED",
                             "reason": "the observer's record and REMORA's readback disagree"})
        basis["observation_after_dispatch"] = {"source": "observer record sim_time_s (sealed)", "recorded": True}
    else:
        basis["observation_after_dispatch"] = {"source": "missing: no sealed observer record with a simulated time",
                                               "recorded": False}

    digest, basis["evidence_digest_integrity"] = _evidence_digest(evidence, capture, findings)
    case = {
        "schema": "ot-federation-case/v1", "case_id": f"live-{pid}", "kind": "positive", "fault": None,
        "claim": {"proposal_id": pid, "tool": "set_zone_setpoint", "zone_id": row["zone_id"],
                  "setpoint_c": row["to_c"], "claimed_effect": "EFFECT_VERIFIED"},
        "decision": {"decision": row.get("decision"), "reasons": row.get("reasons") or [],
                     "approved": bool((row.get("reviewer") or {}).get("approved")),
                     "reviewer": row.get("reviewer")},
        "execution": execution,
        "twin_operation": op,
        "observation": observation,
        "remora_evidence": evidence, "remora_envelope": rec.get("envelope"),
        "evidence_sha256": digest,
    }
    return case, basis, findings


def vantage_of(case: dict[str, Any]) -> str:
    env = case.get("remora_envelope") or {}
    return ((env.get("effect") or {}).get("ledger_entry") or {}).get("effect_vantage") or "unrecorded"


def counted_verdict(edge_verdict: str, basis: dict[str, dict[str, Any]], findings: list[dict[str, str]]) -> str:
    """The verdict the pass counts: an edge's ESTABLISHED stands only on recorded inputs."""
    if any(f["verdict"] == "CONTRADICTED" for f in findings):
        return "CONTRADICTED"
    if edge_verdict == "ESTABLISHED" and not all(b["recorded"] for b in basis.values()):
        return "NOT_ESTABLISHED"
    return edge_verdict


def strict_verdict(counted: str, vantage: str) -> str:
    """Strict vantage: an observation counts only when its recorded vantage is independent."""
    if counted == "ESTABLISHED" and not vantage.startswith(INDEPENDENT_VANTAGE_PREFIX):
        return "NOT_ESTABLISHED"
    return counted


def build_cases(work: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    cases, _, skipped = build_cases_with_inputs(work)
    return cases, skipped


def build_cases_with_inputs(work: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], Counter[str]]:
    cases: list[dict[str, Any]] = []
    inputs: dict[str, dict[str, Any]] = {}
    skipped: Counter[str] = Counter()
    captures = _capture_rows(work)
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
        case, basis, findings = trail_case(rec, captures.get(rec["proposal_id"]))
        cases.append(case)
        inputs[case["case_id"]] = {"basis": basis, "input_findings": findings, "vantage": vantage_of(case)}
    return cases, inputs, skipped


def write_case_set(cases: list[dict[str, Any]], run_id: str, dst: Path) -> str:
    """A fixture-shaped directory for the adapters. Not sealed: these are live trails, not a corpus."""
    (dst / "cases").mkdir(parents=True, exist_ok=True)
    for c in cases:
        (dst / "cases" / f"{c['case_id']}.json").write_text(json.dumps(c, indent=2, sort_keys=True) + "\n", "utf-8", newline="\n")
    manifest = {"schema": "ot-federation-fixtures/v1", "fixture_set": f"{run_id}-live", "kind": "live_run",
                "scope": "Applied set_zone_setpoint trails of one governed run, with the twin's exported "
                         "operation record. Every member comes from a record written when it happened; a member "
                         "with no such record is left out, so the claim it serves is NOT_ESTABLISHED. Expected "
                         "ESTABLISHED; any other verdict is a finding about the run, its records or the edge.",
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


REFERENCE_EDGE_ID = "template-reference-verifier"
USAGE_SCHEMA = "ot-edge-usage/v1"


def edge_digest(edge_dir: Path) -> str:
    """SHA-256 over the edge's files (path and bytes): the version a usage row is tied to."""
    h = hashlib.sha256()
    for f in sorted(x for x in edge_dir.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        h.update(f.relative_to(edge_dir).as_posix().encode("utf-8") + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def usage_record(run_id: str, manifest_sha: str, cases: list[dict[str, Any]], inputs: dict[str, dict[str, Any]],
                 runs: list[dict[str, Any]]) -> dict[str, Any]:
    """What each edge was asked and did in this pass, tied to edge version, input digest and run id.

    A verifier run is one start of the edge's entrypoint over the whole case set,
    not one call per trail, and nothing here is an external API call: the edges
    run locally and offline. Live trails have no independent ground truth (they
    are expected positive), so findings are counted against the reference
    verifier only as agreement or as candidates for review; confirmed, unique
    and false findings need a ground truth (sealed fixture sets, whose run
    records already count them, or seeded mutations) and stay null here.
    """
    case_digest = {c["case_id"]: canonical_sha256(c) for c in cases}
    ref = next((r for r in runs if r["edge_id"] == REFERENCE_EDGE_ID and r["verdicts"] is not None), None)
    rows = []
    for r in runs:
        v = r["verdicts"]
        row: dict[str, Any] = {
            "edge_id": r["edge_id"], "project": r.get("project"), "maintainer": r.get("maintainer"),
            "pin": r.get("pin"), "edge_digest": r["edge_digest"],
            "verifier_runs": 1, "run_ok": v is not None, "exit": r.get("exit"), "runtime_ms": r["runtime_ms"],
            "cases_sent": len(cases), "cases_processed": 0 if v is None else len(set(v) & set(case_digest)),
        }
        if v is not None:
            edge_counts = Counter(x.get("verdict", "?") for x in v.values())
            recorded = Counter(counted_verdict(x.get("verdict", "?"), inputs[c]["basis"], inputs[c]["input_findings"])
                               for c, x in v.items() if c in inputs)
            row.update({
                "answered_case_digests": sorted(case_digest[c] for c in set(v) & set(case_digest)),
                "verdicts": dict(edge_counts), "verdicts_on_recorded_inputs": dict(recorded),
                "not_established": edge_counts.get("NOT_ESTABLISHED", 0),
                "findings": [{"case_id": c, "case_digest": case_digest[c], "verdict": x.get("verdict"),
                              "claim_id": x.get("claim_id")}
                             for c, x in sorted(v.items()) if c in case_digest and x.get("verdict") != "ESTABLISHED"],
            })
            if ref is not None and r is not ref:
                both = set(v) & set(ref["verdicts"])
                same = sum(1 for c in both if v[c].get("verdict") == ref["verdicts"][c].get("verdict")
                           and v[c].get("claim_id") == ref["verdicts"][c].get("claim_id"))
                row["vs_reference"] = {
                    "compared": len(both), "agree": same,
                    "edge_only_findings": sorted(case_digest[c] for c in both
                                                 if v[c].get("verdict") != "ESTABLISHED"
                                                 and ref["verdicts"][c].get("verdict") == "ESTABLISHED"),
                    "reference_only_findings": sorted(case_digest[c] for c in both
                                                      if v[c].get("verdict") == "ESTABLISHED"
                                                      and ref["verdicts"][c].get("verdict") != "ESTABLISHED"),
                }
        row.update({"findings_confirmed": None, "unique_findings": None, "false_findings": None})
        rows.append(row)
    return {"schema": USAGE_SCHEMA, "run_id": run_id, "input_digest": manifest_sha, "cases": len(cases),
            "ground_truth": "none: live trails, expected positive; confirmed, unique and false findings need a "
                            "ground truth and are null (sealed fixture sets are counted in their run records)",
            "reference_edge": REFERENCE_EDGE_ID, "edges": rows}


def run_pass(work: Path, out: Path, edges: list[Path], run_id: str) -> dict[str, Any]:
    cases, inputs, skipped = build_cases_with_inputs(work)
    case_dir = out / "cases"
    if case_dir.exists():
        shutil.rmtree(case_dir)
    manifest_sha = write_case_set(cases, run_id, case_dir)
    expected = {c["case_id"] for c in cases}
    results = []
    runs: list[dict[str, Any]] = []
    for edge_dir in edges:
        edge = json.loads((edge_dir / "edge.json").read_text("utf-8"))
        run = {"edge_id": edge["edge_id"], "project": edge.get("project"), "maintainer": edge.get("maintainer"),
               "pin": (edge.get("pin") or {}).get("value"), "edge_digest": edge_digest(edge_dir), "verdicts": None}
        runs.append(run)
        edge_out = out / "by-edge" / edge["edge_id"]
        if edge_out.exists():
            shutil.rmtree(edge_out)
        edge_out.mkdir(parents=True)
        entry: dict[str, Any] = {"edge_id": edge["edge_id"], "project": edge.get("project"),
                                 "evaluator": edge.get("evaluator"), "available": False}
        t0 = time.perf_counter()
        rc, tail = run_adapter(edge_dir, case_dir, edge_out)
        run["runtime_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        run["exit"] = rc
        if rc != 0:
            entry.update({"exit": rc, "stderr": tail[-600:]})
            results.append(entry)
            continue
        try:
            result = json.loads((edge_out / "result.json").read_text("utf-8"))
            verdicts = {v["case_id"]: v for v in result["verdicts"]}
            run["verdicts"] = verdicts
        except (OSError, ValueError, KeyError, TypeError) as e:
            entry.update({"exit": 0, "stderr": f"malformed result: {e}"})
            results.append(entry)
            continue
        counts = Counter(v.get("verdict", "?") for v in verdicts.values())
        counted: Counter[str] = Counter()
        strict: Counter[str] = Counter()
        for cid, v in verdicts.items():
            i = inputs.get(cid)
            if i is None:
                continue
            c = counted_verdict(v.get("verdict", "?"), i["basis"], i["input_findings"])
            counted[c] += 1
            strict[strict_verdict(c, i["vantage"])] += 1
        findings = [{"case_id": cid, "proposal_id": cid.removeprefix("live-"), "verdict": v.get("verdict"),
                     "reason": v.get("reason"), "claim_id": v.get("claim_id")}
                    for cid, v in sorted(verdicts.items()) if v.get("verdict") != "ESTABLISHED"]
        entry.update({"available": True, "fixtures_sha256_matches": result.get("fixtures_sha256") == manifest_sha,
                      "answered": len(expected & set(verdicts)), "unanswered": sorted(expected - set(verdicts)),
                      "counts": dict(counts), "counts_on_recorded_inputs": dict(counted),
                      "counts_strict_vantage": dict(strict), "findings": findings})
        results.append(entry)
    sources: dict[str, Counter[str]] = {}
    for i in inputs.values():
        for claim, b in i["basis"].items():
            sources.setdefault(claim, Counter())[b["source"]] += 1
    input_findings = [{"case_id": cid, "proposal_id": cid.removeprefix("live-"), **f}
                      for cid, i in sorted(inputs.items()) for f in i["input_findings"]]
    summary = {"schema": "ot-edge-pass/v1", "run_id": run_id, "work": str(work), "cases": len(cases),
               "skipped": dict(skipped), "case_set_manifest_sha256": manifest_sha, "edges": results,
               "inputs": {"by_claim": {k: dict(v) for k, v in sorted(sources.items())},
                          "vantages": dict(Counter(i["vantage"] for i in inputs.values())),
                          "findings": input_findings},
               "note": "Offline edges beside the run (ADR-004): their verdicts never change the run's outcome. "
                       "counts are the edges' own verdicts; counts_on_recorded_inputs counts an ESTABLISHED "
                       "only when every claim's input was recorded when it happened, and an input that "
                       "contradicts its record as CONTRADICTED; counts_strict_vantage also requires an "
                       "independent observation vantage. A non-ESTABLISHED verdict on a live trail is a "
                       "finding about the run, its records or the edge."}
    usage = usage_record(run_id, manifest_sha, cases, inputs, runs)
    (out / "usage.json").write_text(json.dumps(usage, indent=2) + "\n", "utf-8", newline="\n")
    summary["usage"] = "usage.json"
    (out / "edges.json").write_text(json.dumps(summary, indent=2) + "\n", "utf-8", newline="\n")
    (out / "edges.md").write_text(render_md(summary), "utf-8", newline="\n")
    return summary


def ecn_cell(c: dict[str, int]) -> str:
    return f"{c.get('ESTABLISHED', 0)} / {c.get('CONTRADICTED', 0)} / {c.get('NOT_ESTABLISHED', 0)}"


def render_md(s: dict[str, Any]) -> str:
    L = ["## Edges beside the run", "",
         f"{s['cases']} applied `set_zone_setpoint` trails handed to every offline `effect_evidence` edge; "
         f"skipped: " + (", ".join(f"{k} {v}" for k, v in sorted(s["skipped"].items())) or "none") + ". "
         "Verdicts never change the run's outcome. *Edge* is each edge's own answer; *recorded inputs* "
         "counts an ESTABLISHED only when every claim rests on a record written when it happened; "
         "*strict vantage* also requires an independent observation.", "",
         "| Edge | Evaluator | Ran | Edge E / C / N | Recorded inputs E / C / N | Strict vantage E / C / N | Unanswered |",
         "|---|---|---|---|---|---|---:|"]
    for e in s["edges"]:
        ev = e.get("evaluator") or {}
        kind = ev.get("kind", "?")
        if not e.get("available"):
            L.append(f"| `{e['edge_id']}` | {kind} | no (exit {e.get('exit')}) | | | | |")
            continue
        L.append(f"| `{e['edge_id']}` | {kind} | yes | {ecn_cell(e['counts'])} | {ecn_cell(e['counts_on_recorded_inputs'])} | "
                 f"{ecn_cell(e['counts_strict_vantage'])} | {len(e['unanswered'])} |")
    inp = s.get("inputs") or {}
    if inp.get("by_claim"):
        L += ["", "Where each claim's input comes from (trails per source):", "", "| Claim | Source | Trails |",
              "|---|---|---:|"]
        L += [f"| `{claim}` | {src} | {n} |" for claim, srcs in inp["by_claim"].items()
              for src, n in sorted(srcs.items())]
        L += ["", "Observation vantage recorded: " + ", ".join(f"`{k}` {v}" for k, v in sorted(inp["vantages"].items()))]
    if inp.get("findings"):
        L += ["", "Input findings (a record that contradicts itself or its capture digest):", "",
              "| Proposal | Claim | Reason |", "|---|---|---|"]
        L += [f"| `{f['proposal_id'][:8]}` | `{f['claim_id']}` | {f['reason']} |" for f in inp["findings"]]
    findings = [(e["edge_id"], f) for e in s["edges"] if e.get("available") for f in e["findings"]]
    if findings:
        L += ["", "Findings (every verdict other than ESTABLISHED, one per edge and trail):", "",
              "| Edge | Proposal | Verdict | Claim | Reason |", "|---|---|---|---|---|"]
        L += [f"| `{eid}` | `{f['proposal_id'][:8]}` | {f['verdict']} | `{f.get('claim_id') or ''}` | {f.get('reason') or ''} |"
              for eid, f in findings]
    else:
        L += ["", "No edge findings: every available edge established every trail."]
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.edge_pass")
    p.add_argument("--work", required=True, help="the demo's work directory (contains decisions/)")
    p.add_argument("--out", default=None, help="output directory (default: <work>/edges)")
    p.add_argument("--run-id", default=None)
    p.add_argument("--edge", action="append", default=None,
                   help="edge directory to run (repeatable); default: every offline effect_evidence edge")
    p.add_argument("--trust-edge", action="append", default=[], metavar="EDGE_ID",
                   help="run this edge on the host instead of the container sandbox (your own code only)")
    a = p.parse_args(argv)
    from runner import edge_sandbox
    edge_sandbox.EXTRA_TRUSTED.update(a.trust_edge)
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
