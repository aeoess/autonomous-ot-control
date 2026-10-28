"""Mutation sets and benchmarks for offline effect-evidence edges.

    python -m runner.edge_mutations --work artifacts/<run>-work --out <dir> [--edge <dir> ...] [--bench 100 1000]

Seeds are the applied trails of one governed run whose every claim rests on a
recorded source (runner.edge_pass): positive by construction. From each seed
the tool builds mutants whose expected verdict follows from how they were
made and from the case contract (contracts/federation-case-v1.schema.json),
never from running a verifier. The set and its expectations are written and
hashed before any edge runs.

Two layers:

- **case**: what an edge reads. One operator per fault, boundary or
  canonicalisation trap, plus positive controls that must stay ESTABLISHED.
  This measures the verifier itself.
- **pipeline**: the raw exported trail and ledger, before runner.edge_pass
  builds the case. This measures that the pass counts an ESTABLISHED only on
  recorded inputs.

Scores per edge: kill rate (negatives not ESTABLISHED), exact verdict
accuracy, accepted missing evidence (ESTABLISHED where evidence was removed,
the worst error), false refusals of positive controls, claim attribution,
and, against the reference verifier, the mutants only one of them judged
correctly. The benchmark times each edge over growing case sets.

Use is not value and a benchmark is not a guarantee: the scores hold for
these operators on these trails of the simulated plant.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from runner import edge_pass
from runner.edge_check import run_adapter, sha256_bytes
from runner.federation_fixtures import CLAIMS, canonical_sha256

C, N, E = "CONTRADICTED", "NOT_ESTABLISHED", "ESTABLISHED"
NOT_E = frozenset({C, N})
SCHEMA = "ot-edge-mutations/v1"


def _redigest(c: dict[str, Any]) -> dict[str, Any]:
    c["evidence_sha256"] = canonical_sha256(c["remora_evidence"])
    return c


def _ev_note(c: dict[str, Any], note: Any) -> None:
    ev = c["remora_evidence"]
    if isinstance(ev, dict):
        ev["mutation_note"] = note
    else:
        c["remora_evidence"] = {"original": ev, "mutation_note": note}


# -- case-layer operators -------------------------------------------------------
# (operator id, claim, kind, expected verdicts, mutate(case, other) -> case)
Op = tuple[str, str | None, str, frozenset[str], Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]]


def _set(path: tuple[str, ...], value: Any) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    def f(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
        d = c
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
        return c
    return f


def _del(path: tuple[str, ...]) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    def f(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
        d = c
        for k in path[:-1]:
            d = d[k]
        d.pop(path[-1], None)
        return c
    return f


def _shift(path: tuple[str, ...], delta: float) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    def f(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
        d = c
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = round(d[path[-1]] + delta, 6)
        return c
    return f


def _seq(*fs: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]):
    def f(c: dict[str, Any], o: dict[str, Any]) -> dict[str, Any]:
        for g in fs:
            c = g(c, o)
        return c
    return f


def _swap_op(c: dict[str, Any], o: dict[str, Any]) -> dict[str, Any]:
    c["twin_operation"] = copy.deepcopy(o["twin_operation"])
    return c


def _upper_op_id(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    c["twin_operation"]["operation_id"] = c["twin_operation"]["operation_id"].upper()
    return c


def _other_zone(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    z = c["twin_operation"]["zone_id"]
    other = "ZONE-02" if z != "ZONE-02" else "ZONE-01"
    for k in ("zone_id", "target"):
        if k in c["twin_operation"]:
            c["twin_operation"][k] = other
    return c


def _obs_equal_dispatch(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    c["observation"]["observed_at_sim_s"] = c["execution"]["dispatched_at_sim_s"]
    return c


def _obs_before(seconds: int):
    def f(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
        c["observation"]["observed_at_sim_s"] = c["execution"]["dispatched_at_sim_s"] - seconds
        return c
    return f


def _evidence_field_changed(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    _ev_note(c, "edited after the digest was taken")
    return c


def _digest_flip(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    d = c["evidence_sha256"]
    c["evidence_sha256"] = d[:-1] + ("0" if d[-1] != "0" else "1")
    return c


def _digest_upper(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    c["evidence_sha256"] = c["evidence_sha256"].upper()
    return c


def _digest_ascii_escaped(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    """Non-ASCII evidence hashed with ensure_ascii=True: not the contract's canonical form."""
    import hashlib
    _ev_note(c, "Ålesund – ø")
    raw = json.dumps(c["remora_evidence"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    c["evidence_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return c


def _unicode_evidence(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    _ev_note(c, "Ålesund – ø")
    return _redigest(c)


def _reorder_evidence(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    ev = c["remora_evidence"]
    if isinstance(ev, dict):
        c["remora_evidence"] = dict(reversed(list(ev.items())))
    return c


def _int_setpoints(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    """20.0 written as 20 in the claim: the same number."""
    v = c["claim"]["setpoint_c"]
    if float(v).is_integer():
        c["claim"]["setpoint_c"] = int(v)
    return c


def _envelope_request_changed(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    # Native DecisionEnvelope path; aliases are exercised by the admission tests.
    c.setdefault("remora_envelope", {}).setdefault("request", {})["request_id"] = "other-proposal"
    return c


def _evidence_proposal_changed(c: dict[str, Any], _o: dict[str, Any]) -> dict[str, Any]:
    # Rehash so a digest mismatch cannot mask the missing proposal binding.
    c["remora_evidence"]["proposal_id"] = "other-proposal"
    return _redigest(c)


CASE_OPS: list[Op] = [
    # positive controls: must stay ESTABLISHED
    ("pc_identity", None, "positive", frozenset({E}), lambda c, o: c),
    ("pc_observed_at_dispatch", None, "positive", frozenset({E}), _obs_equal_dispatch),
    ("pc_observed_an_hour_later", None, "positive", frozenset({E}),
     _shift(("observation", "observed_at_sim_s"), 3600)),
    ("pc_evidence_key_order", None, "positive", frozenset({E}), _reorder_evidence),
    ("pc_non_ascii_evidence", None, "positive", frozenset({E}), _unicode_evidence),
    ("pc_integer_setpoint", None, "positive", frozenset({E}), _int_setpoints),
    ("pc_extra_reviewer_field", None, "positive", frozenset({E}),
     _set(("decision", "reviewer", "note"), "extra field")),
    # decision_correspondence
    ("dc_not_approved", "decision_correspondence", "negative", frozenset({C}),
     _seq(_set(("decision", "approved"), False), _set(("decision", "decision"), "verify"))),
    ("dc_escalate_not_approved", "decision_correspondence", "negative", frozenset({C}),
     _seq(_set(("decision", "approved"), False), _set(("decision", "decision"), "escalate"))),
    ("dc_reviewer_refused", "decision_correspondence", "negative", frozenset({C}),
     _seq(_set(("decision", "decision"), "verify"), _set(("decision", "approved"), True),
          _set(("decision", "reviewer"), {"approved": False}))),
    ("dc_denied_but_applied", "decision_correspondence", "negative", frozenset({C}),
     _seq(_set(("decision", "decision"), "deny"), _set(("decision", "approved"), False),
          _set(("execution", "executed"), False))),
    ("dc_not_executed_but_observed", "decision_correspondence", "negative", frozenset({C}),
     _set(("execution", "executed"), False)),
    # execution.executed is required by the contract: without it the case is malformed, so anything but
    # ESTABLISHED is correct.
    ("dc_executed_missing", "decision_correspondence", "malformed", NOT_E, _del(("execution", "executed"))),
    # single_dispatch
    ("sd_twice", "single_dispatch", "negative", frozenset({C}), _set(("execution", "executions"), 2)),
    ("sd_zero", "single_dispatch", "negative", frozenset({C}), _set(("execution", "executions"), 0)),
    ("sd_missing", "single_dispatch", "missing", frozenset({N}), _del(("execution", "executions"))),
    ("sd_string_one", "single_dispatch", "malformed", NOT_E, _set(("execution", "executions"), "1")),
    # call_binding
    ("cb_envelope_request", "call_binding", "negative", frozenset({C}), _envelope_request_changed),
    ("cb_evidence_proposal", "call_binding", "negative", frozenset({C}), _evidence_proposal_changed),
    ("cb_other_operation", "call_binding", "negative", frozenset({C}), _swap_op),
    ("cb_operation_id_case", "call_binding", "negative", frozenset({C}), _upper_op_id),
    ("cb_record_missing", "call_binding", "missing", frozenset({N}), _set(("twin_operation",), None)),
    # declared_effect_observed
    ("de_claim_plus_half", "declared_effect_observed", "negative", frozenset({C}),
     _shift(("claim", "setpoint_c"), 0.5)),
    ("de_claim_plus_milli", "declared_effect_observed", "negative", frozenset({C}),
     _shift(("claim", "setpoint_c"), 0.001)),
    ("de_claim_plus_tenth_milli", "declared_effect_observed", "negative", frozenset({C}),
     _shift(("claim", "setpoint_c"), 0.0001)),
    ("de_twin_rejected", "declared_effect_observed", "negative", frozenset({C}),
     _set(("twin_operation", "status"), "REJECTED")),
    ("de_twin_value_only", "declared_effect_observed", "negative", frozenset({C}),
     _shift(("twin_operation", "new_setpoint_c"), 0.5)),
    ("de_twin_other_zone", "declared_effect_observed", "negative", frozenset({C}), _other_zone),
    ("de_observed_off", "declared_effect_observed", "negative", frozenset({C}),
     _shift(("observation", "observed_setpoint_c"), -0.5)),
    ("de_observed_off_tenth_milli", "declared_effect_observed", "negative", frozenset({C}),
     _shift(("observation", "observed_setpoint_c"), -0.0001)),
    ("de_observation_missing", "declared_effect_observed", "missing", frozenset({N}), _set(("observation",), None)),
    ("de_observed_value_missing", "declared_effect_observed", "missing", frozenset({N}),
     _del(("observation", "observed_setpoint_c"))),
    # observation_after_dispatch
    ("oa_one_second_early", "observation_after_dispatch", "negative", frozenset({C}), _obs_before(1)),
    ("oa_fifteen_minutes_early", "observation_after_dispatch", "negative", frozenset({C}), _obs_before(900)),
    ("oa_dispatch_time_missing", "observation_after_dispatch", "missing", frozenset({N}),
     _del(("execution", "dispatched_at_sim_s"))),
    ("oa_dispatch_time_null", "observation_after_dispatch", "missing", frozenset({N}),
     _set(("execution", "dispatched_at_sim_s"), None)),
    ("oa_observation_time_missing", "observation_after_dispatch", "missing", frozenset({N}),
     _del(("observation", "observed_at_sim_s"))),
    # evidence_digest_integrity
    ("ed_evidence_changed", "evidence_digest_integrity", "negative", frozenset({C}), _evidence_field_changed),
    ("ed_digest_one_char", "evidence_digest_integrity", "negative", frozenset({C}), _digest_flip),
    ("ed_digest_ascii_escaped", "evidence_digest_integrity", "negative", frozenset({C}), _digest_ascii_escaped),
    ("ed_digest_uppercase", "evidence_digest_integrity", "malformed", NOT_E, _digest_upper),
    ("malformed_extra_field", None, "malformed", NOT_E, _set(("unexpected",), True)),
    ("malformed_boolean_count", "single_dispatch", "malformed", NOT_E,
     _set(("execution", "executions"), True)),
]


def case_mutants(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every operator on every seed; `other` is the next seed (for swaps). Deterministic."""
    out = []
    for i, s in enumerate(seeds):
        other = seeds[(i + 1) % len(seeds)]
        pid = s["claim"]["proposal_id"]
        for op_id, claim, kind, expect, f in CASE_OPS:
            if op_id == "cb_other_operation" and other is s:
                continue
            m = f(copy.deepcopy(s), other)
            m["case_id"] = f"{op_id}--{pid}"
            m["kind"] = "positive" if kind == "positive" else "negative"
            m["fault"] = None if kind == "positive" else op_id
            out.append({"case": m, "operator": op_id, "claim_id": claim, "class": kind,
                        "expected": sorted(expect), "seed": pid})
    return out


def write_set(mutants: list[dict[str, Any]], set_id: str, dst: Path, seed_note: str) -> str:
    """Cases in the fixture layout the adapters read, and the expectations, written before any edge runs."""
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "cases").mkdir(parents=True)
    for m in mutants:
        (dst / "cases" / f"{m['case']['case_id']}.json").write_text(
            # Preserve the key-order positive control on disk. Sorting the entire
            # case recursively would undo it before any verifier could read it.
            json.dumps(m["case"], indent=2) + "\n", "utf-8", newline="\n")
    manifest = {"schema": "ot-federation-fixtures/v1", "fixture_set": set_id, "kind": "mutation_set",
                "scope": f"Mutants of fully recorded applied trails ({seed_note}). Expected verdicts follow "
                         "from each operator and the case contract, never from a verifier.",
                "claims": [{"claim_id": k, "statement": v} for k, v in CLAIMS.items()],
                "cases": [{"case_id": m["case"]["case_id"], "kind": m["case"]["kind"], "fault": m["case"]["fault"],
                           "claim_id": m["claim_id"], "expected_verdict": m["expected"][0] if len(m["expected"]) == 1
                           else "NOT_ESTABLISHED_OR_CONTRADICTED"} for m in mutants]}
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (dst / "manifest.json").write_bytes(raw)
    expect = {m["case"]["case_id"]: {k: m[k] for k in ("operator", "claim_id", "class", "expected", "seed")}
              for m in mutants}
    (dst / "expected.json").write_text(json.dumps(expect, indent=2, sort_keys=True) + "\n", "utf-8", newline="\n")
    return sha256_bytes(raw)


def run_edge(edge_dir: Path, case_dir: Path, out: Path) -> tuple[dict[str, dict[str, Any]] | None, float, int]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    t0 = time.perf_counter()
    rc, _ = run_adapter(edge_dir, case_dir, out)
    ms = (time.perf_counter() - t0) * 1000
    if rc != 0:
        return None, ms, rc
    try:
        res = json.loads((out / "result.json").read_text("utf-8"))
        return {v["case_id"]: v for v in res["verdicts"]}, ms, rc
    except (OSError, ValueError, KeyError, TypeError):
        return None, ms, rc


def per_operator(edge_dir: Path, mutants: list[dict[str, Any]], out: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    verdicts: dict[str, dict[str, Any]] = {}
    crashed = []
    for op_id in sorted({m["operator"] for m in mutants}):
        sub = [m for m in mutants if m["operator"] == op_id]
        d = out / op_id
        write_set(sub, f"op-{op_id}", d / "set", "per operator")
        v, _, _ = run_edge(edge_dir, d / "set", d / "run")
        if v is None:
            crashed.append(op_id)
        else:
            verdicts.update(v)
    return verdicts, crashed


def score(expect: dict[str, dict[str, Any]], verdicts: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    if verdicts is None:
        return {"ran": False}
    by_op: dict[str, Counter[str]] = {}
    by_claim: dict[str, Counter[str]] = {}
    wrong = []
    for cid, x in sorted(expect.items()):
        v = (verdicts.get(cid) or {}).get("verdict", "UNANSWERED")
        ok = v in x["expected"]
        claim_ok = x["claim_id"] is None or v == E or (verdicts.get(cid) or {}).get("claim_id") in (None, x["claim_id"])
        o = by_op.setdefault(x["operator"], Counter())
        o["n"] += 1
        o["correct"] += ok
        o[v] += 1
        if x["claim_id"]:
            k = by_claim.setdefault(x["claim_id"], Counter())
            k["n"] += 1
            k["killed"] += v != E
            k["correct"] += ok
            k["claim_attributed"] += ok and claim_ok
        if not ok:
            wrong.append({"case_id": cid, "operator": x["operator"], "expected": x["expected"], "got": v,
                          "reason": (verdicts.get(cid) or {}).get("reason")})
    cls = Counter()
    for cid, x in expect.items():
        v = (verdicts.get(cid) or {}).get("verdict", "UNANSWERED")
        cls[f"{x['class']}_n"] += 1
        cls[f"{x['class']}_correct"] += v in x["expected"]
        cls[f"{x['class']}_established"] += v == E
        cls[f"{x['class']}_unanswered"] += v == "UNANSWERED"
    neg = cls["negative_n"] + cls["missing_n"] + cls["malformed_n"]
    # A mutant is killed only by an answer other than ESTABLISHED; no answer is not a kill.
    killed = sum(cls[f"{k}_n"] - cls[f"{k}_established"] - cls[f"{k}_unanswered"]
                 for k in ("negative", "missing", "malformed"))
    return {"ran": True, "cases": len(expect),
            "kill_rate": round(killed / neg, 4) if neg else None, "killed": killed, "mutants": neg,
            "exact_accuracy": round(sum(1 for c, x in expect.items()
                                        if (verdicts.get(c) or {}).get("verdict") in x["expected"]) / len(expect), 4),
            "accepted_missing_evidence": cls["missing_established"],
            "unanswered": sum(v for k, v in cls.items() if k.endswith("_unanswered")),
            "false_refusals": cls["positive_n"] - cls["positive_correct"], "positive_controls": cls["positive_n"],
            "by_class": dict(cls),
            "by_claim": {k: dict(v) for k, v in sorted(by_claim.items())},
            "by_operator": {k: dict(v) for k, v in sorted(by_op.items())},
            "wrong": wrong}


# -- pipeline-layer operators (raw trail + ledger, scored on runner.edge_pass) --
def _recapture(rec: dict[str, Any], row: dict[str, Any]) -> None:
    from runner.demo import capture_digests
    row["capture"] = capture_digests(rec)


def _p_evidence_edited(rec, row):
    rec["evidence"].setdefault("effect_verification", {})["mutation_note"] = "edited after export"


def _p_observer_removed(rec, row):
    rec["effect_observation"] = None
    _recapture(rec, row)


def _p_observer_tampered(rec, row):
    rec["effect_observation"]["sim_time_s"] = int(rec["effect_observation"]["sim_time_s"]) + 60


def _p_observer_disagrees(rec, row):
    eo = {k: v for k, v in rec["effect_observation"].items() if k != "observation_digest"}
    eo["observed_setpoint_c"] = round(float(eo["observed_setpoint_c"]) - 0.5, 3)
    rec["effect_observation"] = {**eo, "observation_digest": "sha256:" + canonical_sha256(eo)}
    _recapture(rec, row)


def _p_capture_missing(rec, row):
    row.pop("capture", None)


def _p_capture_missing_section_edited(rec, row):
    row.pop("capture", None)
    rec["evidence"]["lifecycle"]["mutation_note"] = "edited after REMORA exported it"


def _p_lifecycle_incomplete(rec, row):
    rec["evidence"]["lifecycle"]["events"] = rec["evidence"]["lifecycle"]["events"][:-1]
    _recapture(rec, row)


def _p_double_execution(rec, row):
    ev = rec["evidence"]["lifecycle"]["events"]
    extra = copy.deepcopy(next(e for e in ev if e.get("event") == "execution_result"))
    ev.append(extra)
    rec["evidence"]["manifest"]["event_count"] = len(ev)
    _recapture(rec, row)


def _p_readback_missing(rec, row):
    rec["evidence"]["effect_verification"]["history"] = []
    _recapture(rec, row)


def _p_twin_record_missing(rec, row):
    rec["twin_operation"] = None
    _recapture(rec, row)


PIPELINE_OPS: list[tuple[str, str, frozenset[str], Callable[[dict[str, Any], dict[str, Any]], None]]] = [
    ("p_capture_missing_manifest_ok", "positive", frozenset({E}), _p_capture_missing),
    ("p_evidence_edited_after_capture", "negative", frozenset({C}), _p_evidence_edited),
    ("p_section_edited_no_capture", "negative", frozenset({C}), _p_capture_missing_section_edited),
    ("p_observer_record_tampered", "negative", frozenset({C}), _p_observer_tampered),
    ("p_observer_disagrees_with_readback", "negative", frozenset({C}), _p_observer_disagrees),
    ("p_double_execution", "negative", frozenset({C}), _p_double_execution),
    ("p_observer_record_removed", "missing", frozenset({N}), _p_observer_removed),
    ("p_lifecycle_incomplete", "missing", frozenset({N}), _p_lifecycle_incomplete),
    ("p_readback_missing", "missing", frozenset({N}), _p_readback_missing),
    ("p_twin_record_missing", "missing", frozenset({N}), _p_twin_record_missing),
]


def consistent(c: dict[str, Any]) -> bool:
    """The recorded sources agree with each other, checked without any verifier: a seed must be positive
    by construction, so a trail with a real discrepancy is never mutated as if it were sound."""
    op, obs, ex, cl, d = c.get("twin_operation") or {}, c.get("observation") or {}, c["execution"], c["claim"], c["decision"]
    same = lambda a, b: isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 1e-9  # noqa: E731
    return (op.get("operation_id") == cl["proposal_id"] and op.get("status") == "APPLIED"
            and op.get("zone_id") == cl["zone_id"] and same(op.get("new_setpoint_c"), cl["setpoint_c"])
            and same(obs.get("observed_setpoint_c"), cl["setpoint_c"])
            and isinstance(obs.get("observed_at_sim_s"), int) and isinstance(ex.get("dispatched_at_sim_s"), int)
            and obs["observed_at_sim_s"] >= ex["dispatched_at_sim_s"]
            and ex.get("executed") is True and ex.get("executions") == 1
            and (d.get("approved") is True or d.get("decision") == "accept")
            and c.get("evidence_sha256") == canonical_sha256(c.get("remora_evidence")))


def seeds_from_work(work: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Applied trails whose every claim rests on a recorded source and whose sources agree; the rest are
    named and left out."""
    cases, inputs, _ = edge_pass.build_cases_with_inputs(work)
    seeds, left = [], []
    for c in cases:
        i = inputs[c["case_id"]]
        if all(b["recorded"] for b in i["basis"].values()) and not i["input_findings"] and consistent(c):
            seeds.append(c)
        else:
            left.append(c["case_id"])
    return seeds, left


def pipeline_layer(work: Path, out: Path, edges: list[Path]) -> dict[str, Any]:
    rows = {json.loads(line)["proposal_id"]: json.loads(line) for line in
            (work / "ledger.jsonl").read_text("utf-8").splitlines() if '"proposal_id"' in line}
    seeds, _ = seeds_from_work(work)
    seed_ids = {c["claim"]["proposal_id"] for c in seeds}
    results = {}
    for op_id, kind, expect, f in PIPELINE_OPS:
        w = out / "pipeline" / op_id / "work"
        if w.exists():
            shutil.rmtree(w.parent)
        (w / "decisions").mkdir(parents=True)
        led = []
        for pid in sorted(seed_ids):
            rec = json.loads((work / "decisions" / f"{pid}.json").read_text("utf-8"))
            row = copy.deepcopy(rows[pid])
            f(rec, row)
            (w / "decisions" / f"{pid}.json").write_text(json.dumps(rec), "utf-8", newline="\n")
            led.append(row)
        (w / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in led), "utf-8", newline="\n")
        s = edge_pass.run_pass(w, out / "pipeline" / op_id / "edges", edges, op_id)
        per_edge = {}
        for e in s["edges"]:
            if not e.get("available"):
                per_edge[e["edge_id"]] = {"ran": False}
                continue
            got = e["counts_on_recorded_inputs"]
            n = sum(got.values())
            per_edge[e["edge_id"]] = {"ran": True, "trails": n, "counted": got,
                                      "correct": sum(v for k, v in got.items() if k in expect)}
        results[op_id] = {"class": kind, "expected": sorted(expect), "edges": per_edge}
    return {"seeds": len(seed_ids), "operators": results}


def benchmark(edges: list[Path], seeds: list[dict[str, Any]], sizes: list[int], out: Path,
              repeats: int = 3) -> dict[str, Any]:
    """Wall time of one edge run over n cases (seeds repeated under new ids), median of `repeats`."""
    res: dict[str, Any] = {"sizes": sizes, "repeats": repeats, "edges": {}}
    for n in sizes:
        cases = []
        for i in range(n):
            c = copy.deepcopy(seeds[i % len(seeds)])
            c["case_id"] = f"bench-{i:06d}"
            cases.append({"case": c, "operator": "pc_identity", "claim_id": None, "class": "positive",
                          "expected": [E], "seed": c["claim"]["proposal_id"]})
        d = out / "bench" / f"n{n}"
        write_set(cases, f"bench-{n}", d, "benchmark")
        for edge_dir in edges:
            eid = json.loads((edge_dir / "edge.json").read_text("utf-8"))["edge_id"]
            times, ok = [], True
            for r in range(repeats):
                v, ms, rc = run_edge(edge_dir, d, out / "bench" / f"n{n}-{eid}-{r}")
                times.append(ms)
                ok = ok and v is not None and len(v) == n
            med = statistics.median(times)
            res["edges"].setdefault(eid, []).append(
                {"cases": n, "ok": ok, "median_ms": round(med, 1), "ms_per_case": round(med / n, 3),
                 "cases_per_s": round(n / (med / 1000), 1) if med else None, "runs_ms": [round(t, 1) for t in times]})
    return res


def run(work: Path, out: Path, edges: list[Path], bench_sizes: list[int]) -> dict[str, Any]:
    seeds, left = seeds_from_work(work)
    if not seeds:
        raise SystemExit("no fully recorded applied trail to seed from")
    mutants = case_mutants(seeds)
    set_dir = out / "case-set"
    set_sha = write_set(mutants, f"mutations-{work.name}", set_dir, f"{len(seeds)} seeds from {work.name}")
    expect = json.loads((set_dir / "expected.json").read_text("utf-8"))
    case_scores, ref = {}, None
    for edge_dir in edges:
        eid = json.loads((edge_dir / "edge.json").read_text("utf-8"))["edge_id"]
        verdicts, ms, rc = run_edge(edge_dir, set_dir, out / "case-runs" / eid)
        crashed: list[str] = []
        if verdicts is None:
            # One case can stop a whole run. Run each operator on its own so the rest is still measured,
            # and name the operators the edge could not run on (their mutants count as unanswered).
            verdicts, crashed = per_operator(edge_dir, mutants, out / "case-runs" / f"{eid}-per-operator")
        sc = score(expect, verdicts)
        sc.update(runtime_ms=round(ms, 1), exit=rc, whole_set_ran=rc == 0, crashed_operators=crashed)
        case_scores[eid] = (sc, verdicts)
        if eid == edge_pass.REFERENCE_EDGE_ID:
            ref = verdicts
    for eid, (sc, v) in case_scores.items():
        if ref is not None and v is not None and eid != edge_pass.REFERENCE_EDGE_ID:
            right = {c for c, x in expect.items() if (v.get(c) or {}).get("verdict") in x["expected"]}
            ref_right = {c for c, x in expect.items() if (ref.get(c) or {}).get("verdict") in x["expected"]}
            sc["vs_reference"] = {"only_this_edge_correct": sorted(right - ref_right),
                                  "only_reference_correct": sorted(ref_right - right)}
    summary = {"schema": SCHEMA, "source_work": str(work), "seeds": len(seeds), "seeds_left_out": left,
               "case_set_manifest_sha256": set_sha, "operators": len(CASE_OPS), "case_mutants": len(mutants),
               "case_layer": {eid: sc for eid, (sc, _) in case_scores.items()},
               "pipeline_layer": pipeline_layer(work, out, edges),
               "benchmark": benchmark(edges, seeds, bench_sizes, out) if bench_sizes else None,
               "note": "Expected verdicts come from each operator and the case contract, written before any edge "
                       "ran. Scores hold for these operators on these simulated trails; they are not a guarantee."}
    (out / "mutations.json").write_text(json.dumps(summary, indent=2) + "\n", "utf-8", newline="\n")
    (out / "mutations.md").write_text(render_md(summary), "utf-8", newline="\n")
    return summary


def render_md(s: dict[str, Any]) -> str:
    L = ["# Mutation and benchmark report for offline effect-evidence edges", "",
         f"Seeds: {s['seeds']} fully recorded applied trails from `{Path(s['source_work']).name}`"
         + (f" ({len(s['seeds_left_out'])} left out)" if s["seeds_left_out"] else "") + f". "
         f"{s['operators']} case operators, {s['case_mutants']} case mutants; case set manifest "
         f"`{s['case_set_manifest_sha256'][:16]}…`. {s['note']}", "",
         "## Case layer (what the edge reads)", "",
         "| Edge | Whole set ran | Kill rate | Exact accuracy | Accepted missing evidence | False refusals | Runtime ms |",
         "|---|---|---:|---:|---:|---:|---:|"]
    for eid, sc in s["case_layer"].items():
        if not sc.get("ran"):
            L.append(f"| `{eid}` | no (exit {sc.get('exit')}) | | | | | |")
            continue
        L.append(f"| `{eid}` | {'yes' if sc.get('whole_set_ran') else 'no (per operator)'} | {sc['killed']}/{sc['mutants']} ({sc['kill_rate']:.1%}) | {sc['exact_accuracy']:.1%} | "
                 f"{sc['accepted_missing_evidence']} | {sc['false_refusals']}/{sc['positive_controls']} | {sc['runtime_ms']} |")
    ops = sorted({op for sc in s["case_layer"].values() if sc.get("ran") for op in sc["by_operator"]})
    ran = [e for e, sc in s["case_layer"].items() if sc.get("ran")]
    L += ["", "Per operator (correct / n):", "", "| Operator | " + " | ".join(f"`{e}`" for e in ran) + " |",
          "|---|" + "---:|" * len(ran)]
    for op in ops:
        L.append(f"| `{op}` | " + " | ".join(
            f"{s['case_layer'][e]['by_operator'].get(op, {}).get('correct', 0)}/"
            f"{s['case_layer'][e]['by_operator'].get(op, {}).get('n', 0)}" for e in ran) + " |")
    for eid in ran:
        vr = s["case_layer"][eid].get("vs_reference")
        if vr:
            L += ["", f"`{eid}` against the reference: only this edge correct on {len(vr['only_this_edge_correct'])}, "
                      f"only the reference correct on {len(vr['only_reference_correct'])} mutants."]
        cr = s["case_layer"][eid].get("crashed_operators")
        if cr:
            L += ["", f"`{eid}` did not complete the whole set; run per operator, it crashed on "
                      + ", ".join(f"`{c}`" for c in cr) + " (those mutants count as unanswered, i.e. wrong)."]
        w = s["case_layer"][eid]["wrong"]
        if w:
            byop = Counter(x["operator"] for x in w)
            L += ["", f"`{eid}` wrong on: " + ", ".join(f"`{k}` {v}" for k, v in sorted(byop.items()))
                  + f". Example: expected {w[0]['expected']}, got {w[0]['got']} ({w[0]['reason']})."]
    pl = s["pipeline_layer"]
    ran = sorted({e for r in pl["operators"].values() for e in r["edges"]})
    L += ["", "## Pipeline layer (raw trail and ledger, counted by runner.edge_pass)", "",
          f"{pl['seeds']} trails per operator, mutated before the pass builds the cases.", "",
          "| Operator | Class | Expected | " + " | ".join(f"`{e}`" for e in ran) + " |",
          "|---|---|---|" + "---|" * len(ran)]
    for op, r in pl["operators"].items():
        cells = []
        for e in ran:
            x = r["edges"].get(e, {})
            cells.append(f"{x.get('correct', 0)}/{x.get('trails', 0)}" if x.get("ran") else "did not run")
        L.append(f"| `{op}` | {r['class']} | {'/'.join(r['expected'])} | " + " | ".join(cells) + " |")
    b = s.get("benchmark")
    if b:
        L += ["", f"## Benchmark (median of {b['repeats']} runs, one process start per run)", "",
              "| Edge | Cases | OK | Median ms | ms per case | Cases per s |", "|---|---:|---|---:|---:|---:|"]
        for eid, rows in b["edges"].items():
            L += [f"| `{eid}` | {r['cases']} | {'yes' if r['ok'] else 'no'} | {r['median_ms']} | {r['ms_per_case']} | "
                  f"{r['cases_per_s']} |" for r in rows]
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.edge_mutations")
    p.add_argument("--work", required=True, help="a governed run's work directory (decisions/, ledger.jsonl)")
    p.add_argument("--out", required=True)
    p.add_argument("--edge", action="append", default=None, help="edge directory (repeatable); default: every "
                   "registered offline effect_evidence edge")
    p.add_argument("--bench", type=int, nargs="*", default=[], help="benchmark case counts, e.g. 100 1000")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--trust-edge", action="append", default=[], metavar="EDGE_DIR_NAME",
                   help="run this edge on the host instead of the container sandbox (your own code only)")
    a = p.parse_args(argv)
    from runner import edge_sandbox
    edge_sandbox.EXTRA_TRUSTED.update(a.trust_edge)
    edges = [Path(e) for e in a.edge] if a.edge else edge_pass.offline_effect_edges()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    s = run(Path(a.work), out, edges, a.bench)
    print(render_md(s))
    ref = s["case_layer"].get(edge_pass.REFERENCE_EDGE_ID, {})
    return 0 if ref.get("ran") and not ref.get("wrong") else 1


if __name__ == "__main__":
    sys.exit(main())
