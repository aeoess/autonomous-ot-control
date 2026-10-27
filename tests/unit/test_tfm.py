"""TFM (Statsbygg PA 0802): the register, the parser and validator, and the twin's TFM map.

Specification: twin/tfm.py, docs/tfm.md and ADR-010.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner import caretaker as ck
from runner.areas import CONFIG, scenario_path
from runner.demo import STEP_S
from twin import datapoints as dp
from twin import tfm
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]


def test_register_records_its_sources_and_covers_both_lists():
    reg = tfm.register()
    assert reg["schema"] == "ot-tfm-register/v1"
    assert reg["sources"]["systems"]["revision"] == "5" and len(reg["sources"]["systems"]["sha256"]) == 64
    assert reg["sources"]["components"]["revision"] == "3"
    s, c = reg["systems"], reg["components"]
    assert sum(x["status"] == "system_code" for x in s) == 239 and len(s) == 472
    assert sum(x["status"] == "component_code" for x in c) == 307
    codes = [x["code"] for x in s]
    assert codes == sorted(codes, key=lambda k: (int(k[:2]), k)), "system codes in list order"


@pytest.mark.parametrize("code,status", [("360", "system_code"), ("320", "system_code"), ("564", "system_code"),
                                         ("745", "system_code"), ("325", "not_used"), ("362", "not_used"),
                                         ("32", "group"), ("285", "system_code"), ("286", "system_code")])
def test_known_system_codes(code, status):
    assert tfm._systems()[code]["status"] == status


@pytest.mark.parametrize("code,name", [("JV", "Vifte"), ("RT", "Temperaturgiver"), ("SB", "Reguleringsventil, motorstyrt"),
                                       ("LZ", "Varmekabel / Varmerør"), ("IK", "Kuldeaggregat")])
def test_known_component_codes(code, name):
    assert tfm._components()[code]["name"] == name


def test_parse_round_trips_and_reads_the_en_dash():
    i = tfm.parse("+123456=360.001–JV401")
    assert (i.location, i.system, i.system_run, i.component, i.number, i.is_type) == \
           ("123456", "360", "001", "JV", "401", False)
    assert str(i) == "+123456=360.001-JV401"
    assert tfm.parse("=360.001-JV001T").is_type and str(tfm.parse("+123456")) == "+123456"


@pytest.mark.parametrize("bad,why", [("DEMO=360.001", "not a TFM identifier"), ("=325.001-JP401", "not_used"),
                                     ("=32.001", "not a TFM identifier"), ("=360.001-AA001", "unassigned"),
                                     ("=360.001-QQQ01", "not a TFM identifier"), ("=999.001", "not in Vedlegg 9.1"),
                                     ("=360", "no running number"), ("=360.001-JV000", "000")])
def test_validation_rejects(bad, why):
    errs = tfm.validate(bad)
    assert errs and any(why in e for e in errs), errs


def test_explain_reads_the_running_number_position():
    assert tfm.explain("=360.001-JV401")["component"]["position"] == "supply or flow"
    assert tfm.explain("=360.001-JV501")["component"]["position"] == "return or extract"
    assert tfm.explain("=320.001-RT901")["component"]["position"] == "represents outdoor air"
    assert tfm.explain("=360.001-JV001T")["component"]["position"] is None


def test_every_statsbygg_example_is_understood():
    ex = json.loads((ROOT / "twin/fixtures/tfm-vedlegg-9.3-examples.json").read_text("utf-8"))["identifiers"]
    assert len(ex) == 381
    for e in ex:
        assert (not tfm.validate(e["id"])) == e["valid"], e
        assert e["valid"] or "note" in e, e      # every rejection is a heading or a typo in the source
    assert sum(e["valid"] for e in ex) == 376


def test_every_object_of_the_building_is_valid_and_unique():
    ids = [tfm.object_id(k) for k in tfm.OBJECTS]
    assert len(ids) == len(set(ids))
    for i in ids:
        assert tfm.validate(i) == [], i


def test_every_data_point_names_its_tfm_object():
    roles = {"measured", "setpoint", "state", "calculated", "input"}
    for p in dp.build().points:
        t = tfm.point_tfm(p.id)
        assert t["role"] in roles
        assert (t["tfm"] is None) == (t["role"] == "input"), p.id
        if p.kind in ("SP", "SB") and t["tfm"]:
            assert t["role"] == "setpoint", p.id


def test_positions_match_what_the_objects_are():
    pos = {k: tfm.explain(tfm.object_id(k))["component"]["position"] for k in tfm.OBJECTS}
    assert pos["AHU-01.supply_fan"] == pos["AHU-01.supply_sensor"] == "supply or flow"
    assert pos["AHU-01.extract_fan"] == pos["AHU-01.extract_sensor"] == "return or extract"
    assert pos["OUT-01.temp_sensor"] == "represents outdoor air"
    assert all(tfm.object_id(f"{z}.room_temp").endswith(f"RT60{z[-1]}") for z in ("ZONE-01", "ZONE-02", "ZONE-03"))


@pytest.mark.parametrize("fixture", sorted((ROOT / "twin/fixtures").glob("three-zone*.json")))
def test_zone_fixtures_carry_the_zone_sensor_tfm(fixture):
    for z in json.loads(fixture.read_text("utf-8"))["zones"]:
        assert z["tfm"] == tfm.zone_sensor_tfm(z["id"])


def test_docs_are_current():
    assert (ROOT / "docs/tfm.md").read_text("utf-8") == tfm.render_markdown()


def test_a_mismapped_reading_is_caught_by_the_caretaker():
    raw = json.loads(scenario_path("NO1").read_text("utf-8"))
    raw["duration_seconds"] = 3 * 86400
    tw = Twin(load_config(CONFIG), load_scenario(raw, base_dir=scenario_path("NO1").parent))
    ct = ck.Caretaker(tw)
    tw.zones["ZONE-02"].tfm = tfm.zone_sensor_tfm("ZONE-01")   # ZONE-02 reports ZONE-01's sensor id
    for t in range(0, 4 * STEP_S, STEP_S):
        ct.step(t)
        tw.advance(STEP_S)
    assert ct.alarms and ct.alarms[0]["rule"] == "tfm" and ct.alarms[0]["zone"] == "ZONE-02"
    assert "ZONE-02" in ct._fault and ct.stats.routes["escalate"] == 1
