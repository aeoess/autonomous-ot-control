"""Fault injection for AT-09 (stale observation, false unit, TFM mismatch) and AT-12 (timeout after actuation,
before acknowledgement). Faults are set only through the control listener, are simulation only, and every
write under an AT-09 fault is refused by the twin's interlock before it has an effect."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASELINE, CONFIG, cmd, plant_fingerprint
from twin import api
from twin.api import build_actuator_app, build_control_app, build_read_app
from twin.model import Twin, TwinError, load_config
from twin.scenarios import load_scenario
from twin.state import QUALITY_GOOD, QUALITY_STALE


def H(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def test_a_frozen_sensor_goes_stale_and_the_write_is_refused(twin):
    twin.inject_fault("sensor_freeze", "ZONE-01", actor="t")
    twin.advance(900)
    assert twin.read_zone("ZONE-01").quality == QUALITY_STALE
    assert twin.read_zone("ZONE-02").quality == QUALITY_GOOD
    fp = plant_fingerprint(twin)
    rec, _ = twin.apply_setpoint(cmd(twin, "op-f-stale-01"))
    assert rec.status == "REJECTED" and rec.reason == "stale_observation" and plant_fingerprint(twin) == fp
    twin.clear_faults("t")
    twin.advance(twin.obs_step_s)
    assert twin.read_zone("ZONE-01").quality == QUALITY_GOOD
    assert twin.apply_setpoint(cmd(twin, "op-f-stale-02"))[0].status == "APPLIED"


@pytest.mark.parametrize("kind,value,reason,field", [
    ("reading_unit", "degF", "basis_unit_mismatch", "unit"),
    ("reading_tfm", "+DEMO01=564.001-RT699", "basis_tfm_mismatch", "tfm"),
])
def test_a_false_unit_or_point_is_reported_and_the_write_refused(twin, kind, value, reason, field):
    twin.inject_fault(kind, "ZONE-01", value, actor="t")
    assert getattr(twin.read_zone("ZONE-01"), field) == value
    fp = plant_fingerprint(twin)
    rec, _ = twin.apply_setpoint(cmd(twin, f"op-f-{field}-01"))
    assert rec.status == "REJECTED" and rec.reason == reason and plant_fingerprint(twin) == fp
    assert twin.apply_setpoint(cmd(twin, f"op-f-{field}-02", zone="ZONE-02"))[0].status == "APPLIED"


def test_the_ack_delay_applies_first_then_answers_late_once(twin, tokens, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(api.time, "sleep", slept.append)
    twin.inject_fault("ack_delay", value=8, actor="t")
    act = TestClient(build_actuator_app(twin, tokens))
    r = act.post("/internal/v1/actuate/setpoint", json=cmd(twin, "op-f-ack-0001"), headers=H(tokens.actuator))
    assert r.status_code == 200 and twin.operations["op-f-ack-0001"].status == "APPLIED" and slept == [8.0]
    r2 = act.post("/internal/v1/actuate/setpoint", json=cmd(twin, "op-f-ack-0002", zone="ZONE-02"),
                  headers=H(tokens.actuator))
    assert r2.status_code == 200 and slept == [8.0]  # one shot


def test_faults_are_set_only_through_the_control_listener(twin, tokens):
    ctl = TestClient(build_control_app(twin, tokens))
    body = {"kind": "sensor_freeze", "zone_id": "ZONE-01", "actor": "t"}
    for tok in (tokens.read, tokens.actuator, "x" * 40):
        assert ctl.post("/control/v1/faults", json=body, headers=H(tok)).status_code == 401
    for app in (build_read_app, build_actuator_app):
        r = TestClient(app(twin, tokens)).post("/control/v1/faults", json=body, headers=H(tokens.control))
        assert r.status_code in (404, 405)
    assert twin.fault_state()["sensor_freeze"] == []
    r = ctl.post("/control/v1/faults", json=body, headers=H(tokens.control))
    assert r.status_code == 200 and r.json()["faults"]["sensor_freeze"] == ["ZONE-01"]
    assert ctl.post("/control/v1/faults/clear", json={"engaged": False, "actor": "t"},
                    headers=H(tokens.control)).json()["faults"]["sensor_freeze"] == []
    assert any(e["event"] == "fault_injected" for e in twin.events)


@pytest.mark.parametrize("body,status", [
    ({"kind": "ack_delay", "value": 0, "actor": "t"}, 409),
    ({"kind": "ack_delay", "value": 31, "actor": "t"}, 409),
    ({"kind": "sensor_freeze", "zone_id": "ZONE-09", "actor": "t"}, 409),
    ({"kind": "reading_unit", "zone_id": "ZONE-01", "actor": "t"}, 409),
    ({"kind": "open_valve", "actor": "t"}, 422),
    ({"kind": "sensor_freeze", "zone_id": "ZONE-01", "actor": "t", "extra": 1}, 422),
])
def test_a_fault_request_outside_its_bounds_is_refused(twin, tokens, body, status):
    ctl = TestClient(build_control_app(twin, tokens))
    assert ctl.post("/control/v1/faults", json=body, headers=H(tokens.control)).status_code == status
    assert twin.fault_state() == {"sensor_freeze": [], "reading_unit": {}, "reading_tfm": {}, "ack_delay_s": 0.0}


def test_faults_are_never_persisted(tmp_path):
    p = tmp_path / "snap.json"
    a = Twin(load_config(CONFIG), load_scenario(BASELINE), snapshot_path=p)
    a.inject_fault("reading_unit", "ZONE-01", "degF", actor="t")
    a.apply_setpoint(cmd(a, "op-f-persist-1", zone="ZONE-02"))
    b = Twin(load_config(CONFIG), load_scenario(BASELINE), snapshot_path=p)
    assert b.fault_state()["reading_unit"] == {} and b.read_zone("ZONE-01").unit == "degC"


def test_unknown_fault_kind_is_refused_in_the_model(twin):
    with pytest.raises(TwinError):
        twin.inject_fault("open_valve")
