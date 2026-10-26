"""INV-01/INV-02 at the HTTP layer: route separation and credential separation."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_ENV, cmd, plant_fingerprint
from twin import server
from twin.api import Tokens, TokenConfigError, build_actuator_app, build_control_app, build_read_app


def H(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def apps(twin, tokens):
    return (TestClient(build_read_app(twin, tokens)), TestClient(build_actuator_app(twin, tokens)),
            TestClient(build_control_app(twin, tokens)))


@pytest.mark.parametrize("mutate,match", [
    ({"TWIN_READ_TOKEN": ""}, "missing"),
    ({"TWIN_ACTUATOR_TOKEN": "short"}, "missing"),
    ({"TWIN_CONTROL_TOKEN": TEST_ENV["TWIN_READ_TOKEN"]}, "differ"),
    ({"TWIN_ACTUATOR_TOKEN": "changeme-changeme-changeme-123"}, "placeholder"),
])
def test_token_config_fails_closed(mutate, match):
    with pytest.raises(TokenConfigError, match=match):
        Tokens.from_env(TEST_ENV | mutate)


def test_read_listener_has_no_write_or_control_routes(apps, tokens, twin):
    read, _, _ = apps
    fp = plant_fingerprint(twin)
    for tok in (tokens.read, tokens.actuator, tokens.control):
        r = read.post("/internal/v1/actuate/setpoint", json=cmd(twin, "guess-0001"), headers=H(tok))
        assert r.status_code in (404, 405)
        r = read.post("/control/v1/stop-switch", json={"engaged": True, "actor": "x"}, headers=H(tok))
        assert r.status_code in (404, 405)
    assert plant_fingerprint(twin) == fp


def test_actuator_listener_has_no_read_or_control_routes(apps, tokens):
    _, act, _ = apps
    assert act.get("/v1/zones", headers=H(tokens.actuator)).status_code == 404
    assert act.post("/control/v1/clock/advance", json={"seconds": 60},
                    headers=H(tokens.actuator)).status_code == 404


def test_actuator_refuses_every_other_credential(apps, tokens, twin):
    _, act, _ = apps
    fp = plant_fingerprint(twin)
    for hdr in ({}, H(tokens.read), H(tokens.control), H("x" * 40), {"Authorization": tokens.actuator}):
        r = act.post("/internal/v1/actuate/setpoint", json=cmd(twin, "guess-0002"), headers=hdr)
        assert r.status_code == 401
    assert plant_fingerprint(twin) == fp and not twin.operations


def test_read_refuses_without_read_token(apps, tokens):
    read, _, _ = apps
    assert read.get("/v1/health").status_code == 200
    for hdr in ({}, H(tokens.actuator), H(tokens.control)):
        assert read.get("/v1/zones", headers=hdr).status_code == 401


def test_actuator_happy_path_and_replay(apps, tokens, twin):
    read, act, _ = apps
    body = cmd(twin, "op-http-0001", sp=20.5)
    r1 = act.post("/internal/v1/actuate/setpoint", json=body, headers=H(tokens.actuator))
    r2 = act.post("/internal/v1/actuate/setpoint", json=body, headers=H(tokens.actuator))
    assert r1.status_code == r2.status_code == 200
    assert (r1.json()["replayed"], r2.json()["replayed"]) == (False, True)
    assert twin.revision == 1
    assert read.get("/v1/operations/op-http-0001", headers=H(tokens.read)).json()["status"] == "APPLIED"


def test_actuator_schema_is_strict(apps, tokens, twin):
    _, act, _ = apps
    hdr = H(tokens.actuator)
    for body in (cmd(twin, "op-strict-0001") | {"tenant_override": "x"},
                 cmd(twin, "op-strict-0002", sp="20.5"),
                 cmd(twin, "op-strict-0003", expected_state_revision="0"),
                 cmd(twin, "bad id"),
                 cmd(twin, "op-strict-0005") | {"schema": "ot-actuation-command/v0"}):
        assert act.post("/internal/v1/actuate/setpoint", json=body, headers=hdr).status_code == 422
    assert twin.revision == 0 and not twin.operations


def test_guard_refusal_is_409_with_reason(apps, tokens, twin):
    _, act, _ = apps
    r = act.post("/internal/v1/actuate/setpoint", json=cmd(twin, "op-oob-0001", sp=23.5),
                 headers=H(tokens.actuator))
    assert r.status_code == 409 and r.json()["reason"] == "out_of_bounds"
    r = act.post("/internal/v1/actuate/setpoint", json=cmd(twin, "op-oob-0001", sp=20.0),
                 headers=H(tokens.actuator))
    assert r.status_code == 409 and r.json()["reason"] == "operation_id_reused"


def test_control_requires_control_token(apps, tokens, twin):
    _, _, ctl = apps
    body = {"engaged": True, "actor": "op"}
    assert ctl.post("/control/v1/stop-switch", json=body, headers=H(tokens.actuator)).status_code == 401
    assert ctl.post("/control/v1/stop-switch", json=body, headers=H(tokens.control)).status_code == 200
    assert twin.stop_switch


def test_server_refuses_wildcard_actuator_bind(monkeypatch):
    for k, v in TEST_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TWIN_ACTUATOR_BIND", "0.0.0.0")
    monkeypatch.delenv("TWIN_ALLOW_WILDCARD_ACTUATOR", raising=False)
    assert asyncio.run(server.main()) == 2


def test_server_refuses_missing_tokens(monkeypatch):
    for k in TEST_ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(TokenConfigError):
        asyncio.run(server.main())
