"""Run the real three-listener twin process and drive it over HTTP.

Covers: governed-shaped write via the actuator listener, independent
observation, replay, rejection, restart with snapshot, and the boundary smoke
suite (which must fail when pointed at a reachable actuator).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import httpx
import jsonschema
import pytest

from runner import smoke
from tests.conftest import ROOT, TEST_ENV
from twin.effect_observer import (EFFECT_MISMATCH, EFFECT_UNOBSERVABLE, EFFECT_VERIFIED,
                                  EffectObserver)

OBS_SCHEMA = jsonschema.Draft202012Validator(
    json.loads((ROOT / "contracts" / "twin-observation-v1.schema.json").read_text()))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path):
    ports = {"read": _free_port(), "act": _free_port(), "ctl": _free_port()}
    env = os.environ | TEST_ENV | {
        "TWIN_READ_BIND": "127.0.0.1", "TWIN_READ_PORT": str(ports["read"]),
        "TWIN_ACTUATOR_BIND": "127.0.0.1", "TWIN_ACTUATOR_PORT": str(ports["act"]),
        "TWIN_CONTROL_BIND": "127.0.0.1", "TWIN_CONTROL_PORT": str(ports["ctl"]),
        "TWIN_SNAPSHOT_PATH": str(tmp_path / "snap.json"), "PYTHONPATH": str(ROOT),
    }
    procs: list[subprocess.Popen] = []

    def start() -> None:
        p = subprocess.Popen([sys.executable, "-m", "twin.server"], cwd=ROOT, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{ports['act']}/internal/v1/health", timeout=0.5).is_success:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("twin did not start")

    def stop() -> None:
        while procs:
            p = procs.pop()
            p.terminate()
            p.wait(10)

    start()
    urls = {k: f"http://127.0.0.1:{v}" for k, v in ports.items()}
    yield urls, start, stop
    stop()


def _act(urls, body):
    return httpx.post(f"{urls['act']}/internal/v1/actuate/setpoint", json=body,
                      headers={"Authorization": f"Bearer {TEST_ENV['TWIN_ACTUATOR_TOKEN']}"})


def _ctl(urls, path, body):
    return httpx.post(f"{urls['ctl']}{path}", json=body,
                      headers={"Authorization": f"Bearer {TEST_ENV['TWIN_CONTROL_TOKEN']}"})


def _body(op, sp, rev, zone="ZONE-01"):
    return {"schema": "ot-actuation-command/v1", "operation_id": op, "zone_id": zone,
            "setpoint_c": sp, "units": "degC", "expected_state_revision": rev}


def test_effect_verified_replay_and_restart(live):
    urls, start, stop = live
    obs = EffectObserver(urls["read"], TEST_ENV["TWIN_READ_TOKEN"], tenant_id="t", run_id="r")
    assert _ctl(urls, "/control/v1/clock/advance", {"seconds": 900}).is_success

    r = _act(urls, _body("op-live-0001", 20.5, 0))
    assert r.status_code == 200
    env = obs.observe("op-live-0001", zone_id="ZONE-01", expected_setpoint_c=20.5)
    OBS_SCHEMA.validate(env)
    assert env["effect_status"] == EFFECT_VERIFIED

    # rejected write: observer must not report it verified
    r = _act(urls, _body("op-live-0002", 23.5, 1, zone="ZONE-02"))
    assert r.status_code == 409
    env = obs.observe("op-live-0002", zone_id="ZONE-02", expected_setpoint_c=23.5)
    assert env["effect_status"] == EFFECT_MISMATCH

    # restart: same operation replays the stored outcome, no second effect
    stop()
    start()
    r = _act(urls, _body("op-live-0001", 20.5, 0))
    assert r.status_code == 200 and r.json()["replayed"] is True
    status = httpx.get(f"{urls['read']}/v1/status",
                       headers={"Authorization": f"Bearer {TEST_ENV['TWIN_READ_TOKEN']}"}).json()
    assert status["state_revision"] == 1 and status["sim_time_s"] == 900


def test_plant_down_is_unobservable_not_failed(live):
    urls, _, stop = live
    stop()
    env = EffectObserver(urls["read"], TEST_ENV["TWIN_READ_TOKEN"], tenant_id="t", run_id="r",
                         timeout_s=0.5).observe("op-x-00000001", zone_id="ZONE-01", expected_setpoint_c=20.0)
    assert env["effect_status"] == EFFECT_UNOBSERVABLE


def test_stop_switch_over_http(live):
    urls, _, _ = live
    assert _ctl(urls, "/control/v1/stop-switch", {"engaged": True, "actor": "op-1"}).is_success
    r = _act(urls, _body("op-live-stop-01", 20.5, 0))
    assert r.status_code == 409 and r.json()["reason"] == "stop_switch_engaged"


def test_smoke_suite_passes_when_isolated(live, monkeypatch):
    urls, _, _ = live
    for k in ("TWIN_ACTUATOR_TOKEN", "TWIN_CONTROL_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    dead = f"http://127.0.0.1:{_free_port()}"
    res = smoke.run(urls["read"], TEST_ENV["TWIN_READ_TOKEN"], dead, dead, timeout=1.0)
    assert res and all(r["outcome"] == "PASS" for r in res), res


def test_smoke_suite_fails_on_bypassable_layout(live, monkeypatch):
    """Known-negative: an actuator reachable from the runner must fail the suite."""
    urls, _, _ = live
    monkeypatch.delenv("TWIN_ACTUATOR_TOKEN", raising=False)
    monkeypatch.delenv("TWIN_CONTROL_TOKEN", raising=False)
    res = smoke.run(urls["read"], TEST_ENV["TWIN_READ_TOKEN"], urls["act"], urls["ctl"], timeout=1.0)
    failed = {r["case_id"] for r in res if r["outcome"] == "FAIL"}
    assert {"actuator-listener-unreachable", "control-listener-unreachable"} <= failed


def test_smoke_cli_exit_code_reflects_failure(live, monkeypatch, tmp_path):
    urls, _, _ = live
    monkeypatch.setenv("TWIN_READ_URL", urls["read"])
    monkeypatch.setenv("TWIN_READ_TOKEN", TEST_ENV["TWIN_READ_TOKEN"])
    monkeypatch.setenv("TWIN_ACTUATOR_PROBE_URL", urls["act"])  # reachable -> must fail
    monkeypatch.setenv("TWIN_CONTROL_PROBE_URL", urls["ctl"])
    monkeypatch.delenv("TWIN_ACTUATOR_TOKEN", raising=False)
    monkeypatch.delenv("TWIN_CONTROL_TOKEN", raising=False)
    assert smoke.main(["--run-id", "neg", "--out", str(tmp_path)]) == 1
    m = json.loads((tmp_path / "neg" / "manifest.json").read_text())
    assert m["claims"]["no_bypass_runner_to_actuator"] == "NOT_ESTABLISHED"
    assert m["counts"]["failed"] >= 2
