"""Offline authority_fixture harness: both checks on the same captured input, both verdicts recorded.

    python -m runner.authority_harness --fixtures federation/authority/fixtures/t2 \
        --verifier federation/authority/template-verifier --out <dir>

For every case of a sealed set (runner.authority_fixtures):

- the **delegation check** is the verifier's (APS's, or the stand-in), run
  once over the set as a subprocess with the scorer's timeout. Its answer per
  case is ``pass`` or ``refuse`` with a reason. Anything else is recorded in
  its own class, never as a refusal and never as a catch: ``error`` (the
  process failed), ``timeout``, ``malformed`` (an answer outside pass/refuse)
  or ``unanswered``;
- the **work-order check** is the testbed's (runner.work_order.check) on the
  same normalized call, work order and delegation;
- **admitted** means both passed. Neither check can admit on its own;
- the **oracle** judges the same call from how the case was built
  (``delegation_fault``) and the work order's range, without reading either
  verdict. Its permitted/forbidden must agree with the expected admission.

Each column is compared with the expectation the manifest fixed before any
verifier ran. The run passes only when the set is sealed and unchanged, the
oracle agrees with the manifest, and both checks give the expected verdict on
every case.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from runner import authority_fixtures as af
from runner import work_order as wo
from runner import edge_check
from runner.edge_check import run_adapter
from runner.oracle import Tables, judge

SCHEMA = "ot-authority-result/v1"
TABLES = Tables(zones=("ZONE-01", "ZONE-02", "ZONE-03"), setpoint_min_c=19.0, setpoint_max_c=23.0,
                max_delta_c=1.0, budgets={})


def oracle_verdict(case: dict[str, Any], fault: str | None) -> dict[str, Any]:
    """The oracle on the same call: work order as an inclusive range, delegation fault as built."""
    call, order = case["call"], case["work_order"]
    rng = order["value_range"]
    proposal = {"at_sim_s": 0, "intent_ref": order["work_order_id"], "from_c": None,
                "call": {"tool_name": call["operation"], "arguments": {"zone_id": call["asset"],
                                                                         "setpoint_c": call["value"]}}}
    if fault:
        proposal["delegation_fault"] = fault
    work_orders = {order["work_order_id"]: {"zones": {order["asset"]: {"min": rng["min"], "max": rng["max"]}}}}
    return judge(proposal, work_orders, TABLES).to_dict()


def run_verifier(verifier: Path, set_dir: Path, out: Path, *, timeout_s: int | None = None,
                 known: set[str] | None = None) -> tuple[dict[str, dict[str, Any]] | None, str, float, list[str]]:
    """Run the verifier once over the set. Returns its answers by case, a status, the wall time, and
    problems with the answers: entries without a case id, answers for unknown cases, several answers for
    one case (that case then counts as malformed)."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    saved = edge_check.ADAPTER_TIMEOUT_S
    if timeout_s is not None:
        edge_check.ADAPTER_TIMEOUT_S = timeout_s
    t0 = time.perf_counter()
    try:
        rc, tail = run_adapter(verifier, set_dir, out)
    finally:
        edge_check.ADAPTER_TIMEOUT_S = saved
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if rc == 124:
        return None, "timeout", ms, []
    if rc != 0:
        last = tail.strip().splitlines()[-1] if tail.strip() else ""
        return None, f"error: exit {rc}: {last}", ms, []
    try:
        res = json.loads((out / "result.json").read_text("utf-8"))
        entries = res["verdicts"]
        if not isinstance(entries, list):
            raise TypeError("verdicts is not a list")
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, f"error: malformed result ({type(e).__name__})", ms, []
    answers: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for v in entries:
        if not isinstance(v, dict) or not isinstance(v.get("case_id"), str):
            problems.append("an answer without a case_id")
            continue
        cid = v["case_id"]
        if known is not None and cid not in known:
            problems.append(f"answer for unknown case {cid!r}")
        elif cid in answers:
            problems.append(f"more than one answer for {cid!r}")
            answers[cid] = {"verdict": "ambiguous", "reason": "more than one answer"}
        else:
            answers[cid] = v
    return answers, "ok", ms, problems


def run(set_dir: Path, verifier: Path, out: Path, *, timeout_s: int | None = None) -> dict[str, Any]:
    problems = af.check(set_dir)
    if any(p.startswith("manifest.json") for p in problems):
        raise SystemExit(f"{set_dir}: the manifest is unreadable or does not follow ot-authority-fixtures/v1: "
                         + "; ".join(problems))
    m = json.loads((set_dir / "manifest.json").read_text("utf-8"))
    verdicts, status, ms, answer_problems = run_verifier(verifier, set_dir, out / "verifier", timeout_s=timeout_s,
                                                         known={c["case_id"] for c in m["cases"]})
    rows = []
    for entry in m["cases"]:
        cid = entry["case_id"]
        case = json.loads((set_dir / "cases" / f"{cid}.json").read_text("utf-8"))
        if verdicts is None:
            aps = {"class": "timeout" if status == "timeout" else "error", "verdict": None, "reason": status}
        elif cid not in verdicts:
            aps = {"class": "unanswered", "verdict": None, "reason": None}
        elif verdicts[cid].get("verdict") not in ("pass", "refuse"):
            aps = {"class": "malformed", "verdict": verdicts[cid].get("verdict"), "reason": verdicts[cid].get("reason")}
        else:
            aps = {"class": "answered", "verdict": verdicts[cid]["verdict"], "reason": verdicts[cid].get("reason")}
        woc = wo.check(case["work_order"], case["call"], m["work_order_issuer_keys"], delegation=case["delegation"])
        exp = entry["expected"]
        orc = oracle_verdict(case, entry.get("delegation_fault"))
        admitted = aps["verdict"] == "pass" and woc["verdict"] == "pass"
        rows.append({
            "case_id": cid, "construction": entry["construction"], "delegation_fault": entry.get("delegation_fault"),
            "expected": exp, "aps": aps, "work_order": woc, "admitted": admitted, "oracle": orc,
            "aps_as_expected": aps["verdict"] == exp["aps"],
            "work_order_as_expected": woc["verdict"] == exp["work_order"],
            "oracle_agrees": (orc["verdict"] == "permitted") == exp["admitted"],
        })
    counts = {"cases": len(rows),
              "aps_as_expected": sum(r["aps_as_expected"] for r in rows),
              "work_order_as_expected": sum(r["work_order_as_expected"] for r in rows),
              "oracle_agrees": sum(r["oracle_agrees"] for r in rows),
              "admitted": sum(r["admitted"] for r in rows),
              "aps_classes": {k: sum(1 for r in rows if r["aps"]["class"] == k)
                              for k in ("answered", "error", "timeout", "malformed", "unanswered")}}
    ok = not problems and not answer_problems and all(r["aps_as_expected"] and r["work_order_as_expected"] and r["oracle_agrees"] for r in rows)
    verifier_meta = json.loads((verifier / "edge.json").read_text("utf-8"))
    result = {"schema": SCHEMA, "fixture_set": m["fixture_set"], "package_digest": m["package_digest"],
              "set_problems": problems, "verifier": {"edge_id": verifier_meta.get("edge_id"),
                                                     "project": verifier_meta.get("project"),
                                                     "status": status, "runtime_ms": ms,
                                                     "answer_problems": answer_problems},
              "counts": counts, "cases": rows, "pass": ok,
              "note": "Both checks ran on the same captured input. Errors, timeouts, malformed and unanswered "
                      "verifier answers are their own class: never a refusal, never a catch."}
    out.mkdir(parents=True, exist_ok=True)
    (out / "authority-result.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8", newline="\n")
    (out / "authority-result.md").write_text(render_md(result), "utf-8", newline="\n")
    return result


def render_md(r: dict[str, Any]) -> str:
    c = r["counts"]
    v = r["verifier"]
    L = [f"# authority_fixture: `{r['fixture_set']}` with `{v['edge_id']}`", "",
         f"Result: **{'PASS' if r['pass'] else 'FAIL'}**. Verifier {v['project']}: {v['status']}, {v['runtime_ms']} ms. "
         f"Package `{r['package_digest']}`" + (f"; set problems: {', '.join(r['set_problems'])}" if r["set_problems"] else "")
         + (f"; answer problems: {', '.join(v['answer_problems'])}" if v.get("answer_problems") else "") + ".", "",
         f"Delegation check as expected {c['aps_as_expected']}/{c['cases']}; work-order check as expected "
         f"{c['work_order_as_expected']}/{c['cases']}; oracle agrees {c['oracle_agrees']}/{c['cases']}; admitted "
         f"{c['admitted']}. Verifier answers by class: "
         + ", ".join(f"{k} {n}" for k, n in c["aps_classes"].items()) + ".", "",
         "| Case | Built as | Delegation check (expected / got, reason) | Work-order check (expected / got, reasons) | Admitted | Oracle |",
         "|---|---|---|---|---|---|"]
    for row in r["cases"]:
        a, w, e = row["aps"], row["work_order"], row["expected"]
        got_a = a["verdict"] if a["class"] == "answered" else f"*{a['class']}*"
        L.append(f"| `{row['case_id']}` | {row['construction']} | {e['aps']} / {got_a}"
                 f"{'' if row['aps_as_expected'] else ' ✗'} ({a['reason']}) | {e['work_order']} / {w['verdict']}"
                 f"{'' if row['work_order_as_expected'] else ' ✗'} ({', '.join(w['reasons']) or 'none'}) | "
                 f"{'yes' if row['admitted'] else 'no'} | {row['oracle']['verdict']} "
                 f"{'(' + row['oracle']['rule'] + ')' if row['oracle']['rule'] else ''}{'' if row['oracle_agrees'] else ' ✗'} |")
    L += ["", r["note"], ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.authority_harness")
    p.add_argument("--fixtures", required=True)
    p.add_argument("--verifier", required=True, help="verifier directory with edge.json and its entrypoint")
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=int, default=120, help="seconds the verifier may take over the whole set")
    p.add_argument("--trust-edge", action="append", default=[], metavar="EDGE_ID",
                   help="run this edge on the host instead of the container sandbox (your own code only)")
    a = p.parse_args(argv)
    from runner import edge_sandbox
    edge_sandbox.EXTRA_TRUSTED.update(a.trust_edge)
    r = run(Path(a.fixtures), Path(a.verifier), Path(a.out), timeout_s=a.timeout)
    print(render_md(r))
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
