"""OT-003: the actuator effect primitive applies one effect and fails closed.

Driven against the real twin actuator app over a TestClient transport — no
REMORA needed to prove the effect path and the fail-closed paths.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from adapter.actuation import (ActuationError, ActuatorClient, EffectUnknown, apply_setpoint)
from adapter.contracts import ToolSpec
from tests.conftest import plant_fingerprint
from twin.api import build_actuator_app

OP = "remora-proposal-00000001"


@pytest.fixture
def spec() -> ToolSpec:
    return ToolSpec.load()


@pytest.fixture
def actuator(twin, tokens) -> ActuatorClient:
    return ActuatorClient("http://testserver", tokens.actuator,
                          client=TestClient(build_actuator_app(twin, tokens)))


def test_happy_path_applies_one_effect(twin, spec, actuator):
    rec = apply_setpoint(actuator, spec, operation_id=OP, expected_state_revision=twin.revision,
                         arguments={"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert rec["status"] == "APPLIED" and rec["new_setpoint_c"] == 20.5
    assert rec["operation_id"] == OP
    assert twin.revision == 1 and twin.zones["ZONE-01"].setpoint_c == 20.5


def test_operation_id_replay_is_single_effect(twin, spec, actuator):
    # A true replay re-presents the SAME bound command (same revision), so the
    # request digest matches and the twin returns the stored outcome.
    rev0 = twin.revision
    a = dict(operation_id=OP, expected_state_revision=rev0,
             arguments={"zone_id": "ZONE-01", "setpoint_c": 20.5})
    r1 = apply_setpoint(actuator, spec, **a)
    fp = plant_fingerprint(twin)
    r2 = apply_setpoint(actuator, spec, **a)
    assert r1["replayed"] is False and r2["replayed"] is True
    assert plant_fingerprint(twin) == fp and twin.revision == 1  # INV-04


@pytest.mark.parametrize("args", [
    {"zone_id": "ZONE-01", "setpoint_c": 25.0},     # outside signed bounds
    {"zone_id": "ZONE-99", "setpoint_c": 20.5},     # zone not in contract
    {"zone_id": "ZONE-01", "setpoint_c": 20.5, "x": 1},  # extra arg
])
def test_argument_violations_refuse_before_dispatch(twin, spec, actuator, args):
    fp = plant_fingerprint(twin)
    with pytest.raises(ActuationError, match="argument refused"):
        apply_setpoint(actuator, spec, operation_id=OP, expected_state_revision=twin.revision,
                       arguments=args)
    assert plant_fingerprint(twin) == fp and not twin.operations


def test_guard_409_raises_not_returns(twin, spec, actuator):
    # A stale expected revision is a local-guard refusal (HTTP 409). It must
    # raise, not return a receipt that reads like success (MEDIUM #6).
    with pytest.raises(ActuationError, match="guard_refused:revision_mismatch"):
        apply_setpoint(actuator, spec, operation_id=OP, expected_state_revision=999,
                       arguments={"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert twin.revision == 0 and twin.zones["ZONE-01"].setpoint_c != 20.5


def test_wrong_credential_raises(twin, spec, tokens):
    bad = ActuatorClient("http://testserver", "x" * 40,
                         client=TestClient(build_actuator_app(twin, tokens)))
    fp = plant_fingerprint(twin)
    with pytest.raises(ActuationError, match="rejected adapter credential"):
        apply_setpoint(bad, spec, operation_id=OP, expected_state_revision=twin.revision,
                       arguments={"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert plant_fingerprint(twin) == fp and not twin.operations


def test_unreachable_actuator_is_unknown_not_retry(twin, spec):
    class Dead:
        def post(self, *a, **k):
            raise httpx.ConnectError("refused")
    actuator = ActuatorClient("http://x", "z" * 40, client=Dead())
    with pytest.raises(EffectUnknown):
        apply_setpoint(actuator, spec, operation_id=OP, expected_state_revision=0,
                       arguments={"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert not twin.operations
