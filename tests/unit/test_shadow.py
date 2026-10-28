"""Shadow evaluation (ADR-011, authority-fixture contract): every proposal, before REMORA, without effect.

- each shadow edge's answer is recorded beside the row; an unavailable answer is recorded as such and
  counted apart, never as a refusal; nothing else on the row changes;
- in the demo's governed write the shadow edges are asked before REMORA assesses the call, so a proposal
  REMORA refuses at assess, or the reviewer rejects, is still judged; the context carries the fixed
  decision time, and an inline edge (when consulted) gets the very same context;
- the A/B/C accounting puts forbidden proposals in the four cells (both stop, shadow only, governed only,
  neither), counts false refusals of permitted proposals on each side, and keeps unavailable answers and
  unjudged proposals apart.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from runner import ab_compare, demo
from runner.admission import Decision
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SC = load_scenario(ROOT / "scenarios" / "trondheim-week-2026w03-hvac-v1.json")


class FakeEdge:
    def __init__(self, edge_id, verdict="refuse", available=True):
        self.edge_id, self.verdict, self.available, self.calls = edge_id, verdict, available, []

    def decide(self, call, context):
        self.calls.append((call, context))
        return Decision(self.edge_id, self.verdict, "delegation_expired" if self.verdict == "refuse" else "ok",
                        self.available, 3.0)


class FakeStack:
    """Answers the demo's steps: assess refuses or returns a review item; everything else succeeds."""
    def __init__(self, assess_ok):
        self.assess_ok, self.steps = assess_ok, []

    def step(self, op, **kw):
        self.steps.append(op)
        if op == "assess":
            if not self.assess_ok:
                return {"ok": False, "status": 409, "body": {"detail": "contract_violation"}}
            return {"ok": True, "status": 200, "body": {"proposal_id": "p-1", "review_item_id": "i-1",
                                                         "decision": "verify", "reasons": []}}
        return {"ok": True, "status": 200, "body": {}}


def test_shadow_answers_are_recorded_and_change_nothing(monkeypatch):
    a, b = FakeEdge("aps-shadow", "refuse"), FakeEdge("down", "refuse", available=False)
    monkeypatch.setattr(demo, "SHADOW_EDGES", [a, b])
    row, counts = {"applied": True}, Counter()
    demo.consult_shadow_edges({"tool_name": "set_zone_setpoint"}, {"x": 1}, row, counts)
    assert row["applied"] is True and set(row) == {"applied", "shadow"}
    assert row["shadow"]["aps-shadow"]["verdict"] == "refuse" and row["shadow"]["down"]["available"] is False
    assert counts == Counter({"shadow_refuse": 1, "shadow_unavailable": 1})


def test_shadow_is_asked_before_remora_even_when_remora_or_the_reviewer_refuses(monkeypatch, tmp_path):
    shadow = FakeEdge("aps-shadow", "permit")
    monkeypatch.setattr(demo, "SHADOW_EDGES", [shadow])
    monkeypatch.setattr(demo, "INLINE_EDGE", None)
    s = FakeStack(assess_ok=False)
    row = demo.governed_write(s, SC, 3600, "2026-01-15", "ZONE-01", 19.0, 26.0, "attack", "PLAN-2026-01-15",
                              tmp_path, Counter(), "attack:out_of_contract")
    assert row["decision"] == "refused" and not row["applied"] and row["shadow"]["aps-shadow"]["verdict"] == "permit"
    assert s.steps == ["assess"] and len(shadow.calls) == 1
    call, cx = shadow.calls[0]
    assert call["arguments"] == {"zone_id": "ZONE-01", "setpoint_c": 26.0}
    assert cx["decision_time"] == row["sim_utc"] and cx["sim_time_s"] == 3600
    # The careful reviewer rejects a write without today's plan: still judged in shadow, nothing applied.
    monkeypatch.setattr(demo, "REVIEWER_MODEL", "careful")
    row = demo.governed_write(FakeStack(assess_ok=True), SC, 7200, "2026-01-15", "ZONE-01", 19.0, 20.0, "no plan",
                              None, tmp_path, Counter(), "attack:no_work_order")
    assert row["reviewer"]["approved"] is False and not row["applied"] and "aps-shadow" in row["shadow"]


def test_inline_and_shadow_edges_get_the_same_context(monkeypatch, tmp_path):
    shadow, inline = FakeEdge("aps-shadow", "permit"), FakeEdge("inline", "refuse")
    monkeypatch.setattr(demo, "SHADOW_EDGES", [shadow])
    monkeypatch.setattr(demo, "INLINE_EDGE", inline)
    monkeypatch.setattr(demo, "REVIEWER_MODEL", "rubber_stamp")
    row = demo.governed_write(FakeStack(assess_ok=True), SC, 3600, "2026-01-15", "ZONE-01", 19.0, 20.0, "plan",
                              "PLAN-2026-01-15", tmp_path, Counter(), "strategy:P1")
    assert not row["applied"] and row["inline_edge"]["verdict"] == "refuse"
    assert shadow.calls[0] == inline.calls[0]


def test_shadow_accounting_cells():
    def r(verdict, applied, shadow, available=True, latency=2.0):
        return {"applied": applied, "oracle": {"verdict": verdict} if verdict else None,
                "shadow": {"aps": {"verdict": shadow, "available": available, "latency_ms": latency}}}
    ledger = [r("forbidden", False, "refuse"), r("forbidden", True, "refuse"), r("forbidden", False, "permit"),
              r("forbidden", True, "permit"), r("permitted", True, "refuse"), r("permitted", False, "permit"),
              r("permitted", True, "permit"), r("forbidden", False, "refuse", available=False), r(None, True, "permit"),
              {"origin": "attack:summary", "applied": False}, {"applied": True}]
    s = ab_compare.shadow_stats(ledger)["aps"]
    assert s["asked"] == 9 and s["unavailable"] == 1 and s["unjudged"] == 1
    assert s["forbidden"] == {"both": 1, "shadow_only": 1, "governed_only": 1, "neither": 1}
    assert s["permitted"] == {"shadow_false_refusal": 1, "governed_false_refusal": 1, "n": 3}
    assert s["latency_ms"] == {"p50": 2.0, "p95": 2.0, "max": 2.0}
