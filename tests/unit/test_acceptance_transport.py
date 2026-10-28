"""SDD-OT-002 SR-10 (ST-10): a transport failure is never a refusal.

AT-08, AT-08b and AT-11 credit REMORA with a refusal when a call does not succeed. With REMORA down every
call fails without an HTTP status, and before this fix all three reported PASS. Driven offline against a
stand-in stack whose twin answers and whose REMORA does not: no case may PASS, and every REMORA call that
went unanswered is recorded. A stack whose REMORA does answer with a refusal still lets the case pass.
"""
from __future__ import annotations

import pytest

from runner import acceptance as acc


class DownStack(acc.Stack):
    """The twin answers (status, zone); every REMORA call fails in transport, as when REMORA is stopped."""

    def __init__(self, remora_answers: dict | None = None):
        super().__init__(compose=[])
        self.remora_answers = remora_answers

    def step(self, op, **args):
        if op == "status":
            out = {"ok": True, "status": 200, "body": {"state_revision": 5}}
        elif op == "zone":
            out = {"ok": True, "status": 200, "body": {"setpoint_c": 20.0}}
        elif self.remora_answers is not None:
            out = self.remora_answers
        else:
            out = {"ok": False, "status": None, "error": "ConnectError"}
        self.trace.append({"op": op, "args": args, "result": out})
        if op in acc.REMORA_OPS and not acc.answered(out):
            self.no_answer.append({"op": op, "error": out.get("error")})
        return out

    def ctl(self, *args):
        return {"ok": True}

    def service(self, action, name):
        return None

    def wait_ready(self, want, timeout_s=90):
        return 0.0


CASES = [c for c in acc.CASES if c[0] in ("AT-08", "AT-08b", "AT-11")]


def test_st10_with_remora_down_no_refusal_case_passes():
    assert [c[0] for c in CASES] == ["AT-08", "AT-08b", "AT-11"]
    s = DownStack()
    results = acc.run_cases(acc.Ctx(s), CASES)
    assert [r["outcome"] for r in results] == ["FAIL", "FAIL", "FAIL"], results
    assert s.no_answer and all(m["error"] == "ConnectError" for m in s.no_answer)


class OutageStack(DownStack):
    """REMORA answers until the store is stopped, then does not answer at all (it went down with it)."""

    def __init__(self):
        super().__init__()
        self.down = False

    def service(self, action, name):
        self.down = action == "stop"

    def step(self, op, **args):
        if op in acc.REMORA_OPS and not self.down:
            out = {"assess": {"ok": True, "status": 200, "body": {"review_item_id": "item-1", "proposal_id": "p-1"}},
                   "approve": {"ok": True, "status": 200, "body": {"approved": True}}}.get(
                op, {"ok": False, "status": 409, "body": {"detail": "refused"}})
            self.trace.append({"op": op, "args": args, "result": out})
            return out
        return super().step(op, **args)


def test_st10_at11_does_not_credit_an_unanswered_call_during_the_outage():
    """Before the fix: assess and execute during the outage failed in transport, counted as refusals: PASS."""
    s = OutageStack()
    (r,) = acc.run_cases(acc.Ctx(s), [c for c in acc.CASES if c[0] == "AT-11"])
    assert r["outcome"] == "FAIL" and "AT-11 assess during the outage" in r["detail"], r
    assert {m["op"] for m in s.no_answer} == {"assess", "execute"}


def test_a_real_refusal_still_counts():
    """REMORA answers 409 with a decision body: AT-08's probes are refused, and the case passes."""
    s = DownStack(remora_answers={"ok": False, "status": 409, "body": {"detail": "contract_violation"}})
    (r,) = acc.run_cases(acc.Ctx(s), [c for c in acc.CASES if c[0] == "AT-08"])
    assert r["outcome"] == "PASS" and s.no_answer == []


@pytest.mark.parametrize("r, ok", [({"status": None, "error": "ReadTimeout"}, False), ({"status": 503, "body": {}}, True),
                                   ({"status": 409}, False), ({"status": "409", "body": {}}, False)])
def test_answered(r, ok):
    assert acc.answered(r) is ok
