"""Finding 1 (after PR #20): the attestor must not upgrade an observer refusal.

Run with the real remora.sdk at the pinned commit (see conftest.py). The
observer refuses a record that belongs to another tool, zone or value;
these tests show that refusal surviving EffectAttestor.attest / attest_tool
and the recording boundary, instead of the SDK recomputing EFFECT_VERIFIED
from the plant's coincidentally matching current value.

No REMORA server is involved: remora_recorded stays False, and the recording
boundary is checked with a capturing client. Server acceptance of an attested
MISMATCH is out of scope here.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import remora.sdk as sdk
from adapter.verifier import EffectAttestor
from runner.areas import CONFIG, scenario_path
from tests.conftest import TEST_ENV
from twin.api import Tokens, build_read_app
from twin.effect_observer import EffectObserver
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

COOL = "set_zone_cooling_setpoint"
HEAT = "set_zone_setpoint"
TS = "toolspec-sha256-test"


class CapturingClient:
    """Stands in for RemoraClient.record_effect: keeps what would have been sent."""

    def __init__(self) -> None:
        self.recorded: list[tuple[str, Any]] = []

    def record_effect(self, proposal_id: str, view: Any) -> Any:
        self.recorded.append((proposal_id, view))
        return view


@pytest.fixture
def tw() -> Twin:
    raw = json.loads(scenario_path("NO1").read_text("utf-8"))
    raw["duration_seconds"] = 2 * 86400
    return Twin(load_config(CONFIG), load_scenario(raw, base_dir=scenario_path("NO1").parent))


@pytest.fixture
def attestor(tw) -> EffectAttestor:
    tokens = Tokens.from_env(TEST_ENV)
    obs = EffectObserver("http://testserver", tokens.read, tenant_id="ot-pilot", run_id="sdk",
                         client=TestClient(build_read_app(tw, tokens)))
    return EffectAttestor(obs, sdk=sdk)


def heat(tw: Twin, op: str, zone: str, sp: float) -> None:
    rec, _ = tw.apply_setpoint({"operation_id": op, "zone_id": zone, "setpoint_c": sp, "units": "degC",
                                "expected_state_revision": tw.revision})
    assert rec.status == "APPLIED", rec.reason


def plant(tw: Twin, op: str, tool: str, args: dict) -> None:
    rec, _ = tw.apply_plant_command({"operation_id": op, "tool_name": tool, "arguments": args,
                                     "expected_state_revision": tw.revision})
    assert rec.status == "APPLIED", rec.reason


def attest_heat(att: EffectAttestor, op: str, zone: str, sp: float, client=None) -> dict:
    return att.attest(proposal_id=f"prop-{op}", execution_id=f"exec-{op}", toolspec_hash=TS,
                      zone_id=zone, expected_setpoint_c=sp, operation_id=op, client=client)


def test_the_real_sdk_is_in_use():
    assert sdk.__file__ and hasattr(sdk, "verify_effect") and hasattr(sdk, "build_postcondition")


def test_correct_operation_is_verified(tw, attestor):
    heat(tw, "op-h1", "ZONE-01", 21.5)
    r = attest_heat(attestor, "op-h1", "ZONE-01", 21.5)
    assert r["observation"]["effect_status"] == "EFFECT_VERIFIED"
    assert r["verification"]["status"] == "EFFECT_VERIFIED"


def test_cooling_operation_cannot_attest_an_existing_heating_setpoint(tw, attestor):
    plant(tw, "op-c1", COOL, {"zone_id": "ZONE-01", "cooling_c": tw.setpoints.zones["ZONE-01"].cooling_c + 0.5})
    existing = tw.zones["ZONE-01"].setpoint_c
    r = attest_heat(attestor, "op-c1", "ZONE-01", existing)
    assert r["observation"]["effect_status"] == "EFFECT_MISMATCH"
    assert r["observation"]["detail"] == "operation_tool_mismatch"
    assert r["verification"]["status"] == "EFFECT_MISMATCH"
    assert r["verification"]["reason_code"] == "operation_tool_mismatch"


def test_zone_01_operation_cannot_attest_zone_02s_existing_value(tw, attestor):
    heat(tw, "op-h1", "ZONE-01", tw.zones["ZONE-01"].setpoint_c + 0.5)
    existing = tw.zones["ZONE-02"].setpoint_c
    r = attest_heat(attestor, "op-h1", "ZONE-02", existing)
    assert r["observation"]["effect_status"] == "EFFECT_MISMATCH"
    assert r["verification"]["status"] == "EFFECT_MISMATCH"
    assert r["verification"]["reason_code"] == "zone_mismatch"


def test_first_operation_cannot_attest_a_later_value(tw, attestor):
    heat(tw, "op-h1", "ZONE-01", 21.5)
    tw.advance(3600)
    heat(tw, "op-h2", "ZONE-01", 21.0)  # a later, different value inside the IL-01 deadband
    r = attest_heat(attestor, "op-h1", "ZONE-01", 21.0)
    assert r["observation"]["effect_status"] == "EFFECT_MISMATCH"
    assert r["verification"]["status"] == "EFFECT_MISMATCH"
    assert r["verification"]["reason_code"] == "recorded_setpoint_differs"


def test_a_genuinely_wrong_current_value_is_a_mismatch(tw, attestor):
    heat(tw, "op-h1", "ZONE-01", 21.5)
    tw.advance(3600)
    heat(tw, "op-h2", "ZONE-01", 21.0)
    r = attest_heat(attestor, "op-h1", "ZONE-01", 21.5)  # op-h1 did apply 21.5; the plant moved on
    assert r["observation"]["effect_status"] == "EFFECT_MISMATCH"
    assert r["observation"]["detail"] == "superseded_by_later_operation"
    assert r["verification"]["status"] == "EFFECT_MISMATCH"


def test_an_unreachable_reading_is_unobservable_not_mismatch(tw, attestor, monkeypatch):
    import httpx
    heat(tw, "op-h1", "ZONE-01", 21.5)
    monkeypatch.setattr(attestor._observer, "_get",
                        lambda path: (_ for _ in ()).throw(httpx.ConnectError("down")))
    r = attest_heat(attestor, "op-h1", "ZONE-01", 21.5)
    assert r["observation"]["effect_status"] == "EFFECT_UNOBSERVABLE"
    assert r["verification"]["status"] == "EFFECT_UNOBSERVABLE"


def test_recording_boundary_never_records_a_binding_refusal_as_verified(tw, attestor):
    plant(tw, "op-c1", COOL, {"zone_id": "ZONE-01", "cooling_c": tw.setpoints.zones["ZONE-01"].cooling_c + 0.5})
    client = CapturingClient()
    r = attest_heat(attestor, "op-c1", "ZONE-01", tw.zones["ZONE-01"].setpoint_c, client=client)
    assert r["remora_recorded"] is True
    (pid, view), = client.recorded
    body = view.to_dict()
    assert body["status"] == "EFFECT_MISMATCH" and body["reason_code"] == "operation_tool_mismatch"
    # Nothing in what is recorded lets a server recompute VERIFIED from the plant's current value.
    assert "observed" not in body and "observed" not in getattr(view, "raw", {})


def test_attest_tool_keeps_the_refusal_too(tw, attestor):
    base = tw.setpoints.zones["ZONE-01"].cooling_c
    plant(tw, "op-c1", COOL, {"zone_id": "ZONE-01", "cooling_c": base + 0.5})
    existing_z2 = tw.setpoints.zones["ZONE-02"].cooling_c
    r = attestor.attest_tool(proposal_id="p", execution_id="e", toolspec_hash=TS, tool_id=COOL,
                             arguments={"zone_id": "ZONE-02", "cooling_c": existing_z2}, operation_id="op-c1")
    assert r["observation"]["effect_status"] == "EFFECT_MISMATCH"
    assert r["observation"]["detail"] == "operation_target_mismatch"
    assert r["verification"]["status"] == "EFFECT_MISMATCH"
    assert r["verification"]["reason_code"] == "operation_target_mismatch"
