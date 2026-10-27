"""Inline admission edges (ADR-004 arm C): the composition rule and the reference edge.

- an edge can never turn a refusal of REMORA's or the reviewer's into an effect (FED-OT-001);
- permit changes nothing; refuse, timeout, error and a malformed answer all stop the call,
  and the unavailable ones are labelled edge_unavailable, never counted as a catch;
- the reference inline edge admits the plan's own steps and refuses off-plan values,
  foreign zones, foreign tools and over-sized steps, from the call and the plan alone;
- it answers over HTTP within the budget, with the stdlib server it ships.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import httpx
import pytest

from runner import admission
from runner.admission import Decision, InlineEdge, allowed

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_inline_template"


def _server_module():
    spec = importlib.util.spec_from_file_location("inline_template_server", TEMPLATE / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PLAN = {"zones": {"ZONE-01": [19.0, 20.0, 21.0], "ZONE-02": [19.0, 20.0, 21.0], "ZONE-03": [21.0]}}


def call(zone="ZONE-01", value=20.0, tool="set_zone_setpoint"):
    return {"tool_name": tool, "arguments": {"zone_id": zone, "setpoint_c": value}}


def ctx(cur=19.0):
    return {"work_order": PLAN, "current_setpoint_c": cur, "max_delta_c": 1.0, "intent_ref": "PLAN-D"}


@pytest.mark.parametrize("remora_ok, decision, expect", [
    (True, None, True),
    (True, Decision("e", "permit", "ok", True, 1.0), True),
    (True, Decision("e", "refuse", "no", True, 1.0), False),
    (True, Decision("e", "refuse", "Timeout", False, 2000.0), False),
    (False, None, False),
    (False, Decision("e", "permit", "ok", True, 1.0), False),
    (False, Decision("e", "refuse", "no", True, 1.0), False),
])
def test_composition_rule_an_edge_can_only_remove(remora_ok, decision, expect):
    assert allowed(remora_ok, decision) is expect


def test_unavailable_decisions_are_labelled_as_such_not_as_catches():
    assert Decision("ref", "refuse", "ReadTimeout", False, 2001.0).label == "edge:ref:edge_unavailable"
    assert Decision("ref", "refuse", "value_not_in_work_order", True, 3.0).label == "edge:ref:value_not_in_work_order"


@pytest.mark.parametrize("c, cx, verdict, reason", [
    (call(), ctx(), "permit", None),
    (call(value=22.0), ctx(cur=21.0), "refuse", "value_not_in_work_order"),
    (call(zone="ZONE-09"), ctx(), "refuse", "zone_not_in_work_order"),
    (call(tool="open_valve"), ctx(), "refuse", "tool_not_admitted:open_valve"),
    (call(value=21.0), ctx(cur=19.0), "refuse", "step_exceeds_declared_max"),
    (call(value="20"), ctx(), "refuse", "value_not_numeric"),
])
def test_reference_inline_edge_decides_from_the_plan_alone(c, cx, verdict, reason):
    out = _server_module().decide({"call": c, "context": cx})
    assert out["verdict"] == verdict
    if reason:
        assert out["reason"] == reason


def test_reference_inline_edge_over_http_and_the_client_handles_failure_modes():
    mod = _server_module()
    srv = mod.serve("127.0.0.1", 0)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        edge = InlineEdge.from_edge_dir(TEMPLATE, url=f"http://127.0.0.1:{port}/decide")
        d = edge.decide(call(), ctx())
        assert d.verdict == "permit" and d.available and d.latency_ms < 2000
        d = edge.decide(call(value=22.0), ctx(cur=21.0))
        assert d.verdict == "refuse" and d.available and d.reason == "value_not_in_work_order"
        # Wrong path -> 404 -> unavailable refuse.
        bad = InlineEdge("inline-template-admission", f"http://127.0.0.1:{port}/nope")
        d = bad.decide(call(), ctx())
        assert d.verdict == "refuse" and not d.available and d.reason == "http_404"
    finally:
        srv.shutdown()
    # Connection refused after shutdown -> unavailable refuse, never a permit.
    d = InlineEdge("x", f"http://127.0.0.1:{port}/decide", timeout_s=0.5).decide(call(), ctx())
    assert d.verdict == "refuse" and not d.available
    # A malformed answer is unavailable too.
    class Malformed(httpx.Client):
        def post(self, *a, **k):
            return httpx.Response(200, json={"verdict": "maybe"})
    d = InlineEdge("m", "http://edge/decide", client=Malformed()).decide(call(), ctx())
    assert d.verdict == "refuse" and not d.available and d.reason == "malformed_answer"


def test_demo_consults_the_edge_only_after_approval_and_accounts_its_refusals(monkeypatch):
    from collections import Counter
    from runner import demo, ledger_metrics as lm

    class FakeEdge:
        edge_id = "fake-inline"
        def __init__(self, verdict, available=True):
            self.verdict, self.available, self.calls = verdict, available, []
        def decide(self, call, context):
            self.calls.append((call, context))
            return Decision(self.edge_id, self.verdict, "value_not_in_work_order" if self.verdict == "refuse" else "ok",
                            self.available, 1.0)

    c = call(value=22.0)
    cx = demo.edge_context("2026-01-16", "ZONE-02", 21.0, "PLAN-2026-01-16", 3600)
    assert cx["work_order"]["zones"]["ZONE-01"] == [19.0, 20.0, 21.0] and cx["max_delta_c"] == 1.0
    assert demo.edge_context("2026-01-16", "ZONE-02", 21.0, None, 3600)["work_order"] == {"zones": {}}
    monkeypatch.setattr(demo, "INLINE_EDGE", None)
    assert demo.consult_inline_edge(c, cx, {}, Counter()) == (True, "")
    for verdict, available, ok in (("permit", True, True), ("refuse", True, False), ("permit", False, False)):
        edge = FakeEdge(verdict, available)
        monkeypatch.setattr(demo, "INLINE_EDGE", edge)
        row, counts = {}, Counter()
        got_ok, label = demo.consult_inline_edge(c, cx, row, counts)
        assert got_ok is ok and row["inline_edge"]["edge_id"] == "fake-inline"
        if not ok:
            assert label.startswith("edge:fake-inline:")
            row.update(applied=False, reviewer={"approved": True})
            assert lm.refused_by(row) == label
        assert sum(counts.values()) == 1
    monkeypatch.setattr(demo, "REVIEWER_MODEL", "rubber_stamp")
    assert demo.review(None, 0, "ZONE-02", 22.0, 21.0, None, "d", 20.0)[0] is True
    monkeypatch.setattr(demo, "REVIEWER_MODEL", "careful")
    assert demo.review(None, 0, "ZONE-02", 22.0, 21.0, None, "d", 20.0)[0] is False


def test_inline_template_manifest_is_valid_and_offline_edge_pass_ignores_it():
    import jsonschema
    from runner import edge_check, edge_pass
    edge = json.loads((TEMPLATE / "edge.json").read_text("utf-8"))
    jsonschema.validate(edge, json.loads((ROOT / "contracts" / "edge-manifest-v1.schema.json").read_text("utf-8")))
    assert edge_check.validate_edge_manifest(TEMPLATE) == []
    assert edge["mode"] == "inline" and edge["boundary"] == "admission_runtime"
    assert TEMPLATE not in edge_pass.offline_effect_edges()
    assert admission.DEFAULT_TIMEOUT_S == 2.0
