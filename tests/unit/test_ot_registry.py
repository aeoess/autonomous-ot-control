"""OT-003: the deployment-owned REMORA tool registry (deploy/ot/ot_registry.py).

Verifies the module matches REMORA's real registration contract — it exposes
``register_tools(register)`` and registers ``fn(arguments)->dict`` callables —
and that the write callable applies exactly one governed effect, bound to the
injected REMORA proposal id. The module is loaded by path because it is a
deployment module (copied into the REMORA image), not an installed package. It
imports only REMORA-free helpers, so this runs offline.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapter.actuation import ActuationError, ActuatorClient
from adapter.contracts import ToolSpec
from tests.conftest import plant_fingerprint
from twin.api import build_actuator_app, build_read_app

ROOT = Path(__file__).resolve().parents[2]
# REMORA proposal ids are UUIDs (observed on the live governed run).
PROPOSAL = "7912188c-60d5-4d00-8b26-ff4d6c03e67a"


def _load_module():
    path = ROOT / "deploy" / "ot" / "ot_registry.py"
    spec = importlib.util.spec_from_file_location("ot_registry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def otreg():
    return _load_module()


@pytest.fixture
def tools(otreg, twin, tokens):
    read_client = TestClient(build_read_app(twin, tokens))
    actuator = ActuatorClient("http://testserver", tokens.actuator,
                              client=TestClient(build_actuator_app(twin, tokens)))
    return otreg.OtTools(actuator=actuator, read_base_url="http://testserver",
                         read_token=tokens.read, toolspec=ToolSpec.load(),
                         read_client=read_client, proposal_id=lambda: PROPOSAL)


def test_register_tools_matches_remora_contract(otreg, tools, monkeypatch):
    from adapter.contracts import ALL_WRITE_TOOLS, READ_TOOLS

    monkeypatch.setattr(otreg, "_TOOLS", tools)
    registered: dict[str, object] = {}
    otreg.register_tools(lambda name, fn: registered.__setitem__(name, fn))
    assert set(registered) == set(READ_TOOLS) | set(ALL_WRITE_TOOLS)
    # Every registered callable takes a single `arguments` dict (fn(arguments)->dict).
    out = registered["read_zone_state"]({"zone_id": "ZONE-01"})
    assert out["zone_id"] == "ZONE-01" and "setpoint_c" in out


def test_write_callable_applies_one_effect_bound_to_proposal(tools, twin):
    rec = tools.set_zone_setpoint({"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert rec["status"] == "APPLIED" and rec["operation_id"] == PROPOSAL
    assert twin.revision == 1 and twin.zones["ZONE-01"].setpoint_c == 20.5
    # Re-invocation of the same proposal is idempotent (INV-04).
    fp = plant_fingerprint(twin)
    rec2 = tools.set_zone_setpoint({"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert rec2["replayed"] is True and plant_fingerprint(twin) == fp and twin.revision == 1


def test_write_callable_fails_closed_on_bad_argument(tools, twin):
    with pytest.raises(ActuationError):
        tools.set_zone_setpoint({"zone_id": "ZONE-01", "setpoint_c": 25.0})
    assert twin.revision == 0 and not twin.operations


def test_read_tools(tools):
    energy = tools.read_building_energy({})
    assert energy["estimate"] is True and energy["unit_energy"] == "kWh"
    with pytest.raises(ValueError):
        tools.read_building_energy({"zone_id": "ZONE-01"})  # takes no args
    with pytest.raises(ValueError):
        tools.read_zone_state({"zone_id": "ZONE-99"})       # unknown zone


def test_limit_hooks(tools, otreg, twin, monkeypatch):
    monkeypatch.setattr(otreg, "_TOOLS", tools)
    # observed meter reads the plant's own setpoint.
    assert otreg.observe_limit_value("ot-pilot", "setpoint_movement", "ZONE-01") == \
        twin.zones["ZONE-01"].setpoint_c
    with pytest.raises(KeyError):
        otreg.observe_limit_value("ot-pilot", "valve_travel", "V-1")
    # evidence hook resolves UNKNOWN from the twin operation log.
    tools.set_zone_setpoint({"zone_id": "ZONE-02", "setpoint_c": 20.5})
    ev = otreg.check_limit_evidence("ot-pilot", {}, f"twin-op:{PROPOSAL}")
    assert ev["executed"] is True and ev["source"] == "twin-operation-log"
    never = "00000000-0000-4000-8000-000000000000"
    assert otreg.check_limit_evidence("ot-pilot", {}, f"twin-op:{never}")["executed"] is False
    for bad in ("bad-ref", "twin-op:never", "twin-op:../status", f"twin-op:{PROPOSAL}/x"):
        with pytest.raises(ValueError):
            otreg.check_limit_evidence("ot-pilot", {}, bad)


@pytest.mark.parametrize("resource", ["ZONE-09", "../operations/x", "ZONE-01/../status", ""])
def test_the_observer_reads_only_contract_zones(tools, otreg, monkeypatch, resource):
    """REMORA may hand the observer the agent's own argument before the signed
    contract has checked it (REMORA #1101). It must never reach a twin URL."""
    monkeypatch.setattr(otreg, "_TOOLS", tools)
    with pytest.raises(KeyError):
        otreg.observe_limit_value("ot-pilot", "setpoint_movement", resource)


def test_all_supervisory_plant_tools_and_interlocks_through_registry(otreg, tokens):
    """Every supervisory energy-saving tool executes through OtTools, enforces
    IL-01..IL-11 on the twin actuator, and is observed by EffectObserver."""
    from twin.effect_observer import EFFECT_VERIFIED, EffectObserver
    from twin.model import Twin, load_config
    from twin.scenarios import load_scenario

    cfg = load_config(ROOT / "twin" / "fixtures" / "three-zone-hvac-seasonal-v2.json")
    sc = load_scenario(ROOT / "scenarios" / "baseline-day-hvac-v1.json")
    hvac = Twin(cfg, sc)
    read_client = TestClient(build_read_app(hvac, tokens))
    actuator = ActuatorClient("http://testserver", tokens.actuator,
                              client=TestClient(build_actuator_app(hvac, tokens)))
    seq = 0

    def next_pid() -> str:
        nonlocal seq
        seq += 1
        return f"7912188c-60d5-4d00-8b26-{seq:012d}"

    t = otreg.OtTools(actuator=actuator, read_base_url="http://testserver",
                      read_token=tokens.read, toolspec=ToolSpec.load(),
                      read_client=read_client, proposal_id=next_pid)
    obs = EffectObserver("http://testserver", tokens.read, tenant_id="ot-pilot",
                         run_id="test", client=read_client)

    # 1. Cooling setpoint (IL-01 deadband enforced)
    r_cool = t.set_zone_cooling_setpoint({"zone_id": "ZONE-01", "cooling_c": 26.0})
    assert r_cool["status"] == "APPLIED" and hvac.setpoints.zones["ZONE-01"].cooling_c == 26.0
    assert obs.observe_tool(r_cool["operation_id"], tool_name="set_zone_cooling_setpoint",
                            arguments={"zone_id": "ZONE-01", "cooling_c": 26.0})["effect_status"] == EFFECT_VERIFIED
    with pytest.raises(ActuationError, match="IL-01:deadband"):
        t.set_zone_cooling_setpoint({"zone_id": "ZONE-02", "cooling_c": 22.0})  # heating is 21.0

    # 2. CO2 setpoint (IL-07)
    r_co2 = t.set_zone_co2_setpoint({"zone_id": "ZONE-01", "co2_ppm": 850})
    assert r_co2["status"] == "APPLIED" and hvac.setpoints.zones["ZONE-01"].co2_ppm == 850.0
    assert obs.observe_tool(r_co2["operation_id"], tool_name="set_zone_co2_setpoint",
                            arguments={"zone_id": "ZONE-01", "co2_ppm": 850})["effect_status"] == EFFECT_VERIFIED

    # 3. Lighting setpoint & mode (IL-08)
    r_lux = t.set_zone_lighting_setpoint({"zone_id": "ZONE-01", "lux": 450})
    assert r_lux["status"] == "APPLIED" and hvac.setpoints.zones["ZONE-01"].lux == 450.0
    r_lm = t.set_zone_lighting_mode({"zone_id": "ZONE-01", "mode": "schedule", "daylight_harvesting": False})
    assert r_lm["status"] == "APPLIED"
    assert obs.observe_tool(r_lm["operation_id"], tool_name="set_zone_lighting_mode",
                            arguments={"zone_id": "ZONE-01", "mode": "schedule",
                                       "daylight_harvesting": False})["effect_status"] == EFFECT_VERIFIED

    # 4. Supply air setpoint (IL-05, IL-11)
    r_sat = t.set_supply_air_setpoint({"supply_air_c": 18.0})
    assert r_sat["status"] == "APPLIED" and hvac.setpoints.supply_air_c == 18.0
    assert obs.observe_tool(r_sat["operation_id"], tool_name="set_supply_air_setpoint",
                            arguments={"supply_air_c": 18.0})["effect_status"] == EFFECT_VERIFIED
    with pytest.raises(ActuationError, match="IL-11:delta_exceeded"):
        t.set_supply_air_setpoint({"supply_air_c": 16.0})

    # 5. Buffer tank setpoint (IL-04) & heat-pump limit (IL-09)
    r_tank = t.set_tank_setpoint({"tank_c": 48.0})
    assert r_tank["status"] == "APPLIED" and hvac.setpoints.tank_c == 48.0
    # Tank is at 45.0 < 48.0 - 2.0, so capping HP below 1.0 is refused by IL-09!
    with pytest.raises(ActuationError, match="IL-09:tank_short"):
        t.set_heat_pump_limit({"max_fraction": 0.6})
    hvac.plant_state = hvac.plant_state.__class__(tank_c=47.0, co2_ppm=hvac.plant_state.co2_ppm)
    r_hp = t.set_heat_pump_limit({"max_fraction": 0.6})
    assert r_hp["status"] == "APPLIED" and hvac.setpoints.hp_max_fraction == 0.6
    assert obs.observe_tool(r_hp["operation_id"], tool_name="set_heat_pump_limit",
                            arguments={"max_fraction": 0.6})["effect_status"] == EFFECT_VERIFIED

    # 6. AHU enabled (IL-06: t=0 is unoccupied, so false is allowed; while occupied it is refused)
    r_ahu = t.set_ahu_enabled({"enabled": False})
    assert r_ahu["status"] == "APPLIED" and hvac.setpoints.ahu_enabled is False
    assert obs.observe_tool(r_ahu["operation_id"], tool_name="set_ahu_enabled",
                            arguments={"enabled": False})["effect_status"] == EFFECT_VERIFIED

    # 7. Heating cables (IL-10)
    r_cab = t.set_heating_cable_mode({"circuit": "CABLE-RAMP", "mode": "sensor"})
    assert r_cab["status"] == "APPLIED" and hvac.setpoints.cable_modes["CABLE-RAMP"] == "sensor"
    assert obs.observe_tool(r_cab["operation_id"], tool_name="set_heating_cable_mode",
                            arguments={"circuit": "CABLE-RAMP", "mode": "sensor"})["effect_status"] == EFFECT_VERIFIED
    hvac.points["OUT-01.SurfaceWet"] = 1.0  # wet surface at sub-zero outdoor temperature
    with pytest.raises(ActuationError, match="IL-10:freezing_wet_surface"):
        t.set_heating_cable_mode({"circuit": "CABLE-GUTTER", "mode": "off"})

