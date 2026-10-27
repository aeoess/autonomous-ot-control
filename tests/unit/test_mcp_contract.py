"""The customer MCP contract can never reach the plant except through REMORA.

contracts/mcp/ot-caretaker-mcp-v1.openapi.json is a design contract (ARCH-OT-008
§13). These checks pin its safety properties before any server exists:

* every tool declares plant_effect "none";
* no tool calls a REMORA execute route;
* every tool needs a scope, and approval is its own step-up scope;
* every schema reference resolves;
* the document agrees with the architecture document's tool table.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "contracts" / "mcp" / "ot-caretaker-mcp-v1.openapi.json").read_text("utf-8"))
ARCH = (ROOT / "docs" / "architecture" / "autonomous-runtime-cloudflare.md").read_text("utf-8")
TOOLS = SPEC["x-mcp-tools"]
SCOPES = set(SPEC["components"]["securitySchemes"]["oauth2"]["flows"]["authorizationCode"]["scopes"])


def test_openapi_shape():
    assert SPEC["openapi"].startswith("3.1")
    assert "/mcp" in SPEC["paths"] and "/.well-known/oauth-protected-resource" in SPEC["paths"]
    assert SPEC["security"], "OAuth must be required by default"
    assert SPEC["paths"]["/.well-known/oauth-protected-resource"]["get"]["security"] == []


def test_no_tool_touches_the_plant_or_an_execute_route():
    forbidden = SPEC["x-forbidden-remora-routes"]
    for t in TOOLS:
        assert t["plant_effect"] == "none", t["name"]
        assert "execute" not in t["remora_call"] and "limits/resolve" not in t["remora_call"], t["name"]
        assert "execute" not in t["name"]
    assert {"/v1/execution/execute", "/v1/execution/execute-accepted"} <= set(forbidden)


def test_every_tool_has_a_scope_and_approval_is_step_up():
    for t in TOOLS:
        assert t["scope"] in SCOPES, t["name"]
    approvers = {t["name"] for t in TOOLS if t["scope"] == "changes.approve"}
    assert approvers == {"confirm_change", "reject_change"}
    assert "Step-up" in SPEC["components"]["securitySchemes"]["oauth2"]["flows"][
        "authorizationCode"]["scopes"]["changes.approve"]


def test_every_ref_resolves():
    text = json.dumps(SPEC)
    for ref in set(re.findall(r'"\$ref": "#/components/schemas/([A-Za-z0-9]+)"', text)):
        assert ref in SPEC["components"]["schemas"], ref


def test_the_architecture_table_lists_the_same_tools():
    table = set(re.findall(r"^\| `([a-z_]+)` \| `[a-z.]+`", ARCH, flags=re.M))
    assert table == {t["name"] for t in TOOLS}


def test_propose_tools_only_name_catalogued_tools():
    catalogue = json.loads((ROOT / "twin" / "fixtures" / "datapoints-v1.json").read_text("utf-8")) \
        if (ROOT / "twin" / "fixtures" / "datapoints-v1.json").exists() else None
    enum = SPEC["components"]["schemas"]["ProposeChangeInput"]["properties"]["tool"]["enum"]
    if catalogue is not None:
        assert set(enum) == {t["id"] for t in catalogue["tools"]}
    assert "set_zone_setpoint" in enum
