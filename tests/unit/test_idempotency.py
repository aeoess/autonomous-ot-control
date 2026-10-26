"""Single-use operation identity at the twin (INV-04, AT-04/AT-12 twin side)."""
from __future__ import annotations

import pytest

from tests.conftest import BASELINE, cmd, plant_fingerprint
from twin.model import OperationConflict, Twin, TwinError
from twin.scenarios import load_scenario


def test_replay_same_request_no_second_effect(twin):
    body = cmd(twin, "op-idem-0001", sp=20.5)
    first, r1 = twin.apply_setpoint(body)
    fp = plant_fingerprint(twin)
    second, r2 = twin.apply_setpoint(body)
    assert (r1, r2) == (False, True)
    assert second == first
    assert plant_fingerprint(twin) == fp


def test_reused_id_different_payload_conflicts(twin):
    twin.apply_setpoint(cmd(twin, "op-idem-0002", sp=20.5))
    fp = plant_fingerprint(twin)
    with pytest.raises(OperationConflict):
        twin.apply_setpoint(cmd(twin, "op-idem-0002", sp=20.0, expected_state_revision=0))
    assert plant_fingerprint(twin) == fp


def test_replay_after_restart(config, tmp_path):
    snap = tmp_path / "twin.json"
    a = Twin(config, load_scenario(BASELINE), snapshot_path=snap)
    a.advance(900)
    body = cmd(a, "op-restart-0001", sp=20.5)
    first, _ = a.apply_setpoint(body)
    del a
    b = Twin(config, load_scenario(BASELINE), snapshot_path=snap)
    assert b.clock.now_s == 900 and b.revision == 1
    fp = plant_fingerprint(b)
    again, replayed = b.apply_setpoint(body)
    assert replayed and again == first and plant_fingerprint(b) == fp


def test_snapshot_from_other_scenario_refused(config, tmp_path):
    snap = tmp_path / "twin.json"
    Twin(config, load_scenario(BASELINE), snapshot_path=snap).advance(60)
    raw = load_scenario(BASELINE).raw | {"seed": 999}
    with pytest.raises(TwinError, match="refusing to resume"):
        Twin(config, load_scenario(raw), snapshot_path=snap)


def test_missing_operation_id(twin):
    with pytest.raises(TwinError):
        twin.apply_setpoint(cmd(twin, ""))
