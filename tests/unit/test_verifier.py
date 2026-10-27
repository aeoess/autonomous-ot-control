"""OT-003: the effect attestor uses the real remora.sdk effect signatures.

Injects a labeled fake SDK (not an authorization engine) to assert the attestor
calls build_postcondition / verify_effect / record_effect with the verified
argument shapes, and that an unobservable effect is handed to the SDK as
``observed=None`` rather than a guessed value.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from adapter.verifier import TOOL_ID, EffectAttestor
from tests.conftest import cmd
from twin.api import build_read_app
from twin.effect_observer import EffectObserver

OP = "remora-proposal-effect-1"


class FakeView:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._p = payload
    def to_dict(self) -> dict[str, Any]:
        return self._p


class FakeSDK:
    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}
    def build_postcondition(self, **kw):
        self.calls["build_postcondition"] = kw
        return {"spec": kw}
    def verify_effect(self, spec, observed, **kw):
        self.calls["verify_effect"] = {"spec": spec, "observed": observed, **kw}
        return FakeView({"status": "VERIFIED", "observed": observed, **kw})


class FakeClient:
    def __init__(self) -> None:
        self.recorded: list = []
    def record_effect(self, proposal_id, view):
        self.recorded.append((proposal_id, view))
        return view


@pytest.fixture
def observer(twin, tokens) -> EffectObserver:
    return EffectObserver("http://testserver", tokens.read, tenant_id="ot-pilot", run_id="r1",
                          client=TestClient(build_read_app(twin, tokens)))


def test_attest_uses_real_sdk_signatures(twin, tokens, observer):
    twin.apply_setpoint(cmd(twin, OP, zone="ZONE-01", sp=20.5))  # make the effect real
    sdk, client = FakeSDK(), FakeClient()
    attestor = EffectAttestor(observer, sdk=sdk)
    out = attestor.attest(proposal_id="P1", execution_id="E1", toolspec_hash="TS",
                          zone_id="ZONE-01", expected_setpoint_c=20.5, operation_id=OP,
                          client=client)
    bp = sdk.calls["build_postcondition"]
    assert bp["tool_id"] == TOOL_ID and bp["target_selector"] == {"zone_id": "ZONE-01"}
    assert bp["expected_fields"] == {"setpoint_c": 20.5}
    ve = sdk.calls["verify_effect"]
    assert ve["observed"] == {"setpoint_c": 20.5}  # observed delta, not a guess
    assert ve["proposal_id"] == "P1" and ve["execution_id"] == "E1"
    assert ve["toolspec_hash"] == "TS"
    assert out["remora_recorded"] is True and client.recorded == [("P1", client.recorded[0][1])]


def test_unobservable_is_passed_as_none(twin, tokens, observer):
    sdk, client = FakeSDK(), FakeClient()
    attestor = EffectAttestor(observer, sdk=sdk)
    # No operation record for this id -> observer returns EFFECT_UNOBSERVABLE.
    attestor.attest(proposal_id="P2", execution_id="E2", toolspec_hash="TS",
                    zone_id="ZONE-01", expected_setpoint_c=20.5, operation_id="never-applied-1",
                    client=client)
    assert sdk.calls["verify_effect"]["observed"] is None  # UNOBSERVABLE != MISMATCH


def test_no_sdk_records_nothing(observer, monkeypatch):
    # sdk=None means "load the installed SDK"; make it absent explicitly, so the
    # test does not depend on whether remora happens to be installed here.
    import adapter.verifier as verifier

    monkeypatch.setattr(verifier, "_load_sdk", lambda: None)
    attestor = EffectAttestor(observer, sdk=None)
    out = attestor.attest(proposal_id="P3", execution_id="E3", toolspec_hash="TS",
                          zone_id="ZONE-01", expected_setpoint_c=20.5, operation_id="x1")
    assert out["remora_recorded"] is False and out["verification"] is None
