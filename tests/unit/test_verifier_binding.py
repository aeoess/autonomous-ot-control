"""Finding 1 (after PR #20), SDK-independent half: a binding refusal survives the attestor.

The labelled fake SDK here answers VERIFIED to anything it is asked to
compare, which is exactly what the real SDK does when the plant's current
value happens to equal the claim. The attestor must therefore not ask it at
all when the observer has refused to bind the record to the claim, and must
not hand the recording boundary anything a server could recompute VERIFIED
from. tests/sdk/test_attestor_binding_sdk.py repeats this with the real SDK.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adapter.verifier import EffectAttestor
from runner.areas import CONFIG, scenario_path
from tests.conftest import TEST_ENV
from twin.api import Tokens, build_read_app
from twin.effect_observer import EffectObserver
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

COOL = "set_zone_cooling_setpoint"


class AlwaysVerifiedSDK:
    """Compares nothing: says VERIFIED whenever asked, like the real SDK on a matching value."""

    def __init__(self) -> None:
        self.verify_calls: list[dict[str, Any]] = []

    def build_postcondition(self, **kw):
        return {"spec": kw}

    def verify_effect(self, spec, observed, **kw):
        self.verify_calls.append({"observed": observed, **kw})
        status = "EFFECT_UNOBSERVABLE" if observed is None else "EFFECT_VERIFIED"
        return _View({"status": status, "reason_code": "", **kw})


class _View:
    def __init__(self, p: dict[str, Any]) -> None:
        self._p = p
        self.raw = p

    def to_dict(self) -> dict[str, Any]:
        return dict(self._p)


class CapturingClient:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, Any]] = []

    def record_effect(self, proposal_id, view):
        self.recorded.append((proposal_id, view))
        return view


@pytest.fixture
def tw() -> Twin:
    raw = json.loads(scenario_path("NO1").read_text("utf-8"))
    raw["duration_seconds"] = 2 * 86400
    return Twin(load_config(CONFIG), load_scenario(raw, base_dir=scenario_path("NO1").parent))


@pytest.fixture
def fake() -> AlwaysVerifiedSDK:
    return AlwaysVerifiedSDK()


@pytest.fixture
def attestor(tw, fake) -> EffectAttestor:
    tokens = Tokens.from_env(TEST_ENV)
    obs = EffectObserver("http://testserver", tokens.read, tenant_id="ot-pilot", run_id="fake",
                         client=TestClient(build_read_app(tw, tokens)))
    return EffectAttestor(obs, sdk=fake)


def plant(tw: Twin, op: str, tool: str, args: dict) -> None:
    rec, _ = tw.apply_plant_command({"operation_id": op, "tool_name": tool, "arguments": args,
                                     "expected_state_revision": tw.revision})
    assert rec.status == "APPLIED", rec.reason


def test_binding_refusal_is_not_handed_to_the_sdk_for_comparison(tw, attestor, fake):
    plant(tw, "op-c1", COOL, {"zone_id": "ZONE-01", "cooling_c": tw.setpoints.zones["ZONE-01"].cooling_c + 0.5})
    client = CapturingClient()
    r = attestor.attest(proposal_id="p", execution_id="e", toolspec_hash="ts", zone_id="ZONE-01",
                        expected_setpoint_c=tw.zones["ZONE-01"].setpoint_c, operation_id="op-c1", client=client)
    assert r["observation"]["effect_status"] == "EFFECT_MISMATCH"
    assert r["verification"]["status"] == "EFFECT_MISMATCH"
    assert r["verification"]["reason_code"] == "operation_tool_mismatch"
    assert fake.verify_calls == [], "a refused binding must never be re-decided by comparing values"
    (_, view), = client.recorded
    assert view.to_dict()["status"] == "EFFECT_MISMATCH" and "observed" not in view.to_dict()


def test_attest_tool_binding_refusal_is_not_handed_to_the_sdk(tw, attestor, fake):
    plant(tw, "op-c1", COOL, {"zone_id": "ZONE-01", "cooling_c": tw.setpoints.zones["ZONE-01"].cooling_c + 0.5})
    r = attestor.attest_tool(proposal_id="p", execution_id="e", toolspec_hash="ts", tool_id=COOL,
                             arguments={"zone_id": "ZONE-02", "cooling_c": tw.setpoints.zones["ZONE-02"].cooling_c},
                             operation_id="op-c1")
    assert r["verification"]["status"] == "EFFECT_MISMATCH"
    assert r["verification"]["reason_code"] == "operation_target_mismatch"
    assert fake.verify_calls == []


def test_plant_mismatch_and_unobservable_still_go_through_the_sdk(tw, attestor, fake, monkeypatch):
    import httpx
    rec, _ = tw.apply_setpoint({"operation_id": "op-h1", "zone_id": "ZONE-01", "setpoint_c": 21.5, "units": "degC",
                                "expected_state_revision": tw.revision})
    assert rec.status == "APPLIED"
    r = attestor.attest(proposal_id="p", execution_id="e", toolspec_hash="ts", zone_id="ZONE-01",
                        expected_setpoint_c=21.5, operation_id="op-h1")
    assert r["verification"]["status"] == "EFFECT_VERIFIED" and len(fake.verify_calls) == 1
    monkeypatch.setattr(attestor._observer, "_get", lambda path: (_ for _ in ()).throw(httpx.ConnectError("down")))
    r = attestor.attest(proposal_id="p", execution_id="e", toolspec_hash="ts", zone_id="ZONE-01",
                        expected_setpoint_c=21.5, operation_id="op-h1")
    assert r["verification"]["status"] == "EFFECT_UNOBSERVABLE" and fake.verify_calls[-1]["observed"] is None
