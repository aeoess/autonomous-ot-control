"""Local interlock: every refusal leaves the plant untouched (OT-002 gate)."""
from __future__ import annotations

import pytest

from tests.conftest import STALE, cmd, plant_fingerprint
from twin.model import Twin
from twin.scenarios import load_scenario


def _refused(twin: Twin, body: dict, reason: str) -> None:
    before = plant_fingerprint(twin)
    rec, replayed = twin.apply_setpoint(body)
    assert (rec.status, rec.reason, replayed) == ("REJECTED", reason, False)
    assert plant_fingerprint(twin) == before


def test_vector_file(twin, vectors):
    for i, v in enumerate(vectors):
        sp = float("nan") if v["setpoint_c"] == "NaN" else v["setpoint_c"]
        _refused(twin, cmd(twin, f"vec-{i:04d}-{v['case']}", zone=v["zone_id"], sp=sp,
                           units=v["units"]), v["reason"])


def test_happy_path_applies_once(twin):
    rec, replayed = twin.apply_setpoint(cmd(twin, "op-happy-0001", sp=20.5))
    assert rec.status == "APPLIED" and not replayed
    assert (rec.previous_setpoint_c, rec.new_setpoint_c) == (21.0, 20.5)
    assert (rec.state_revision_before, rec.state_revision_after) == (0, 1)
    assert twin.zones["ZONE-01"].setpoint_c == 20.5


def test_resolution(twin):
    _refused(twin, cmd(twin, "op-res-0001", sp=20.55), "invalid_resolution")


def test_revision_mismatch(twin):
    _refused(twin, cmd(twin, "op-rev-0001", expected_state_revision=7), "revision_mismatch")


def test_no_change(twin):
    _refused(twin, cmd(twin, "op-noop-0001", sp=21.0), "no_change")


def test_min_interval_and_rate_limit(twin):
    assert twin.apply_setpoint(cmd(twin, "op-rate-0001", sp=20.5))[0].status == "APPLIED"
    _refused(twin, cmd(twin, "op-rate-0002", sp=21.0), "min_interval")
    twin.advance(900)
    assert twin.apply_setpoint(cmd(twin, "op-rate-0003", sp=21.0))[0].status == "APPLIED"
    twin.advance(900)
    _refused(twin, cmd(twin, "op-rate-0004", sp=21.5), "rate_limited")
    twin.advance(1800)
    assert twin.apply_setpoint(cmd(twin, "op-rate-0005", sp=21.5))[0].status == "APPLIED"


def test_stop_switch(twin):
    twin.set_stop_switch(True, "operator-1")
    _refused(twin, cmd(twin, "op-stop-0001"), "stop_switch_engaged")
    twin.set_stop_switch(False, "operator-1")
    # The refused ID stays refused: no replay shortcut after release (AT-10).
    rec, replayed = twin.apply_setpoint(cmd(twin, "op-stop-0001"))
    assert (rec.status, rec.reason, replayed) == ("REJECTED", "stop_switch_engaged", True)
    assert twin.apply_setpoint(cmd(twin, "op-stop-0002"))[0].status == "APPLIED"


def test_alarm_freeze(twin):
    twin.zones["ZONE-03"].temp_c = 11.0
    _refused(twin, cmd(twin, "op-alarm-0001"), "alarm_freeze")


def test_stale_observation(config):
    t = Twin(config, load_scenario(STALE))
    t.advance(3600 + 900)  # sensor frozen since 3600 s, max age 600 s
    assert t.read_zone("ZONE-01").quality == "STALE"
    _refused(t, cmd(t, "op-stale-0001"), "stale_observation")
    assert t.apply_setpoint(cmd(t, "op-stale-0002", zone="ZONE-02"))[0].status == "APPLIED"


@pytest.mark.parametrize("bad", [True, "20.5", None])
def test_non_numeric_setpoint(twin, bad):
    _refused(twin, cmd(twin, f"op-type-{str(bad)[:4]}-01", sp=bad), "invalid_value")
