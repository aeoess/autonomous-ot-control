from __future__ import annotations

import copy

import pytest

from runner.baseline import run_baseline
from tests.conftest import BASELINE, cmd
from twin.model import Twin, TwinError, load_config
from twin.scenarios import ScenarioError, load_scenario
from twin.state import canonical_digest


def test_same_seed_identical_trace(config):
    a = run_baseline(config, BASELINE)
    b = run_baseline(copy.deepcopy(config), BASELINE)
    assert canonical_digest(a.trace) == canonical_digest(b.trace)
    assert a.energy_kwh == b.energy_kwh and a.cost == b.cost


def test_different_seed_changes_trace(config):
    raw = load_scenario(BASELINE).raw | {"seed": 178}
    a = Twin(config, load_scenario(BASELINE), record_trace=True)
    b = Twin(copy.deepcopy(config), load_scenario(raw), record_trace=True)
    a.advance(3600)
    b.advance(3600)
    assert canonical_digest(a.trace) != canonical_digest(b.trace)


def test_energy_is_monotonic(twin):
    last = twin.energy_kwh
    for _ in range(96):
        twin.advance(900)
        assert twin.energy_kwh >= last
        last = twin.energy_kwh
    assert twin.ended


def test_cannot_advance_past_scenario(twin):
    twin.advance(86400)
    with pytest.raises(TwinError, match="scenario_ended"):
        twin.advance(60)


def test_advance_must_be_step_multiple(twin):
    with pytest.raises(TwinError):
        twin.advance(30)


def test_setpoint_change_affects_only_future_demand(twin):
    twin.advance(900)
    before = twin.energy_kwh
    rec, _ = twin.apply_setpoint(cmd(twin, "op-future-0001", sp=22.0))
    assert rec.status == "APPLIED"
    assert twin.energy_kwh == before  # no invented immediate effect
    twin.advance(900)
    assert twin.energy_kwh > before


def test_unstable_step_refused(config):
    config["step_seconds"] = 900
    config["observation_step_seconds"] = 900
    with pytest.raises(TwinError, match="stability"):
        load_config(config)


def test_initial_setpoint_outside_bounds_refused(config):
    config["zones"][0]["initial_setpoint_c"] = 25.0
    with pytest.raises(TwinError):
        load_config(config)


@pytest.mark.parametrize("path", [("outdoor", "mean_c"), ("price", "peak_nok_per_kwh"),
                                  ("occupancy", "gain_w_per_m2")])
def test_missing_series_raises_never_zero(path):
    raw = copy.deepcopy(load_scenario(BASELINE).raw)
    del raw[path[0]][path[1]]
    with pytest.raises(ScenarioError):
        load_scenario(raw)


def test_unknown_disturbance_refused():
    raw = copy.deepcopy(load_scenario(BASELINE).raw)
    raw["disturbances"] = [{"kind": "open_valve", "zone_id": "ZONE-01", "from_s": 0, "to_s": 60}]
    with pytest.raises(ScenarioError):
        load_scenario(raw)


def test_reads_carry_separate_sim_clock_fields(twin):
    r = twin.read_zone("ZONE-02").to_dict()
    assert r["sim_time_s"] == 0 and r["sim_utc"].endswith("Z") and r["unit"] == "degC"
