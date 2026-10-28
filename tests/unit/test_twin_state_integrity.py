"""SDD-OT-002 SR-14 (ST-14): authenticated snapshots, re-validated on restore; no effect without its record.

F-08: a doctored twin-state volume (setpoint 45 C, operations emptied, stop switch released) resumed silently.
F-18: the effect was applied in memory before it was persisted, so a persist failure left the plant changed and
the operation id reusable after a restart.
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BASELINE, CONFIG, cmd, plant_fingerprint
from twin.api import build_actuator_app
from twin.model import StateStoreFailed, Twin, TwinError, load_config
from twin.scenarios import load_scenario

KEY = "snapshot-key-0123456789abcdef0123456789abcdef"


def _twin(path, key=KEY) -> Twin:
    return Twin(load_config(CONFIG), load_scenario(BASELINE), snapshot_path=path, snapshot_key=key)


def _write(path, snap):
    path.write_text(json.dumps(snap), "utf-8")


def test_st14_a_doctored_snapshot_is_refused_with_a_key(tmp_path):
    p = tmp_path / "snap.json"
    tw = _twin(p)
    tw.apply_setpoint(cmd(tw, "op-a-0001", sp=20.5))
    tw.set_stop_switch(True, "operator")
    good = json.loads(p.read_text("utf-8"))
    assert good["integrity"]["alg"] == "HMAC-SHA256"
    for doctor in (lambda s: s["zones"]["ZONE-01"].update(setpoint_c=45.0),
                   lambda s: s.update(operations={}),
                   lambda s: s.update(stop_switch=False),
                   lambda s: s.pop("integrity")):
        bad = copy.deepcopy(good)
        doctor(bad)
        _write(p, bad)
        with pytest.raises(TwinError, match="integrity"):
            _twin(p)


def test_st14_without_a_key_a_snapshot_is_still_re_validated(tmp_path):
    p = tmp_path / "snap.json"
    tw = _twin(p, key="")
    tw.apply_setpoint(cmd(tw, "op-b-0001", sp=20.5))
    good = json.loads(p.read_text("utf-8"))
    assert "integrity" not in good
    hot = copy.deepcopy(good)
    hot["zones"]["ZONE-01"]["setpoint_c"] = 45.0
    _write(p, hot)
    with pytest.raises(TwinError, match="outside"):
        _twin(p, key="")
    emptied = copy.deepcopy(good)
    emptied["operations"] = {}
    _write(p, emptied)
    with pytest.raises(TwinError, match="applied operations"):
        _twin(p, key="")  # past op ids would be replayable: refused


def test_a_valid_snapshot_resumes_logged_and_replay_safe(tmp_path):
    p = tmp_path / "snap.json"
    tw = _twin(p)
    first, _ = tw.apply_setpoint(cmd(tw, "op-c-0001", sp=20.5))
    resumed = _twin(p)
    ev = resumed.events[-1]
    assert ev["event"] == "snapshot_restored" and ev["authenticated"] is True and len(ev["snapshot_sha256"]) == 64
    again, replayed = resumed.apply_setpoint(cmd(resumed, "op-c-0001", sp=20.5, expected_state_revision=0))
    assert replayed and again.status == "APPLIED" and resumed.revision == 1
    # A snapshot written without a key is refused once a key is set: it cannot be told from a doctored one.
    q = tmp_path / "plain.json"
    plain = _twin(q, key="")
    plain.apply_setpoint(cmd(plain, "op-d-0001", sp=20.5))
    with pytest.raises(TwinError, match="integrity"):
        _twin(q)


def test_st14_a_persist_failure_leaves_no_effect_no_record_and_fails_closed(tmp_path, monkeypatch):
    p = tmp_path / "snap.json"
    tw = _twin(p)
    tw.apply_setpoint(cmd(tw, "op-e-0001", sp=20.5))
    tw.advance(1800)
    good_bytes, before = p.read_bytes(), plant_fingerprint(tw)  # the last durable state

    def disk_full():
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(tw, "_persist", disk_full)
    with pytest.raises(StateStoreFailed):
        tw.apply_setpoint(cmd(tw, "op-e-0002", sp=21.0))
    assert plant_fingerprint(tw) == before and tw.operation("op-e-0002") is None and tw.revision == 1
    monkeypatch.undo()
    with pytest.raises(StateStoreFailed, match="restarted"):  # fail-closed until restart, even once disk recovers
        tw.apply_setpoint(cmd(tw, "op-e-0003", sp=21.0))
    assert p.read_bytes() == good_bytes
    # After a restart from the last durable snapshot the lost operation never happened: it applies once.
    resumed = _twin(p)
    resumed.advance(1800)
    rec, replayed = resumed.apply_setpoint(cmd(resumed, "op-e-0002", sp=21.0))
    assert rec.status == "APPLIED" and not replayed and resumed.revision == 2


def test_the_actuator_answers_503_store_failed(tmp_path, monkeypatch, tokens):
    tw = _twin(tmp_path / "snap.json")
    monkeypatch.setattr(tw, "_persist", lambda: (_ for _ in ()).throw(OSError("disk")))
    r = TestClient(build_actuator_app(tw, tokens)).post(
        "/internal/v1/actuate/setpoint", json=cmd(tw, "op-f-0001", sp=20.5),
        headers={"Authorization": f"Bearer {tokens.actuator}"})
    assert r.status_code == 503 and r.json()["status"] == "STORE_FAILED" and tw.revision == 0


def test_the_key_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TWIN_SNAPSHOT_KEY", KEY)
    p = tmp_path / "snap.json"
    tw = Twin(load_config(CONFIG), load_scenario(BASELINE), snapshot_path=p)
    tw.apply_setpoint(cmd(tw, "op-g-0001", sp=20.5))
    assert "integrity" in json.loads(p.read_text("utf-8"))
