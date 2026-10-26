from __future__ import annotations

import json

import jsonschema
import pytest

from tests.conftest import ROOT, cmd

SCHEMAS = {p.name: json.loads(p.read_text()) for p in (ROOT / "contracts").glob("*.schema.json")}


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid(name):
    jsonschema.Draft202012Validator.check_schema(SCHEMAS[name])


TOOL = jsonschema.Draft202012Validator(SCHEMAS["ot-tool-v1.schema.json"])


@pytest.mark.parametrize("call", [
    {"tool_name": "read_zone_state", "arguments": {"zone_id": "ZONE-02"}},
    {"tool_name": "read_building_energy", "arguments": {}},
    {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": 20.5}},
    {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-03", "setpoint_c": 22.3}},
])
def test_tool_surface_accepts(call):
    assert TOOL.is_valid(call)


@pytest.mark.parametrize("call", [
    {"tool_name": "open_valve", "arguments": {}},
    {"tool_name": "override_interlock", "arguments": {}},
    {"tool_name": "write_register", "arguments": {"addr": 1, "value": 2}},
    {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-09", "setpoint_c": 20.0}},
    {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": 24.0}},
    {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": 20.55}},
    {"tool_name": "set_zone_setpoint",
     "arguments": {"zone_id": "ZONE-01", "setpoint_c": 20.0, "operation_id": "agent-chosen"}},
])
def test_tool_surface_refuses(call):
    assert not TOOL.is_valid(call)


def test_receipts_match_schema(twin):
    v = jsonschema.Draft202012Validator(SCHEMAS["effect-receipt-v1.schema.json"])
    ok, _ = twin.apply_setpoint(cmd(twin, "op-schema-0001", sp=20.5))
    bad, _ = twin.apply_setpoint(cmd(twin, "op-schema-0002", sp=30.0))
    for rec in (ok, bad):
        v.validate(rec.to_dict() | {"replayed": False})
