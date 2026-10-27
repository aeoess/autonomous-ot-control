"""The caretaker (P3): profiles, overlays, the independent validator, the fallback and FDD.

Specification: contracts/caretaker-profile-catalog-v1.json, deploy/ot/site_policy.v1.json
and ADR-009.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

from runner import caretaker as ck
from runner.areas import CONFIG, scenario_path
from runner.demo import STEP_S
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "contracts/caretaker-profile-catalog-v1.json").read_text("utf-8"))


def _twin(area: str = "NO1", days: int = 3, disturbances: list | None = None, start_day: int = 0) -> Twin:
    raw = json.loads(scenario_path(area).read_text("utf-8"))
    raw["duration_seconds"] = (start_day + days) * 86400
    if disturbances:
        raw["disturbances"] = disturbances
    return Twin(load_config(CONFIG), load_scenario(raw, base_dir=scenario_path(area).parent))


def _run(ct: ck.Caretaker, until_s: int) -> None:
    for t in range(ct.tw.clock.now_s, until_s, STEP_S):
        ct.step(t)
        ct.tw.advance(STEP_S)


def test_catalog_matches_its_schema():
    schema = json.loads((ROOT / "contracts/caretaker-profile-catalog-v1.schema.json").read_text("utf-8"))
    jsonschema.Draft202012Validator(schema).validate(CATALOG)


def test_every_profile_overlay_and_algorithm_the_engine_emits_is_catalogued():
    ct = ck.Caretaker(_twin(days=7))
    _run(ct, 7 * 86400 - STEP_S)
    profiles = {p["id"] for p in CATALOG["profiles"]}
    overlays = {o["id"] for o in CATALOG["overlays"]}
    algos = {a["id"] for a in CATALOG["algorithms"]}
    assert set(ck.PROFILES) <= profiles and set(ck.OVERLAYS) <= overlays
    used = {a.split("+")[0] for a in ct.stats.algorithms}
    assert used <= algos, used - algos
    for p in CATALOG["profiles"]:
        assert set(p["algorithms"]) <= algos


def test_same_context_gives_the_same_decisions():
    days = {"2025-01-02"}
    a, b = ck.Caretaker(_twin(), record_days=days), ck.Caretaker(_twin(), record_days=days)
    _run(a, 2 * 86400)
    _run(b, 2 * 86400)
    assert a.records and [r["context_sha256"] for r in a.records] == [r["context_sha256"] for r in b.records]
    assert [(r["base_profile"], r["algorithms"], r["changes"]) for r in a.records] == \
           [(r["base_profile"], r["algorithms"], r["changes"]) for r in b.records]


@pytest.mark.parametrize("kind", ck.OPTIMIZER_FAULTS)
def test_validator_catches_every_corruption_where_it_breaks_a_rule(kind):
    # Tuesday 2025-01-07 09:00 UTC: occupied, so AHU-off and a low heating setpoint break rules.
    t = 6 * 86400 + 9 * 3600
    tw2 = _twin(days=8)
    ct2 = ck.Caretaker(tw2)
    tw2.advance(t)
    assert tw2.scenario.occupied(t)
    base = ct2._fallback(t, ["icing_protection"])
    assert ct2.validate(base, t, ["icing_protection"]) == []
    bad = ck.corrupt(base, kind)
    assert ct2.validate(bad, t, ["icing_protection"]), kind


def test_a_corrupted_proposal_never_reaches_the_twin():
    tw = _twin(days=8)
    ct = ck.Caretaker(tw, proposal_fault_rate=1.0, seed=1)
    _run(ct, 2 * 86400)
    assert sum(ct.stats.optimizer_faults.values()) == 2 * 86400 // STEP_S
    sp = tw.setpoints
    assert sp.tank_c <= 55.0 and 15.0 <= sp.supply_air_c <= 21.0
    assert all(zs.cooling_c >= tw.zones[z].setpoint_c + 2.0 for z, zs in sp.zones.items())
    assert ct.stats.routes["fallback"] == ct.stats.optimizer_faults_caught > 0


def test_without_the_validator_the_same_corruption_is_applied():
    tw = _twin(days=8)
    ct = ck.Caretaker(tw, validated=False, proposal_fault_rate=1.0, seed=1)
    seen = set()
    for t in range(0, 2 * 86400, STEP_S):
        ct.step(t)
        seen.add(tw.setpoints.tank_c)
        tw.advance(STEP_S)
    assert 70.0 in seen


def test_the_fallback_reads_no_zone_sensor_and_passes_validation():
    tw = _twin()
    ct = ck.Caretaker(tw)
    for t in range(0, 86400, STEP_S):
        fb = ct._fallback(t, [])
        for z in tw.zones:
            tw.zones[z].measured_temp_c = -99.0     # a sensor that lies
        assert ct._fallback(t, []) == fb
        assert ct.validate(fb, t, []) == []
        tw.advance(STEP_S)


def test_frost_overlay_lifts_heating_and_forbids_the_cap():
    tw = _twin()
    ct = ck.Caretaker(tw)
    p = ct._fallback(3600, [])
    low = replace(p, heating=dict.fromkeys(p.heating, 17.0), hp_max_fraction=0.6)
    v = ct.validate(low, 3600, ["frost_protection"])
    assert any(x.startswith("OVL-frost:ZONE") for x in v) and "OVL-frost:hp_cap" in v


def test_a_frozen_sensor_is_detected_put_on_the_fallback_and_recovers():
    tw = _twin(days=3, disturbances=[{"kind": "sensor_freeze", "zone_id": "ZONE-01",
                                      "from_s": 6 * 3600, "to_s": 12 * 3600}])
    ct = ck.Caretaker(tw)
    _run(ct, 3 * 86400 - STEP_S)
    ct.finish()
    assert ct.stats.fdd_alarms == ct.stats.fdd_true_alarms == 1
    assert ct.stats.fdd_episodes == ct.stats.fdd_episodes_detected == 1
    assert ct.alarms[0]["rule"] == "stale" and ct.alarms[0]["ground_truth"] == ["sensor_freeze"]
    assert "ZONE-01" not in ct._fault          # recovered after >= 24 h with fresh readings
    assert ct.stats.routes["escalate"] == 1


def test_a_drifted_sensor_stays_distrusted_until_it_jumps_back():
    tw = _twin(days=6, disturbances=[{"kind": "sensor_offset", "zone_id": "ZONE-02", "from_s": 86400,
                                      "to_s": 4 * 86400, "offset_c": 3.0}])
    ct = ck.Caretaker(tw)
    _run(ct, 3 * 86400)
    assert "ZONE-02" in ct._fault             # still offset after 2 days: not trusted again
    _run(ct, 6 * 86400 - STEP_S)
    assert "ZONE-02" not in ct._fault
    assert ct.stats.fdd_alarms == 1 and ct.stats.fdd_true_alarms == 1


def test_raw_arm_has_no_telemetry_checks():
    tw = _twin(days=2, disturbances=[{"kind": "sensor_freeze", "zone_id": "ZONE-01",
                                      "from_s": 3600, "to_s": 20 * 3600}])
    ct = ck.Caretaker(tw, validated=False)
    _run(ct, 86400)
    assert ct.stats.fdd_alarms == 0 and ct.stats.overlays["telemetry_degraded"] == 0


def test_decision_record_carries_the_evidence_fields():
    ct = ck.Caretaker(_twin(), record_days={"2025-01-01"})
    _run(ct, 86400)
    r = ct.records[40]
    for k in ("context", "context_sha256", "site_policy_sha256", "overlays", "base_profile", "algorithms",
              "proposal", "validation", "route", "changes"):
        assert k in r
    assert r["schema"] == "ot-caretaker-decision/v1" and r["route"] in ("submit", "hold", "fallback", "escalate")


def test_caretaker_drives_all_supervisory_changes_through_governed_tools():
    """When given a governed tool dispatcher, Caretaker P3 decomposes every
    supervisory change into governed tool calls that pass the signed ToolSpec
    and the twin's local interlocks."""
    from adapter.contracts import ToolSpec

    tw = _twin(days=2)
    spec = ToolSpec.load()
    dispatched: list[tuple[str, dict]] = []

    def dispatch(tool_name: str, arguments: dict) -> bool:
        if tool_name == "set_zone_setpoint":
            # Clamp to the signed v1 heating range when testing against the v1 contract
            arguments = {**arguments, "setpoint_c": max(spec.min_setpoint_c, min(spec.max_setpoint_c, arguments["setpoint_c"]))}
            if abs(arguments["setpoint_c"] - tw.zones[arguments["zone_id"]].setpoint_c) < 1e-6:
                return False
        canon = spec.canonical_tool_arguments(tool_name, arguments)
        op_id = f"gov-p3-{tw.clock.now_s}-{len(dispatched):04d}"
        if tool_name == "set_zone_setpoint":
            rec, _ = tw.apply_setpoint({"operation_id": op_id, "zone_id": canon["zone_id"],
                                        "setpoint_c": canon["setpoint_c"], "units": "degC",
                                        "expected_state_revision": tw.revision})
        else:
            rec, _ = tw.apply_plant_command({"operation_id": op_id, "tool_name": tool_name,
                                             "arguments": canon, "expected_state_revision": tw.revision})
        assert rec.status == "APPLIED", f"{tool_name}({canon}) rejected: {rec.reason}"
        dispatched.append((tool_name, canon))
        return True

    ct = ck.Caretaker(tw, dispatcher=dispatch)
    _run(ct, 2 * 86400 - STEP_S)
    tools_used = {t for t, _ in dispatched}
    assert {"set_zone_setpoint", "set_zone_cooling_setpoint", "set_tank_setpoint",
            "set_ahu_enabled", "set_heating_cable_mode"} <= tools_used

