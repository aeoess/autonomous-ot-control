"""Plant: optional free cooling (fixed EER) and constant auxiliary electricity (pumps, electric DHW)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from twin import plant as P

ROOT = Path(__file__).resolve().parents[2]
BASE = json.loads((ROOT / "twin" / "fixtures" / "three-zone-hvac-v1.json").read_text("utf-8"))["plant"]


def cfg(**over):
    return P.PlantConfig.from_dict({**BASE, **over})


def test_defaults_keep_every_earlier_fixture_unchanged():
    c = cfg()
    assert c.free_cooling_eer is None and c.aux_electric_w == 0.0


def test_free_cooling_eer_bounds():
    assert cfg(free_cooling_eer=15.0).free_cooling_eer == 15.0
    for bad in (0.5, 61.0):
        with pytest.raises(P.PlantError):
            cfg(free_cooling_eer=bad)


def test_site_fixture_carries_both_and_they_reach_the_electricity():
    from runner.areas import scenario_path
    from twin.model import Twin, load_config
    from twin.scenarios import load_scenario
    f = ROOT / "twin" / "fixtures" / "calibrated" / "site-001-v2.json"
    c = load_config(f)
    assert c["plant"]["aux_electric_w"] > 0 and c["plant"]["free_cooling_eer"] > 7.0
    raw = json.loads(scenario_path("NO1").read_text("utf-8"))
    raw["duration_seconds"] = 3600
    tw = Twin(c, load_scenario(raw, base_dir=scenario_path("NO1").parent))
    tw.advance(600)
    assert tw.points["AUX-01.ElectricPower"] == c["plant"]["aux_electric_w"]
