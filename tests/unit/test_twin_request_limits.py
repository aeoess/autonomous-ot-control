"""SDD-OT-002 SR-15 (F-17), acceptance test ST-15: the twin's listeners refuse oversized, malformed and
unauthenticated requests before they cost anything, and refused operations cannot grow the log by what
they send."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import cmd, plant_fingerprint
from twin import api, server
from twin.api import MAX_BODY_BYTES, PLANT_ARGUMENT_MODELS, build_actuator_app, build_control_app, \
    build_read_app
from twin.model import OperationLogFull, Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SETPOINT = "/internal/v1/actuate/setpoint"
PLANT = "/internal/v1/actuate/plant"


def H(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def hvac() -> Twin:
    return Twin(load_config(ROOT / "twin" / "fixtures" / "three-zone-hvac-seasonal-v2.json"),
                load_scenario(ROOT / "scenarios" / "baseline-day-hvac-v1.json"))


def plant_cmd(twin: Twin, op: str, tool: str, args: dict) -> dict:
    return {"schema": "ot-plant-command/v1", "operation_id": op, "tool_name": tool, "arguments": args,
            "expected_state_revision": twin.revision}


def raw(client: TestClient, path: str, text: str, headers: dict[str, str]):
    return client.post(path, content=text.encode(), headers={**headers, "Content-Type": "application/json"})


# -- ST-15: 500 kB arguments -> 413 -----------------------------------------------------------------------

def test_st15_a_500kb_body_is_refused_413_and_changes_nothing(hvac, tokens):
    act = TestClient(build_actuator_app(hvac, tokens))
    fp = plant_fingerprint(hvac)
    body = plant_cmd(hvac, "op-big-00001", "set_supply_air_setpoint", {"supply_air_c": 18.5, "pad": "x" * 500_000})
    r = act.post(PLANT, json=body, headers=H(tokens.actuator))
    assert r.status_code == 413 and r.json()["detail"] == "body_too_large"
    assert plant_fingerprint(hvac) == fp and not hvac.operations


def test_st15_a_chunked_body_over_the_cap_is_counted_and_refused(twin, tokens):
    act = TestClient(build_actuator_app(twin, tokens))

    def chunks():
        for _ in range(8):
            yield b" " * (MAX_BODY_BYTES // 4)
    r = act.post(SETPOINT, content=chunks(), headers={**H(tokens.actuator), "Content-Type": "application/json"})
    assert r.status_code == 413 and not twin.operations


def test_a_body_under_the_cap_still_reaches_the_route(twin, tokens):
    act = TestClient(build_actuator_app(twin, tokens))
    r = act.post(SETPOINT, json=cmd(twin, "op-small-0001"), headers=H(tokens.actuator))
    assert r.status_code == 200 and r.json()["status"] == "APPLIED"


# -- ST-15: NaN value -> 422 ------------------------------------------------------------------------------

def test_st15_nan_and_infinity_are_422_and_never_recorded(twin, hvac, tokens):
    act = TestClient(build_actuator_app(twin, tokens))
    for lit in ("NaN", "Infinity", "-Infinity"):
        text = json.dumps(cmd(twin, f"op-nan-{lit.strip('-')[:3]}-1")).replace("20.5", lit)
        assert raw(act, SETPOINT, text, H(tokens.actuator)).status_code == 422, lit
    assert not twin.operations
    pact = TestClient(build_actuator_app(hvac, tokens))
    text = json.dumps(plant_cmd(hvac, "op-nan-plant-1", "set_tank_setpoint", {"tank_c": 50.0})).replace("50.0", "NaN")
    assert raw(pact, PLANT, text, H(tokens.actuator)).status_code == 422 and not hvac.operations


# -- ST-15: list zone_id -> 422; every tool's arguments typed -------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("set_zone_cooling_setpoint", {"zone_id": ["ZONE-01"], "cooling_c": 24.0}),
    ("set_zone_cooling_setpoint", {"zone_id": "ZONE-01", "cooling_c": "24.0"}),
    ("set_zone_cooling_setpoint", {"zone_id": "ZONE-01", "cooling_c": True}),
    ("set_zone_co2_setpoint", {"zone_id": "ZONE-01", "co2_ppm": 800.5}),
    ("set_zone_lighting_mode", {"zone_id": "ZONE-01", "mode": "presence", "daylight_harvesting": "yes"}),
    ("set_supply_air_setpoint", {"supply_air_c": 18.5, "override_interlock": True}),
    ("set_ahu_enabled", {"enabled": 1}),
    ("set_heating_cable_mode", {"circuit": "C" * 65, "mode": "auto"}),
    ("set_heat_pump_limit", {}),
    ("open_valve", {"zone_id": "ZONE-01"}),
])
def test_st15_arguments_outside_the_tools_typed_model_are_422(hvac, tokens, tool, args):
    act = TestClient(build_actuator_app(hvac, tokens))
    r = act.post(PLANT, json=plant_cmd(hvac, "op-typed-0001", tool, args), headers=H(tokens.actuator))
    assert r.status_code == 422, r.text
    assert not hvac.operations


def test_a_list_zone_id_on_the_setpoint_route_is_422(twin, tokens):
    act = TestClient(build_actuator_app(twin, tokens))
    r = act.post(SETPOINT, json=cmd(twin, "op-list-0001", zone=["ZONE-01"]), headers=H(tokens.actuator))
    assert r.status_code == 422 and not twin.operations


def test_every_plant_tool_the_guard_knows_has_a_typed_model():
    src = (ROOT / "twin" / "guard.py").read_text("utf-8")
    import re
    assert set(re.findall(r'tool == "(set_[a-z0-9_]+)"', src)) == set(PLANT_ARGUMENT_MODELS)


def test_typed_shape_leaves_bounds_to_the_interlocks(hvac, tokens):
    # Well-typed but out of bounds: still a REJECTED record from the guard, not a 422.
    act = TestClient(build_actuator_app(hvac, tokens))
    r = act.post(PLANT, json=plant_cmd(hvac, "op-bounds-0001", "set_tank_setpoint", {"tank_c": 99.0}),
                 headers=H(tokens.actuator))
    assert r.status_code == 409 and r.json()["status"] == "REJECTED"


def test_an_int_for_a_number_argument_keeps_the_adapters_digest(hvac, tokens):
    from adapter.actuation import build_plant_command, plant_request_digest
    act = TestClient(build_actuator_app(hvac, tokens))
    c = build_plant_command("op-int-00001", hvac.revision, "set_tank_setpoint", {"tank_c": 50})
    r = act.post(PLANT, json=c, headers=H(tokens.actuator))
    assert r.json()["request_digest"] == plant_request_digest(c)


# -- ST-15: unauthenticated malformed JSON -> 401 before the body is parsed -----------------------------

@pytest.mark.parametrize("build,path,good", [
    (build_actuator_app, SETPOINT, "actuator"),
    (build_actuator_app, PLANT, "actuator"),
    (build_control_app, "/control/v1/stop-switch", "control"),
    (build_control_app, "/control/v1/clock/advance", "control"),
])
def test_st15_unauthenticated_malformed_or_oversized_bodies_get_401(twin, tokens, build, path, good):
    c = TestClient(build(twin, tokens))
    others = [t for name, t in (("read", tokens.read), ("actuator", tokens.actuator),
                                ("control", tokens.control)) if name != good]
    for hdr in ({}, *(H(t) for t in others), H("x" * 40)):
        assert raw(c, path, "{not json", hdr).status_code == 401
        assert raw(c, path, "[" * 600_000, hdr).status_code == 401
    assert raw(c, path, "{not json", H(getattr(tokens, good))).status_code == 422
    assert not twin.operations and not twin.stop_switch


def test_the_gate_keeps_health_open_and_wrong_listener_routes_absent(twin, tokens):
    read, act = TestClient(build_read_app(twin, tokens)), TestClient(build_actuator_app(twin, tokens))
    assert read.get("/v1/health").status_code == 200 and act.get("/internal/v1/health").status_code == 200
    assert read.get("/v1/zones").status_code == 401
    assert act.get("/v1/zones", headers=H(tokens.read)).status_code == 404


# -- ST-15: the snapshot grows by less than 1 kB per refused operation ---------------------------------

def test_st15_ten_thousand_refused_operations_grow_the_snapshot_by_under_1kb_each(hvac):
    def size() -> int:
        return len(json.dumps(hvac.snapshot(), sort_keys=True).encode())
    before = size()
    pad = "Z" * 64
    n = 10_000
    for i in range(n):
        rec, _ = hvac.apply_plant_command(plant_cmd(hvac, f"op-flood-{i:06d}", "set_zone_cooling_setpoint",
                                                    {"zone_id": pad, "cooling_c": 24.0}))
        assert rec.status == "REJECTED"
    grown = (size() - before) / n
    assert grown < 1024, grown
    sample = hvac.operations["op-flood-000000"]
    assert sample.requested_value is None and len(sample.target) <= 64


def test_a_refused_operation_keeps_the_digest_not_the_arguments(hvac):
    c = plant_cmd(hvac, "op-digest-0001", "set_tank_setpoint", {"tank_c": 99.0})
    rec, _ = hvac.apply_plant_command(c)
    assert rec.status == "REJECTED" and rec.requested_value is None
    assert rec.request_digest == Twin.plant_request_digest(c)


# -- operation log bounded ------------------------------------------------------------------------------

def test_a_full_operation_log_refuses_new_operations_and_still_answers_replays(twin, tokens, monkeypatch):
    monkeypatch.setattr(twin, "MAX_OPERATIONS", 2)
    first = cmd(twin, "op-full-00001")
    twin.apply_setpoint(first)
    twin.apply_setpoint(cmd(twin, "op-full-00002", zone="ZONE-02", sp=20.5))
    fp = plant_fingerprint(twin)
    with pytest.raises(OperationLogFull):
        twin.apply_setpoint(cmd(twin, "op-full-00003", zone="ZONE-03", sp=21.5))
    assert plant_fingerprint(twin) == fp and "op-full-00003" not in twin.operations
    assert twin.apply_setpoint(first)[1] is True  # a known operation is still answered
    act = TestClient(build_actuator_app(twin, tokens))
    r = act.post(SETPOINT, json=cmd(twin, "op-full-00004", zone="ZONE-03", sp=21.5), headers=H(tokens.actuator))
    assert r.status_code == 503 and "operation_log_full" in r.json()["reason"]


# -- uvicorn limits -------------------------------------------------------------------------------------

def test_every_listener_runs_with_the_uvicorn_limits():
    import uvicorn
    cfg = uvicorn.Config(lambda: None, **server.UVICORN_LIMITS)
    assert cfg.limit_concurrency and cfg.timeout_keep_alive <= 5 and cfg.h11_max_incomplete_event_size <= 16384
    src = (ROOT / "twin" / "server.py").read_text("utf-8")
    assert src.count("**UVICORN_LIMITS") == src.count("uvicorn.Config(") == 3
    assert api.MAX_BODY_BYTES <= 64 * 1024
