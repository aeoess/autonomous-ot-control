"""OT-003: the deployment-owned callable applies exactly one governed effect,
is idempotent, fails closed, and only the WRITE tool is actuator-backed.

The callable is exercised against the *real* twin actuator app over a TestClient
transport — no REMORA needed to prove the effect path and the fail-closed paths.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from adapter.contracts import ToolSpec
from adapter.registry import (ActuatorClient, AdapterError, EffectUnknown, ExecutionContext,
                              Registrar, register, set_zone_setpoint)
from tests.conftest import plant_fingerprint
from twin.api import build_actuator_app

OP = "remora-op-00000001"


@pytest.fixture
def spec() -> ToolSpec:
    return ToolSpec.load()


@pytest.fixture
def actuator(twin, tokens) -> ActuatorClient:
    client = TestClient(build_actuator_app(twin, tokens))
    return ActuatorClient("http://testserver", tokens.actuator, client=client)


def ctx(twin, spec, *, op=OP, rev=None, digest=None) -> ExecutionContext:
    return ExecutionContext(
        tenant_id="federation-demo", principal_id="operator-1", operation_id=op,
        toolspec_digest=digest if digest is not None else spec.digest,
        policy_version=spec.policy_version,
        expected_state_revision=twin.revision if rev is None else rev)


def test_happy_path_applies_one_effect(twin, spec, actuator):
    rec = set_zone_setpoint(ctx(twin, spec), {"zone_id": "ZONE-01", "setpoint_c": 20.5},
                            actuator=actuator, toolspec=spec)
    assert rec["status"] == "APPLIED" and rec["new_setpoint_c"] == 20.5
    assert rec["operation_id"] == OP and rec["tenant_id"] == "federation-demo"
    assert rec["toolspec_digest"] == spec.digest
    assert twin.revision == 1 and twin.zones["ZONE-01"].setpoint_c == 20.5


def test_operation_id_replay_is_single_effect(twin, spec, actuator):
    c = ctx(twin, spec)
    r1 = set_zone_setpoint(c, {"zone_id": "ZONE-01", "setpoint_c": 20.5}, actuator=actuator, toolspec=spec)
    fp = plant_fingerprint(twin)
    r2 = set_zone_setpoint(c, {"zone_id": "ZONE-01", "setpoint_c": 20.5}, actuator=actuator, toolspec=spec)
    assert r1["replayed"] is False and r2["replayed"] is True
    assert plant_fingerprint(twin) == fp and twin.revision == 1  # INV-04: no second effect


def test_digest_mismatch_refuses(twin, spec, actuator):
    fp = plant_fingerprint(twin)
    with pytest.raises(AdapterError, match="digest mismatch"):
        set_zone_setpoint(ctx(twin, spec, digest="sha256:" + "0" * 64),
                          {"zone_id": "ZONE-01", "setpoint_c": 20.5}, actuator=actuator, toolspec=spec)
    assert plant_fingerprint(twin) == fp and not twin.operations


@pytest.mark.parametrize("args", [
    {"zone_id": "ZONE-01", "setpoint_c": 25.0},     # outside signed bounds
    {"zone_id": "ZONE-99", "setpoint_c": 20.5},     # zone not in contract
    {"zone_id": "ZONE-01", "setpoint_c": 20.5, "x": 1},  # extra arg
])
def test_argument_violations_refuse_before_dispatch(twin, spec, actuator, args):
    fp = plant_fingerprint(twin)
    with pytest.raises(AdapterError, match="argument refused"):
        set_zone_setpoint(ctx(twin, spec), args, actuator=actuator, toolspec=spec)
    assert plant_fingerprint(twin) == fp and not twin.operations


def test_wrong_actuator_credential_refuses(twin, spec, tokens):
    client = TestClient(build_actuator_app(twin, tokens))
    bad = ActuatorClient("http://testserver", "x" * 40, client=client)  # not the actuator token
    fp = plant_fingerprint(twin)
    with pytest.raises(AdapterError, match="rejected adapter credential"):
        set_zone_setpoint(ctx(twin, spec), {"zone_id": "ZONE-01", "setpoint_c": 20.5},
                          actuator=bad, toolspec=spec)
    assert plant_fingerprint(twin) == fp and not twin.operations


def test_guard_rejection_is_reported_not_raised(twin, spec, actuator):
    # A stale expected revision is a local-guard refusal: the twin records a
    # REJECTED receipt and changes nothing; the callable reports it (no effect).
    rec = set_zone_setpoint(ctx(twin, spec, rev=999), {"zone_id": "ZONE-01", "setpoint_c": 20.5},
                            actuator=actuator, toolspec=spec)
    assert rec["status"] == "REJECTED" and rec["reason"] == "revision_mismatch"
    assert twin.revision == 0 and twin.zones["ZONE-01"].setpoint_c != 20.5


def test_unreachable_actuator_is_unknown_not_retry(twin, spec):
    class Dead:
        def post(self, *a, **k):
            raise httpx.ConnectError("refused")
    actuator = ActuatorClient("http://x", "z" * 40, client=Dead())
    with pytest.raises(EffectUnknown):
        set_zone_setpoint(ctx(twin, spec), {"zone_id": "ZONE-01", "setpoint_c": 20.5},
                          actuator=actuator, toolspec=spec)
    assert not twin.operations


def test_execution_context_rejects_agent_shaped_gaps(twin, spec):
    with pytest.raises(AdapterError):
        ExecutionContext(tenant_id="", principal_id="p", operation_id=OP,
                         toolspec_digest=spec.digest, policy_version="v", expected_state_revision=0)
    with pytest.raises(AdapterError):
        ExecutionContext(tenant_id="t", principal_id="p", operation_id=OP,
                         toolspec_digest=spec.digest, policy_version="v", expected_state_revision=-1)


# -- registration seam -------------------------------------------------------

class FakeRegistrar:
    """Test double for REMORA's deployment registrar (see Registrar protocol)."""
    def __init__(self) -> None:
        self.tools: dict[str, dict] = {}

    def register_tool(self, *, tool_name, effect, callable, spec) -> None:
        self.tools[tool_name] = {"effect": effect, "callable": callable, "spec": spec}


def test_register_exposes_only_write_as_actuator_backed(twin, spec, actuator):
    reg = FakeRegistrar()
    assert isinstance(reg, Registrar)
    names = register(reg, toolspec=spec, actuator=actuator)
    assert set(names) == {"read_zone_state", "read_building_energy", "set_zone_setpoint"}
    assert reg.tools["read_zone_state"]["callable"] is None
    assert reg.tools["read_building_energy"]["callable"] is None
    assert reg.tools["set_zone_setpoint"]["effect"] == "WRITE"
    assert reg.tools["set_zone_setpoint"]["spec"]["toolspec_digest"] == spec.digest
    # The registered write callable actually actuates through REMORA's context.
    rec = reg.tools["set_zone_setpoint"]["callable"](ctx(twin, spec),
                                                     {"zone_id": "ZONE-03", "setpoint_c": 20.5})
    assert rec["status"] == "APPLIED" and twin.revision == 1


def test_register_requires_actuator_for_write(spec):
    with pytest.raises(AdapterError, match="requires an actuator"):
        register(FakeRegistrar(), toolspec=spec, actuator=None)
