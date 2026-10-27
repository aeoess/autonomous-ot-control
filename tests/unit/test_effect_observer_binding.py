"""The observer binds an effect to the operation that claims it.

Finding (federation negative test, 2026-10-27): the observer accepted the
operation id of one applied operation as proof of a different effect whenever
the plant already showed the desired state. Reproduced with the wrong tool,
the wrong zone and the wrong value. A false effect confirmation, not an
actuator bypass: nothing was written, but EFFECT_VERIFIED was issued for an
effect the named operation never produced.

An operation record is proof only for its own tool, its own target and the
value it actually applied. Everything else is EFFECT_MISMATCH, whatever the
plant happens to show.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from runner.areas import CONFIG, scenario_path
from tests.conftest import TEST_ENV
from twin.api import Tokens, build_read_app
from twin.effect_observer import EFFECT_MISMATCH, EFFECT_VERIFIED, EffectObserver
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

COOL = "set_zone_cooling_setpoint"
HEAT = "set_zone_setpoint"


@pytest.fixture
def tw() -> Twin:
    raw = json.loads(scenario_path("NO1").read_text("utf-8"))
    raw["duration_seconds"] = 2 * 86400
    return Twin(load_config(CONFIG), load_scenario(raw, base_dir=scenario_path("NO1").parent))


@pytest.fixture
def observer(tw) -> EffectObserver:
    tokens = Tokens.from_env(TEST_ENV)
    return EffectObserver("http://testserver", tokens.read, tenant_id="ot-pilot", run_id="bind",
                          client=TestClient(build_read_app(tw, tokens)))


def plant(tw: Twin, op: str, tool: str, args: dict) -> None:
    rec, _ = tw.apply_plant_command({"operation_id": op, "tool_name": tool, "arguments": args,
                                     "expected_state_revision": tw.revision})
    assert rec.status == "APPLIED", f"{tool}({args}) rejected: {rec.reason}"


def heat(tw: Twin, op: str, zone: str, sp: float) -> None:
    rec, _ = tw.apply_setpoint({"operation_id": op, "zone_id": zone, "setpoint_c": sp, "units": "degC",
                                "expected_state_revision": tw.revision})
    assert rec.status == "APPLIED", f"setpoint {zone}={sp} rejected: {rec.reason}"


def cooling(tw: Twin, zone: str) -> float:
    return float(tw.setpoints.zones[zone].cooling_c)


def test_matching_operation_is_verified(tw, observer):
    target = cooling(tw, "ZONE-01") + 0.5
    plant(tw, "op-cool-1", COOL, {"zone_id": "ZONE-01", "cooling_c": target})
    env = observer.observe_tool("op-cool-1", tool_name=COOL, arguments={"zone_id": "ZONE-01", "cooling_c": target})
    assert env["effect_status"] == EFFECT_VERIFIED


def test_wrong_tool_operation_is_not_proof_even_if_the_state_already_exists(tw, observer):
    heat(tw, "op-heat-1", "ZONE-01", tw.zones["ZONE-01"].setpoint_c + 0.5)
    already = cooling(tw, "ZONE-01")  # the cooling setpoint is what it always was
    env = observer.observe_tool("op-heat-1", tool_name=COOL, arguments={"zone_id": "ZONE-01", "cooling_c": already})
    assert env["effect_status"] == EFFECT_MISMATCH
    assert env["detail"] == "operation_tool_mismatch"


def test_wrong_zone_operation_is_not_proof_even_if_the_state_already_exists(tw, observer):
    plant(tw, "op-cool-1", COOL, {"zone_id": "ZONE-01", "cooling_c": cooling(tw, "ZONE-01") + 0.5})
    already = cooling(tw, "ZONE-02")
    env = observer.observe_tool("op-cool-1", tool_name=COOL, arguments={"zone_id": "ZONE-02", "cooling_c": already})
    assert env["effect_status"] == EFFECT_MISMATCH
    assert env["detail"] == "operation_target_mismatch"


def test_wrong_value_operation_is_not_proof_of_a_later_value(tw, observer):
    base = cooling(tw, "ZONE-01")
    plant(tw, "op-cool-1", COOL, {"zone_id": "ZONE-01", "cooling_c": base + 0.5})
    tw.advance(3600)
    plant(tw, "op-cool-2", COOL, {"zone_id": "ZONE-01", "cooling_c": base + 1.0})
    env = observer.observe_tool("op-cool-1", tool_name=COOL, arguments={"zone_id": "ZONE-01", "cooling_c": base + 1.0})
    assert env["effect_status"] == EFFECT_MISMATCH
    assert env["detail"] == "recorded_value_differs"


def test_setpoint_path_rejects_a_plant_operation_as_proof(tw, observer):
    """A plant record carries zone_id and new_setpoint_c too; it is still not a heating effect.

    The interlock deadband keeps a cooling setpoint from equalling the heating
    setpoint in the same zone today, so the record is placed directly: the
    observer must bind on tool_name, not rely on the interlock."""
    from dataclasses import replace
    heat(tw, "op-heat-1", "ZONE-01", 22.0)
    rec = tw.operation("op-heat-1")
    tw.operations["op-cool-1"] = replace(rec, operation_id="op-cool-1", tool_name=COOL,
                                         requested_value=22.0, new_value=22.0)
    env = observer.observe("op-cool-1", zone_id="ZONE-01", expected_setpoint_c=22.0)
    assert env["effect_status"] == EFFECT_MISMATCH
    assert env["detail"] == "operation_tool_mismatch"
