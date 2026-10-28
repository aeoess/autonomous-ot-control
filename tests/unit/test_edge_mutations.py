"""runner.edge_mutations: mutation sets and benchmarks for offline effect-evidence edges.

On the synthetic demo export shared with the edge pass tests:
- seeds are only trails whose recorded sources agree, checked without a verifier;
- the case set and its expectations are deterministic and written before any edge runs;
- the reference verifier is exactly right on every case operator and every pipeline operator
  (it is the contract's reading; a failure here means an operator or the verifier is wrong);
- an edge that establishes everything is scored as such: no kills, missing evidence accepted;
- an edge that crashes on part of the set is run per operator, the crashing operator is named,
  and its mutants count as unanswered, never as killed;
- the benchmark times each edge over n cases.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from runner import edge_mutations as m

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_edge_pass as T  # noqa: E402

LENIENT = '''import json, sys
from pathlib import Path
a = sys.argv
fx, out = Path(a[a.index("--fixtures") + 1]), Path(a[a.index("--out") + 1])
v = [{"case_id": json.loads(p.read_text("utf-8"))["case_id"], "verdict": "ESTABLISHED"} for p in sorted((fx / "cases").glob("*.json"))]
out.write_text(json.dumps({"verdicts": v}), "utf-8")
'''
CRASHY = '''import json, sys
from pathlib import Path
a = sys.argv
fx, out = Path(a[a.index("--fixtures") + 1]), Path(a[a.index("--out") + 1])
v = []
for p in sorted((fx / "cases").glob("*.json")):
    c = json.loads(p.read_text("utf-8"))
    (c["observation"] or {"observed_setpoint_c": None})["observed_setpoint_c"]  # KeyError only when the value is missing
    v.append({"case_id": c["case_id"], "verdict": "NOT_ESTABLISHED"})
out.write_text(json.dumps({"verdicts": v}), "utf-8")
'''


def _edge(tmp_path: Path, name: str, src: str) -> Path:
    d = tmp_path / "edges" / name
    shutil.copytree(T.TEMPLATE, d, ignore=shutil.ignore_patterns("__pycache__"))
    e = json.loads((d / "edge.json").read_text("utf-8"))
    e["edge_id"] = name
    (d / "edge.json").write_text(json.dumps(e), "utf-8")
    (d / "adapter.py").write_text(src, "utf-8")
    return d


def test_seeds_are_only_trails_whose_sources_agree(tmp_path):
    seeds, left = m.seeds_from_work(T._work(tmp_path))
    assert sorted(s["claim"]["proposal_id"] for s in seeds) == ["p-ok-1", "p-ok-2"]
    assert sorted(left) == ["live-p-bad", "live-p-noop"]


def test_the_case_set_is_deterministic_and_sealed_before_any_edge(tmp_path):
    seeds, _ = m.seeds_from_work(T._work(tmp_path))
    a = m.write_set(m.case_mutants(seeds), "s", tmp_path / "a", "t")
    b = m.write_set(m.case_mutants(seeds), "s", tmp_path / "b", "t")
    assert a == b
    exp = json.loads((tmp_path / "a" / "expected.json").read_text("utf-8"))
    assert len(exp) == 2 * len(m.CASE_OPS) and all(x["expected"] for x in exp.values())
    assert {x["class"] for x in exp.values()} == {"positive", "negative", "missing", "malformed"}


def test_reference_is_exact_on_every_operator_and_lenient_and_crashy_edges_are_scored_honestly(tmp_path):
    work = T._work(tmp_path)
    edges = [T.TEMPLATE, _edge(tmp_path, "lenient-edge", LENIENT), _edge(tmp_path, "crashy-edge", CRASHY)]
    s = m.run(work, tmp_path / "out", edges, [10])
    ref = s["case_layer"]["template-reference-verifier"]
    assert ref["whole_set_ran"] and ref["wrong"] == [] and ref["kill_rate"] == 1.0 and ref["false_refusals"] == 0
    for op, r in s["pipeline_layer"]["operators"].items():
        x = r["edges"]["template-reference-verifier"]
        assert x["ran"] and x["correct"] == x["trails"] == 2, op
    lenient = s["case_layer"]["lenient-edge"]
    assert lenient["killed"] == 0 and lenient["accepted_missing_evidence"] > 0 and lenient["false_refusals"] == 0
    crashy = s["case_layer"]["crashy-edge"]
    assert not crashy["whole_set_ran"] and crashy["crashed_operators"] == ["de_observed_value_missing"]
    assert crashy["unanswered"] == 2 and crashy["killed"] == crashy["mutants"] - 2
    assert len(crashy["vs_reference"]["only_reference_correct"]) > 0
    b = s["benchmark"]["edges"]["template-reference-verifier"][0]
    assert b["cases"] == 10 and b["ok"] and b["median_ms"] > 0
    md = (tmp_path / "out" / "mutations.md").read_text("utf-8")
    assert "crashed on `de_observed_value_missing`" in md and "## Benchmark" in md
    assert m.main(["--work", str(work), "--out", str(tmp_path / "out2"), "--edge", str(T.TEMPLATE)]) == 0
