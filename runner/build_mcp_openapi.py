"""Build contracts/mcp/ot-caretaker-mcp-v1.openapi.json (ARCH-OT-008 section 13).

    python -m runner.build_mcp_openapi

The tool enum must equal the governed tools in twin/datapoints.py;
tests/unit/test_mcp_contract.py checks it.
"""
import json
from pathlib import Path

ZONES = ["ZONE-01", "ZONE-02", "ZONE-03"]
REF = "#/components/schemas/"


def obj(props, required=(), extra=False):
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": extra}


decision = {
    "type": "object",
    "description": "What REMORA decided, verbatim from /v1/execution/assess, plus the caretaker's estimate.",
    "properties": {
        "proposal_id": {"type": "string"},
        "decision": {"type": "string", "enum": ["accept", "verify", "abstain", "escalate"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "review_item_id": {"type": ["string", "null"]},
        "required_role": {"type": ["string", "null"],
                          "description": "Role REMORA requires to approve (risk profile)."},
        "resolution_plan": {"type": ["object", "null"]},
        "refusal": {"type": ["string", "null"],
                    "description": "A 409 reason code when REMORA refused before deciding (toolspec_*, limit_*)."},
        "expected_effect": {"$ref": REF + "ExpectedEffect"},
        "evidence_ref": {"type": "string", "description": "R2 key of the proposal's evidence."},
    },
    "required": ["decision", "reasons"],
    "additionalProperties": False,
}

tools = {
    "get_building_status": {
        "scope": "building.read", "remora_call": "none", "plant_effect": "none",
        "description": "Current mode, zone temperatures and setpoints, plant state, active plan, open alerts.",
        "input": obj({"site_id": {"type": "string"}}, ["site_id"]),
        "output": {"$ref": REF + "BuildingStatus"}},
    "get_datapoints": {
        "scope": "building.read", "remora_call": "none", "plant_effect": "none",
        "description": "Latest values of catalogued data points (docs/datapoints.md), with sample time and quality.",
        "input": obj({"site_id": {"type": "string"},
                      "point_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                      "system": {"type": "string"}}, ["site_id"]),
        "output": obj({"points": {"type": "array", "items": {"$ref": REF + "PointValue"}}}, ["points"])},
    "get_prices_and_forecast": {
        "scope": "building.read", "remora_call": "none", "plant_effect": "none",
        "description": "Day-ahead spot prices and the weather forecast the caretaker planned on, with snapshot SHA-256.",
        "input": obj({"site_id": {"type": "string"}, "date": {"type": "string", "format": "date"}},
                     ["site_id", "date"]),
        "output": {"$ref": REF + "Inputs"}},
    "get_report": {
        "scope": "reports.read", "remora_call": "none", "plant_effect": "none",
        "description": "Energy, cost, comfort, hazards and savings against the reference schedule, for a period.",
        "input": obj({"site_id": {"type": "string"},
                      "period": {"type": "string", "enum": ["day", "week", "month"]},
                      "start": {"type": "string", "format": "date"},
                      "kind": {"type": "string", "enum": ["energy", "savings", "comfort", "decisions"]}},
                     ["site_id", "period", "start", "kind"]),
        "output": {"$ref": REF + "Report"}},
    "get_decision": {
        "scope": "reports.read", "remora_call": "GET /v1/execution/proposals/{id}/lifecycle and /evidence",
        "plant_effect": "none",
        "description": "The full audit trail of one decision: ledger row, REMORA lifecycle and evidence manifest, effect verification.",
        "input": obj({"proposal_id": {"type": "string"}}, ["proposal_id"]),
        "output": {"$ref": REF + "DecisionTrail"}},
    "list_recommendations": {
        "scope": "reports.read", "remora_call": "none", "plant_effect": "none",
        "description": "Measures the caretaker suggests, each with expected NOK, comfort effect, confidence and evidence refs. A recommendation changes nothing until proposed and confirmed.",
        "input": obj({"site_id": {"type": "string"},
                      "horizon": {"type": "string", "enum": ["today", "week", "season"]}}, ["site_id"]),
        "output": obj({"recommendations": {"type": "array", "items": {"$ref": REF + "Recommendation"}}},
                      ["recommendations"])},
    "propose_change": {
        "scope": "changes.propose", "remora_call": "POST /v1/execution/assess (principal customer-agent:<tenant>)",
        "plant_effect": "none",
        "description": "Ask REMORA to assess one governed change. Never dispatches. The customer's words become the work order text, so REMORA can ground literal values.",
        "input": obj({"site_id": {"type": "string"},
                      "tool": {"type": "string", "enum": [
                          "set_zone_setpoint", "set_zone_cooling_setpoint", "set_zone_co2_setpoint",
                          "set_zone_lighting_setpoint", "set_zone_lighting_mode", "set_supply_air_setpoint", "set_tank_setpoint",
                          "set_heat_pump_limit", "set_ahu_enabled", "set_heating_cable_mode"]},
                      "arguments": {"type": "object",
                                    "description": "Validated against the tool's schema in docs/datapoints.md and REMORA's signed ToolSpec."},
                      "customer_request": {"type": "string", "maxLength": 1000,
                                           "description": "The customer's own words, recorded verbatim in the work order."},
                      "when": {"type": "string", "format": "date-time",
                               "description": "Earliest execution time; the caretaker schedules the dispatch."}},
                     ["site_id", "tool", "arguments", "customer_request"]),
        "output": decision},
    "propose_procedure": {
        "scope": "changes.propose", "remora_call": "POST /v1/execution/assess of a plan work order",
        "plant_effect": "none",
        "description": "Propose a multi-step procedure (ADR-007 PR-01..05) as one plan; each step stays individually governed.",
        "input": obj({"site_id": {"type": "string"},
                      "procedure_id": {"type": "string", "enum": ["PR-01", "PR-02", "PR-03", "PR-04", "PR-05"]},
                      "parameters": {"type": "object"},
                      "customer_request": {"type": "string", "maxLength": 1000}},
                     ["site_id", "procedure_id", "parameters", "customer_request"]),
        "output": decision},
    "confirm_change": {
        "scope": "changes.approve", "remora_call": "POST /v1/execution/approve (principal customer-approver:<tenant>, after step-up)",
        "plant_effect": "none",
        "description": "Approve a pending review item as the customer's approving principal. Requires a step-up token. The caretaker executes later through REMORA.",
        "input": obj({"review_item_id": {"type": "string"},
                      "ttl_seconds": {"type": "integer", "minimum": 60, "maximum": 86400}},
                     ["review_item_id"]),
        "output": obj({"status": {"type": "string"}, "proposal_id": {"type": "string"},
                       "expires_at": {"type": "string", "format": "date-time"},
                       "scheduled_for": {"type": ["string", "null"], "format": "date-time"},
                       "audit": {"$ref": REF + "AuditRef"}}, ["status", "proposal_id", "audit"])},
    "reject_change": {
        "scope": "changes.approve", "remora_call": "POST /v1/execution/reject", "plant_effect": "none",
        "description": "Refuse a pending review item, with a mandatory reason.",
        "input": obj({"review_item_id": {"type": "string"}, "reason": {"type": "string", "minLength": 3}},
                     ["review_item_id", "reason"]),
        "output": obj({"status": {"type": "string"}, "audit": {"$ref": REF + "AuditRef"}}, ["status"])},
    "get_change_status": {
        "scope": "reports.read", "remora_call": "GET /v1/execution/proposals/{id} and /lifecycle",
        "plant_effect": "none",
        "description": "Where a change is: assessed, waiting, approved, dispatched, APPLIED/refused/UNKNOWN, effect verified.",
        "input": obj({"proposal_id": {"type": "string"}}, ["proposal_id"]),
        "output": obj({"proposal_id": {"type": "string"},
                       "state": {"type": "string", "enum": ["ASSESSED", "WAITING_APPROVAL", "APPROVED",
                                                            "DISPATCHED", "APPLIED", "REFUSED", "UNKNOWN",
                                                            "EFFECT_VERIFIED", "EFFECT_MISMATCH",
                                                            "EFFECT_UNOBSERVABLE"]},
                       "observed": {"type": ["object", "null"]},
                       "evidence_ref": {"type": ["string", "null"]}}, ["proposal_id", "state"])},
}

schemas = {
    "AuditRef": obj({"sequence_no": {"type": ["integer", "null"]}, "entry_hash": {"type": ["string", "null"]},
                     "deferred": {"type": "boolean"}, "idempotency_key": {"type": ["string", "null"]}}),
    "ExpectedEffect": obj({"cost_delta_nok": {"type": "number"}, "comfort_delta_dh": {"type": "number"},
                           "basis": {"type": "string", "description": "Model, inputs and snapshot SHA-256 the estimate used; always synthetic."},
                           "estimate": {"type": "boolean", "const": True}}, ["estimate"]),
    "PointValue": obj({"id": {"type": "string"}, "value": {"type": ["number", "boolean", "string"]},
                       "unit": {"type": "string"}, "sample_at": {"type": "string", "format": "date-time"},
                       "quality": {"type": "string", "enum": ["GOOD", "STALE", "UNOBSERVABLE"]}},
                      ["id", "value", "unit", "sample_at", "quality"]),
    "BuildingStatus": obj({"site_id": {"type": "string"}, "mode": {"type": "string"},
                           "zones": {"type": "array", "items": obj({"zone_id": {"type": "string", "enum": ZONES},
                                                                     "air_temp_c": {"type": "number"},
                                                                     "heating_setpoint_c": {"type": "number"},
                                                                     "co2_ppm": {"type": "number"}})},
                           "active_plan": {"type": ["string", "null"]},
                           "alerts": {"type": "array", "items": {"type": "string"}},
                           "fictitious": {"type": "boolean"}}, ["site_id", "mode", "zones"]),
    "Inputs": obj({"price_area": {"type": "string"}, "prices_nok_per_kwh": {"type": "array", "items": {"type": "number"}},
                   "forecast_outdoor_c": {"type": "array", "items": {"type": "number"}},
                   "snapshots": {"type": "array", "items": obj({"kind": {"type": "string"}, "sha256": {"type": "string"}})},
                   "limitations": {"type": "array", "items": {"type": "string"}}}),
    "Report": obj({"period": {"type": "string"}, "energy_kwh": {"type": "number"}, "cost_nok": {"type": "number"},
                   "reference_cost_nok": {"type": "number"}, "savings_nok": {"type": "number"},
                   "comfort_degree_hours": {"type": "number"}, "hazard_hours": {"type": "number"},
                   "decisions": {"type": "integer"}, "scope": {"type": "string"},
                   "estimate": {"type": "boolean", "const": True}}, ["period", "estimate", "scope"]),
    "DecisionTrail": obj({"proposal_id": {"type": "string"}, "ledger_row": {"type": "object"},
                          "lifecycle": {"type": "array", "items": {"type": "object"}},
                          "evidence_manifest": {"type": "object"}, "effect": {"type": ["object", "null"]},
                          "audit_chain_valid": {"type": "boolean"}}, ["proposal_id", "ledger_row", "lifecycle"]),
    "Recommendation": obj({"id": {"type": "string"}, "title": {"type": "string"},
                           "procedure_id": {"type": ["string", "null"]},
                           "proposed_calls": {"type": "array", "items": {"type": "object"}},
                           "expected_effect": {"$ref": REF + "ExpectedEffect"},
                           "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                           "evidence_refs": {"type": "array", "items": {"type": "string"}}},
                          ["id", "title", "expected_effect", "confidence"]),
    "JsonRpcRequest": obj({"jsonrpc": {"type": "string", "const": "2.0"}, "id": {"type": ["string", "integer"]},
                           "method": {"type": "string", "enum": ["initialize", "tools/list", "tools/call", "ping"]},
                           "params": {"type": "object"}}, ["jsonrpc", "method"], extra=False),
    "JsonRpcResponse": {"type": "object", "properties": {"jsonrpc": {"const": "2.0"}, "id": {},
                                                          "result": {"type": "object"}, "error": {"type": "object"}}},
}
for name, t in tools.items():
    key = "".join(w.capitalize() for w in name.split("_"))
    schemas[key + "Input"] = t["input"]
    schemas[key + "Output"] = t["output"]

doc = {
    "openapi": "3.1.0",
    "info": {
        "title": "ot-caretaker MCP server",
        "version": "1.0.0-draft",
        "summary": "Remote MCP server for a customer to read, ask and propose; REMORA decides and executes.",
        "description": (
            "Transport: MCP Streamable HTTP (JSON-RPC 2.0 over POST /mcp). Authorization: OAuth 2.1 "
            "(MCP authorization), mandatory for every call. The tools are listed under x-mcp-tools with "
            "their input/output schemas, required scope, the single REMORA call each makes, and their "
            "plant effect, which is 'none' for every tool: no MCP tool dispatches. State-changing calls "
            "are limited to REMORA assess (propose), approve and reject; execution happens in the "
            "caretaker through REMORA's protected dispatcher. Design specification; see "
            "docs/architecture/autonomous-runtime-cloudflare.md section 13."),
        "license": {"name": "Not yet decided (SDD D-01)"},
    },
    "servers": [{"url": "https://mcp.{domain}", "variables": {"domain": {"default": "example.invalid"}}}],
    "security": [{"oauth2": ["building.read"]}],
    "paths": {
        "/.well-known/oauth-protected-resource": {"get": {
            "summary": "Protected resource metadata (RFC 9728): names the authorization server.",
            "security": [],
            "responses": {"200": {"description": "Resource metadata",
                                  "content": {"application/json": {"schema": obj({
                                      "resource": {"type": "string"},
                                      "authorization_servers": {"type": "array", "items": {"type": "string"}},
                                      "scopes_supported": {"type": "array", "items": {"type": "string"}}},
                                      ["resource", "authorization_servers"], extra=True)}}}}}},
        "/mcp": {"post": {
            "summary": "MCP JSON-RPC endpoint (initialize, tools/list, tools/call).",
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": REF + "JsonRpcRequest"}}}},
            "responses": {
                "200": {"description": "JSON-RPC result or error",
                        "content": {"application/json": {"schema": {"$ref": REF + "JsonRpcResponse"}},
                                    "text/event-stream": {"schema": {"type": "string"}}}},
                "401": {"description": "No or invalid token; WWW-Authenticate names the resource metadata URL."},
                "403": {"description": "insufficient_scope; the WWW-Authenticate header names the scopes needed (step-up for changes.approve)."}}}},
    },
    "components": {
        "securitySchemes": {"oauth2": {"type": "oauth2", "flows": {"authorizationCode": {
            "authorizationUrl": "https://auth.{domain}/authorize",
            "tokenUrl": "https://auth.{domain}/token",
            "scopes": {
                "building.read": "Read building status, data points, prices and forecasts.",
                "reports.read": "Read reports, recommendations, decisions and their evidence.",
                "changes.propose": "Ask REMORA to assess a change. Never executes.",
                "changes.approve": "Approve or reject a pending change as the customer's approver. Step-up only: never granted with other scopes, fresh sign-in each time."}}}}},
        "schemas": schemas,
    },
    "x-mcp-tools": [
        {"name": n, "description": t["description"], "scope": t["scope"], "remora_call": t["remora_call"],
         "plant_effect": t["plant_effect"],
         "inputSchema": {"$ref": REF + "".join(w.capitalize() for w in n.split("_")) + "Input"},
         "outputSchema": {"$ref": REF + "".join(w.capitalize() for w in n.split("_")) + "Output"}}
        for n, t in tools.items()],
    "x-forbidden-remora-routes": ["/v1/execution/execute", "/v1/execution/execute-accepted",
                                  "/v1/execution/limits/resolve"],
}
out = Path("contracts/mcp/ot-caretaker-mcp-v1.openapi.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
print(len(tools), "tools")
