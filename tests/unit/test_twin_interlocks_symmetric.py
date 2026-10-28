"""SDD-OT-002 SR-13 (ST-13): cross-point interlocks hold on the resulting state, whichever side is written.

Before the fix IL-01 (cooling >= heating + 2 K) was checked only when cooling was written, so heating could be
raised into the cooling setpoint (simultaneous heating and cooling); IL-09 (no limited heat pump while the tank
is more than 2 K short) was checked only when the limit was written, never when the tank setpoint was raised.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import cmd, plant_fingerprint
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]


def _hvac() -> Twin:
    return Twin(load_config(ROOT / "twin" / "fixtures" / "three-zone-hvac-v1.json"),
                load_scenario(ROOT / "scenarios" / "baseline-day-hvac-v1.json"))


def _plant(tw: Twin, op: str, tool: str, args: dict):
    return tw.apply_plant_command({"schema": "ot-plant-command/v1", "operation_id": op, "tool_name": tool,
                                   "arguments": args, "expected_state_revision": tw.revision})[0]


def test_st13_il01_heating_cannot_be_raised_into_the_cooling_setpoint():
    tw = _hvac()
    assert tw.zones["ZONE-01"].setpoint_c == 21.0 and tw.setpoints.zones["ZONE-01"].cooling_c == 24.0
    assert _plant(tw, "op-c-1", "set_zone_cooling_setpoint", {"zone_id": "ZONE-01", "cooling_c": 23.0}).status == "APPLIED"
    tw.advance(900)
    before = plant_fingerprint(tw)
    rec, _ = tw.apply_setpoint(cmd(tw, "op-h-1", zone="ZONE-01", sp=22.0))
    assert (rec.status, rec.reason) == ("REJECTED", "IL-01:deadband") and plant_fingerprint(tw) == before
    rec, _ = tw.apply_setpoint(cmd(tw, "op-h-2", zone="ZONE-01", sp=20.5))  # moving away from cooling is fine
    assert rec.status == "APPLIED"


def test_il01_the_boundary_and_the_cooling_side_still_hold():
    tw = _hvac()
    _plant(tw, "op-c-1", "set_zone_cooling_setpoint", {"zone_id": "ZONE-01", "cooling_c": 23.0})
    tw.advance(900)
    rec, _ = tw.apply_setpoint(cmd(tw, "op-h-1", zone="ZONE-01", sp=21.0))
    assert rec.reason == "no_change"  # 21.0 is exactly 2 K below 23.0: allowed (and already there)
    tw.advance(900)
    rec = _plant(tw, "op-c-2", "set_zone_cooling_setpoint", {"zone_id": "ZONE-01", "cooling_c": 22.5})
    assert (rec.status, rec.reason) == ("REJECTED", "IL-01:deadband")  # the cooling-side check is unchanged


def test_il01_does_not_apply_to_a_twin_without_a_plant(twin):
    rec, _ = twin.apply_setpoint(cmd(twin, "op-np-1", zone="ZONE-01", sp=21.5))
    assert rec.status == "APPLIED"


def test_st13_il09_a_tank_raise_with_a_limited_heat_pump_is_refused():
    tw = _hvac()
    tank_setpoint, tank_temp = tw.setpoints.tank_c, tw.plant_state.tank_c
    assert tank_temp >= tank_setpoint - 2.0
    assert _plant(tw, "op-hp-1", "set_heat_pump_limit", {"max_fraction": 0.5}).status == "APPLIED"
    tw.advance(900)
    target = tank_setpoint + 3.0  # within the 3 K step, but more than 2 K above the tank's temperature
    assert tw.plant_state.tank_c < target - 2.0
    before = tw.revision
    rec = _plant(tw, "op-t-1", "set_tank_setpoint", {"tank_c": target})
    assert (rec.status, rec.reason) == ("REJECTED", "IL-09:tank_short") and tw.revision == before
    small = tank_setpoint + 0.5
    if tw.plant_state.tank_c >= small - 2.0:
        assert _plant(tw, "op-t-2", "set_tank_setpoint", {"tank_c": small}).status == "APPLIED"


def test_il09_an_unlimited_heat_pump_may_raise_the_tank():
    tw = _hvac()
    assert tw.setpoints.hp_max_fraction == 1.0
    rec = _plant(tw, "op-t-1", "set_tank_setpoint", {"tank_c": tw.setpoints.tank_c + 3.0})
    assert rec.status == "APPLIED"
