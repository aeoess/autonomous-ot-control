"""OT-003: the governed runner proposes, never self-approves, and fails closed.

Uses a labeled in-test double for ``remora.sdk`` to exercise the runner's
decision handling. The double is NOT an authorization engine and lives only in
this test file: it returns a scripted decision so we can assert the runner's
control flow (dispatch only on ACCEPT via REMORA's token; review/refuse
otherwise; unknown execution outcome never retried).
"""
from __future__ import annotations

import pytest

from runner import governed
from runner.governed import (STATE_DISPATCHED, STATE_REFUSED, STATE_UNKNOWN, STATE_WAITING_APPROVAL,
                             SDKUnavailable, decide, propose_setpoint)


class FakeAction:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeAssessment:
    def __init__(self, action: str, token: str | None = "exec-token-1") -> None:
        self.action = FakeAction(action)
        self.execution_token = token


class FakeToolCall:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class FakeSDK:
    """Minimal stand-in for the remora.sdk symbols the runner touches."""
    ToolCall = FakeToolCall


class FakeClient:
    def __init__(self, assessment: FakeAssessment, *, execute_raises: bool = False) -> None:
        self._assessment = assessment
        self._execute_raises = execute_raises
        self.assessed: list = []
        self.executed: list = []

    def assess(self, call):
        self.assessed.append(call)
        return self._assessment

    def execute_accepted(self, token, call):
        if self._execute_raises:
            raise RuntimeError("timeout after send")
        self.executed.append((token, call))
        return {"status": "APPLIED", "operation_id": "remora-op-1"}


def _propose(client):
    return propose_setpoint(client, zone_id="ZONE-01", setpoint_c=20.5,
                            intent_ref="WORK-ORDER-001", sdk=FakeSDK())


@pytest.mark.parametrize("action,state", [
    ("ACCEPT", STATE_DISPATCHED),
    ("VERIFY", STATE_WAITING_APPROVAL),
    ("ESCALATE", STATE_WAITING_APPROVAL),
    ("REFUSE", STATE_REFUSED),
    ("ABSTAIN", STATE_REFUSED),
    ("SOMETHING_NEW", STATE_REFUSED),  # unmapped action fails closed
])
def test_decision_states(action, state):
    client = FakeClient(FakeAssessment(action))
    result = _propose(client)
    assert result["state"] == state
    # Only ACCEPT reaches execute_accepted, and exactly once.
    assert len(client.executed) == (1 if action == "ACCEPT" else 0)


def test_accept_dispatches_the_exact_call():
    client = FakeClient(FakeAssessment("ACCEPT"))
    result = _propose(client)
    assert result["state"] == STATE_DISPATCHED and "receipt" in result
    (token, call), = client.executed
    assert token == "exec-token-1"
    assert call.tool_name == "set_zone_setpoint"
    assert call.arguments == {"zone_id": "ZONE-01", "setpoint_c": 20.5}
    assert call.intent_ref == "WORK-ORDER-001"


def test_accept_without_token_refuses():
    client = FakeClient(FakeAssessment("ACCEPT", token=None))
    result = _propose(client)
    assert result["state"] == STATE_REFUSED and not client.executed


def test_execution_outcome_unknown_is_not_retried():
    client = FakeClient(FakeAssessment("ACCEPT"), execute_raises=True)
    result = _propose(client)
    assert result["state"] == STATE_UNKNOWN
    assert len(client.assessed) == 1  # proposed once; no blind re-execute


def test_review_actions_never_self_approve():
    client = FakeClient(FakeAssessment("VERIFY"))
    result = _propose(client)
    assert result["state"] == STATE_WAITING_APPROVAL and not client.executed


def test_decide_is_pure_and_defaults_closed():
    assert decide(FakeAssessment("ACCEPT")).dispatch is True
    assert decide(FakeAssessment("VERIFY")).dispatch is False
    assert decide(FakeAssessment("whatever")).dispatch is False


def test_missing_sdk_fails_closed(monkeypatch):
    # With no injected sdk and remora.sdk absent, the runner refuses rather than
    # inventing an authorization.
    import builtins
    real_import = builtins.__import__

    def no_remora(name, *a, **k):
        if name == "remora.sdk" or name.startswith("remora"):
            raise ImportError("no remora in this environment")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_remora)
    with pytest.raises(SDKUnavailable):
        governed._require_sdk()
