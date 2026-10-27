"""The data-point catalogue matches the model, and its documentation is current.

A catalogue that drifts from the plant documents a building that does not
exist. These checks fail on any point the plant emits without a catalogue
entry (or the reverse), on dependency edges to unknown points, on writable
points without a tool or interlock, and on stale rendered files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from twin import datapoints as dp
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "twin" / "fixtures" / "three-zone-hvac-v1.json"
CAT = dp.build()
IDS = CAT.by_id()


def _emitted(heat_source: str) -> set[str]:
    cfg = json.loads(CFG.read_text("utf-8"))
    cfg["plant"]["heat_source"] = heat_source
    t = Twin(load_config(cfg), load_scenario(ROOT / "scenarios" / "baseline-day-hvac-v1.json"))
    t.advance(600)
    return set(t.points)


@pytest.mark.parametrize("source", ["heat_pump", "district_heating", "electric_boiler"])
def test_every_emitted_point_is_catalogued_and_applies_to_its_variant(source):
    emitted = _emitted(source)
    missing = emitted - set(IDS)
    assert not missing, f"plant emits uncatalogued points: {sorted(missing)}"
    for pid in emitted:
        v = IDS[pid].variants
        assert not v or source in v, f"{pid} emitted for {source} but declared only for {v}"


def test_every_measured_point_is_emitted_by_some_variant():
    emitted = set().union(*(_emitted(s) for s in ("heat_pump", "district_heating", "electric_boiler")))
    plant_kinds = {"AI", "AV", "BI", "BV", "CV"}
    not_plant = {"EM-01.Cost", "OUT-01.IcingRisk", "OUT-01.Temp24hMean", "EM-01.MonthlyPeakKw",
                 "EM-01.GridCost"} | {f"{z}.AirTemp" for z in dp.ZONES}
    declared = {p.id for p in CAT.points if p.kind in plant_kinds} - not_plant
    assert declared <= emitted, f"catalogued but never emitted: {sorted(declared - emitted)}"


def _matches(ref: str, ids: set[str]) -> bool:
    if ref in ids:
        return True
    if "*" in ref or ref.startswith(("ZONE.", "CABLE.")):
        tail = ref.split(".", 1)[1]
        return any(i.endswith("." + tail) for i in ids)
    return False


def test_dependency_edges_name_known_points():
    ids = set(IDS)
    for p in CAT.points:
        for a in p.affects:
            assert a in ids, f"{p.id} affects unknown {a}"
    for k in CAT.cascades:
        for step in k.chain:
            assert _matches(step, ids), f"{k.id}: unknown {step}"
    for i in CAT.interlocks:
        for ref in i.points:
            assert ref == "*" or _matches(ref, ids), f"{i.id}: unknown {ref}"


def test_every_writable_point_has_a_tool_and_an_interlock():
    tools = {t.id for t in CAT.tools}
    interlocks = {i.id for i in CAT.interlocks}
    for p in CAT.points:
        if p.kind in ("SP", "SB"):
            assert p.writable_via in tools, p.id
            assert p.interlocks and set(p.interlocks) <= interlocks, p.id
        else:
            assert p.writable_via is None, f"{p.id} is not a setpoint but names a tool"


def test_statuses_are_honest():
    for item in (*CAT.interlocks, *CAT.tools, *CAT.procedures):
        assert item.status in ("IMPLEMENTED", "SPECIFIED"), item
    implemented_tools = {t.id for t in CAT.tools if t.status == "IMPLEMENTED"}
    # Today the twin's actuator route serves exactly the zone heating setpoint.
    assert implemented_tools == {"set_zone_setpoint"}
    for t in CAT.tools:
        if t.requires_procedure:
            assert t.requires_procedure in {r.id for r in CAT.procedures}


def test_the_implemented_tool_schema_matches_the_signed_contract():
    spec = json.loads((ROOT / "contracts" / "ot-toolspec-v1.json").read_text("utf-8"))
    write = next(t for t in spec["tools"] if t["tool_name"] == "set_zone_setpoint")
    tool = next(t for t in CAT.tools if t.id == "set_zone_setpoint")
    sp = write["arguments"]["setpoint_c"]
    assert (tool.arguments["setpoint_c"]["minimum"], tool.arguments["setpoint_c"]["maximum"]) == \
        (sp["minimum"], sp["maximum"])
    assert tool.arguments["zone_id"]["enum"] == write["arguments"]["zone_id"]["enum"]


def test_rendered_files_are_current():
    md = (ROOT / "docs" / "datapoints.md").read_text("utf-8")
    js = json.loads((ROOT / "twin" / "fixtures" / "datapoints-v1.json").read_text("utf-8"))
    assert md == dp.render_markdown(CAT), "run: python -m twin.datapoints --write"
    assert js == json.loads(json.dumps(dp.as_json(CAT))), "run: python -m twin.datapoints --write"
