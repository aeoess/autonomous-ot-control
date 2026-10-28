"""Finding 2 (after PR #20): a restart must not lose HVAC state or reset the interval guard.

Before the fix the snapshot carried zones, operations and counters but not
the HVAC setpoints, the plant state, the point history behind the
min-interval interlock, or the heat/electricity meters. A twin resumed from
it kept revision 1 and an APPLIED record for cooling 24.0 while the cooling
setpoint was back at its default, and accepted a new change at the same
simulated instant that the interlock had just refused.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.areas import CONFIG, scenario_path
from tests.conftest import BASELINE, CONFIG as SIMPLE_CONFIG
from twin.model import SNAPSHOT_SCHEMA, Twin, TwinError, load_config
from twin.scenarios import load_scenario

COOL = "set_zone_cooling_setpoint"


def hvac(snapshot_path: Path) -> Twin:
    raw = json.loads(scenario_path("NO1").read_text("utf-8"))
    raw["duration_seconds"] = 2 * 86400
    return Twin(load_config(CONFIG), load_scenario(raw, base_dir=scenario_path("NO1").parent),
                snapshot_path=snapshot_path)


def plant(tw: Twin, op: str, tool: str, args: dict):
    rec, replayed = tw.apply_plant_command({"operation_id": op, "tool_name": tool, "arguments": args,
                                            "expected_state_revision": tw.revision})
    return rec, replayed


def hvac_fingerprint(tw: Twin) -> dict:
    sp = tw.setpoints
    return {
        "revision": tw.revision, "now_s": tw.clock.now_s,
        "cooling": {z: v.cooling_c for z, v in sp.zones.items()},
        "lux": {z: v.lux for z, v in sp.zones.items()},
        "lighting": {z: (v.lighting_mode, v.daylight_harvesting) for z, v in sp.zones.items()},
        "supply_air_c": sp.supply_air_c, "tank_c": sp.tank_c, "hp_max_fraction": sp.hp_max_fraction,
        "ahu_enabled": sp.ahu_enabled, "cable_modes": dict(sp.cable_modes),
        "plant_state": (tw.plant_state.tank_c, dict(tw.plant_state.co2_ppm), tw.plant_state.hp_on,
                        tw.plant_state.hp_state_since_s, tw.plant_state.hp_starts),
        "point_history_s": {k: list(v) for k, v in tw.point_history_s.items()},
        "meters": (tw.energy_kwh, tw.cost, tw.district_heat_kwh, tw.electric_kwh, tw.comfort_violation_dh),
        "operations": {k: v.to_dict() for k, v in tw.operations.items()},
        "zones": {z: (s.setpoint_c, list(s.change_history_s)) for z, s in tw.zones.items()},
    }


def test_cooling_change_survives_restart_and_interval_guard_still_holds(tmp_path):
    snap = tmp_path / "twin.json"
    a = hvac(snap)
    a.advance(3600)
    base = a.setpoints.zones["ZONE-01"].cooling_c
    rec, _ = plant(a, "op-cool-1", COOL, {"zone_id": "ZONE-01", "cooling_c": base + 0.5})
    assert rec.status == "APPLIED"
    again, _ = plant(a, "op-cool-2", COOL, {"zone_id": "ZONE-01", "cooling_c": base + 1.0})
    assert again.status == "REJECTED" and "min_interval" in str(again.reason)
    before = hvac_fingerprint(a)
    del a

    b = hvac(snap)
    assert hvac_fingerprint(b) == before
    assert b.setpoints.zones["ZONE-01"].cooling_c == base + 0.5
    # Same simulated instant: the interlock refuses exactly as before the restart.
    third, _ = plant(b, "op-cool-3", COOL, {"zone_id": "ZONE-01", "cooling_c": base + 1.0})
    assert third.status == "REJECTED" and "min_interval" in str(third.reason)
    assert b.revision == before["revision"]


def test_replay_after_restart_is_the_stored_outcome_not_a_new_effect(tmp_path):
    snap = tmp_path / "twin.json"
    a = hvac(snap)
    a.advance(3600)
    base = a.setpoints.zones["ZONE-01"].cooling_c
    body = {"operation_id": "op-cool-1", "tool_name": COOL, "arguments": {"zone_id": "ZONE-01", "cooling_c": base + 0.5},
            "expected_state_revision": a.revision}
    first, _ = a.apply_plant_command(body)
    assert first.status == "APPLIED"
    del a
    b = hvac(snap)
    fp = hvac_fingerprint(b)
    again, replayed = b.apply_plant_command(body)
    assert replayed and again == first and hvac_fingerprint(b) == fp
    # A historical APPLIED on replay is the record, not evidence of the plant now: the plant must agree anyway.
    assert b.setpoints.zones["ZONE-01"].cooling_c == base + 0.5


@pytest.mark.parametrize("tool, make_args, read", [
    ("set_tank_setpoint", lambda tw: {"tank_c": 47.0}, lambda tw: tw.setpoints.tank_c),
    ("set_ahu_enabled", lambda tw: {"enabled": False}, lambda tw: tw.setpoints.ahu_enabled),
    ("set_heating_cable_mode", lambda tw: {"circuit": "CABLE-RAMP", "mode": "off"},
     lambda tw: tw.setpoints.cable_modes.get("CABLE-RAMP")),
    ("set_supply_air_setpoint", lambda tw: {"supply_air_c": tw.setpoints.supply_air_c - 0.5},
     lambda tw: tw.setpoints.supply_air_c),
    ("set_zone_lighting_mode", lambda tw: {"zone_id": "ZONE-02", "mode": "schedule", "daylight_harvesting": False},
     lambda tw: (tw.setpoints.zones["ZONE-02"].lighting_mode, tw.setpoints.zones["ZONE-02"].daylight_harvesting)),
])
def test_other_plant_tools_survive_restart(tmp_path, tool, make_args, read):
    snap = tmp_path / "twin.json"
    a = hvac(snap)
    a.advance(3600)
    rec, _ = plant(a, "op-1", tool, make_args(a))
    assert rec.status == "APPLIED", rec.reason
    value = read(a)
    before = hvac_fingerprint(a)
    del a
    b = hvac(snap)
    assert read(b) == value
    assert hvac_fingerprint(b) == before


def test_meters_and_plant_physics_state_survive_restart(tmp_path):
    snap = tmp_path / "twin.json"
    a = hvac(snap)
    a.advance(4 * 3600)
    assert a.electric_kwh > 0 or a.district_heat_kwh > 0
    before = hvac_fingerprint(a)
    tank_before, hp_starts_before = a.plant_state.tank_c, a.plant_state.hp_starts
    del a
    b = hvac(snap)
    assert hvac_fingerprint(b) == before
    assert b.plant_state.tank_c == tank_before and b.plant_state.hp_starts == hp_starts_before
    # Both twins continue identically from the same state.
    c = hvac(snap)
    b.advance(1800)
    c.advance(1800)
    assert hvac_fingerprint(b) == hvac_fingerprint(c)


def test_v1_snapshot_without_plant_state_is_refused_for_the_hvac_model(tmp_path):
    snap = tmp_path / "twin.json"
    a = hvac(snap)
    a.advance(3600)
    data = json.loads(snap.read_text("utf-8"))
    assert data["schema"] == SNAPSHOT_SCHEMA
    legacy = {k: v for k, v in data.items() if k in (
        "config_digest", "scenario_digest", "now_s", "revision", "energy_kwh", "cost",
        "comfort_violation_dh", "power_w", "stop_switch", "zones", "operations", "events")}
    legacy["schema"] = "ot-twin-snapshot/v1"
    legacy.setdefault("operations", {})  # v1 carried the operation log inline; v2 keeps it in the journal
    snap.write_text(json.dumps(legacy), "utf-8")
    with pytest.raises(TwinError, match="v1"):
        hvac(snap)


def test_v1_snapshot_migrates_explicitly_for_the_simple_model(tmp_path):
    snap = tmp_path / "twin.json"
    config = load_config(SIMPLE_CONFIG)
    a = Twin(config, load_scenario(BASELINE), snapshot_path=snap)
    a.advance(900)
    data = json.loads(snap.read_text("utf-8"))
    legacy = {k: v for k, v in data.items() if k in (
        "config_digest", "scenario_digest", "now_s", "revision", "energy_kwh", "cost",
        "comfort_violation_dh", "power_w", "stop_switch", "zones", "operations", "events")}
    legacy["schema"] = "ot-twin-snapshot/v1"
    legacy.setdefault("operations", {})  # v1 carried the operation log inline; v2 keeps it in the journal
    snap.write_text(json.dumps(legacy), "utf-8")
    b = Twin(config, load_scenario(BASELINE), snapshot_path=snap)
    assert b.clock.now_s == 900
    assert b.snapshot_migrated_from == "ot-twin-snapshot/v1"
    assert any(e.get("event") == "snapshot_migrated" for e in b.events)
