"""runner.work_order: the normalized call and the signed work order (authority-fixture contract).

- the normalized call is derived from a proposal's tool call, validates against its schema, and its
  decision time is the episode's start plus simulated seconds (never the wall clock);
- a work order signed with the test-only key validates against its schema and verifies; any change
  to a signed member, an unknown issuer or another key's signature is refused;
- the signature covers the RFC 8785 JCS form, so key order and 20 vs 20.0 do not change it;
- the delegation binding is the SHA-256 of the delegation's JCS form, and a different delegation is refused;
- the check refuses outside the window (valid_until exclusive), on any field mismatch, and outside the
  inclusive value range, naming every reason; it passes the in-scope, in-range call;
- the five cases proposed in issue #27, as far as the work-order side decides them: case 5 is refused
  here for its value; cases 1 to 4 pass here (2 to 4 are APS's to refuse, not this check's).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from runner import jcs
from runner import work_order as wo

ROOT = Path(__file__).resolve().parents[2]
CALL_SCHEMA = json.loads((ROOT / "contracts" / "ot-normalized-call-v1.schema.json").read_text("utf-8"))
WO_SCHEMA = json.loads((ROOT / "contracts" / "ot-work-order-v1.schema.json").read_text("utf-8"))
KEYS = wo.issuer_test_keys()
DELEGATION = {"delegation_id": "DLG-0001", "scope": {"building_id": "demo-building-001", "assets": ["ZONE-01"],
                                                    "parameters": ["setpoint_c"], "operations": ["set_zone_setpoint"]},
              "signatures": [{"alg": "Ed25519", "sig": "placeholder-from-APS"}]}


def _call(value=20.5, zone="ZONE-01", when="2026-01-15T07:00:00Z"):
    return wo.normalized_call({"tool_name": "set_zone_setpoint", "arguments": {"zone_id": zone, "setpoint_c": value}},
                              building_id="demo-building-001", decision_time_utc=when)


def _order(**over):
    base = {"schema": wo.SCHEMA, "work_order_id": "WO-AF-0001", "building_id": "demo-building-001",
            "asset": "ZONE-01", "parameter": "setpoint_c", "operation": "set_zone_setpoint",
            "value_range": {"min": 19.0, "max": 21.0, "unit": "degC"},
            "valid_from": "2026-01-15T00:00:00Z", "valid_until": "2026-01-16T00:00:00Z",
            "delegation_id": "DLG-0001", "delegation_sha256": wo.delegation_sha256(DELEGATION)}
    base.update(over)
    return wo.sign(base, wo.issuer_test_key(), wo.TEST_ISSUER)


def test_normalized_call_and_decision_time():
    c = _call()
    jsonschema.validate(c, CALL_SCHEMA)
    assert c == {"schema": "ot-normalized-call/v1", "building_id": "demo-building-001", "asset": "ZONE-01",
                 "parameter": "setpoint_c", "operation": "set_zone_setpoint", "value": 20.5, "unit": "degC",
                 "decision_time": "2026-01-15T07:00:00Z"}
    assert wo.decision_time("2026-01-15T00:00:00Z", 7 * 3600) == "2026-01-15T07:00:00Z"
    assert wo.decision_time("2026-01-15T00:00:00Z", 86400 + 61) == "2026-01-16T00:01:01Z"
    for bad in ({"tool_name": "set_tank_setpoint", "arguments": {}},
                {"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": True}}):
        with pytest.raises(ValueError):
            wo.normalized_call(bad, building_id="b", decision_time_utc="2026-01-15T07:00:00Z")
    with pytest.raises(ValueError):
        wo.normalized_call({"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "Z", "setpoint_c": 20}},
                           building_id="b", decision_time_utc="2026-01-15 07:00")


def test_signed_order_validates_verifies_and_is_tamper_evident():
    o = _order()
    jsonschema.validate(o, WO_SCHEMA)
    assert o["issuer"] == wo.TEST_ISSUER and wo.verify_signature(o, KEYS) is None
    for path, value in ((("asset",), "ZONE-03"), (("value_range", "max"), 25.0), (("valid_until",), "2027-01-01T00:00:00Z"),
                        (("delegation_sha256",), "0" * 64)):
        t = copy.deepcopy(o)
        d = t
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
        assert wo.verify_signature(t, KEYS) == "signature_invalid", path
    assert wo.verify_signature({**o, "issuer": "someone-else"}, KEYS) == "unknown_issuer"
    other = wo.sign({k: v for k, v in o.items() if k != "signature"}, wo.issuer_test_key("other"), wo.TEST_ISSUER)
    assert wo.verify_signature(other, KEYS) == "signature_invalid"
    assert wo.verify_signature({k: v for k, v in o.items() if k != "signature"}, KEYS) == "signature_missing"


def test_signature_covers_the_jcs_form():
    o = _order(value_range={"min": 19, "max": 21, "unit": "degC"})
    same = dict(reversed(list(o.items())))
    same["value_range"] = {"unit": "degC", "max": 21.0, "min": 19.0}
    assert wo.verify_signature(same, KEYS) is None
    assert wo.signing_bytes(o) == jcs.canonicalize({k: v for k, v in o.items() if k != "signature"})


def test_delegation_binding_is_the_jcs_sha256():
    assert wo.delegation_sha256(DELEGATION) == jcs.sha256_hex(DELEGATION)
    assert wo.check(_order(), _call(), KEYS, delegation=DELEGATION)["verdict"] == "pass"
    changed = copy.deepcopy(DELEGATION)
    changed["scope"]["assets"].append("ZONE-02")
    r = wo.check(_order(), _call(), KEYS, delegation=changed)
    assert r == {"verdict": "refuse", "reasons": ["delegation_digest_mismatch"]}


@pytest.mark.parametrize("call, reasons", [
    (dict(when="2026-01-14T23:59:59Z"), ["not_yet_valid"]),
    (dict(when="2026-01-16T00:00:00Z"), ["expired"]),          # valid_until is exclusive
    (dict(zone="ZONE-03"), ["asset_mismatch"]),
    (dict(value=18.9), ["value_below_range"]),
    (dict(value=21.01), ["value_above_range"]),
    (dict(value=21.0), []),                                    # the range is inclusive
    (dict(value=19, when="2026-01-15T00:00:00Z"), []),         # valid_from is inclusive; an int value is fine
])
def test_window_fields_and_range(call, reasons):
    r = wo.check(_order(), _call(**call), KEYS)
    assert r["reasons"] == reasons and r["verdict"] == ("refuse" if reasons else "pass")


def test_every_reason_is_named_and_malformed_orders_are_refused():
    c = {**_call(value=30.0, zone="ZONE-02", when="2026-02-01T00:00:00Z"), "building_id": "other", "unit": "K"}
    r = wo.check(_order(), c, KEYS)
    assert r["verdict"] == "refuse"
    assert set(r["reasons"]) == {"expired", "building_id_mismatch", "asset_mismatch", "unit_mismatch",
                                 "value_above_range"}
    o = _order()
    del o["delegation_id"]
    assert wo.check(o, _call(), KEYS) == {"verdict": "refuse", "reasons": ["malformed"], "missing": ["delegation_id"]}
    assert wo.check(_order(value_range={"min": 22.0, "max": 21.0, "unit": "degC"}), _call(), KEYS)["reasons"] == ["malformed_range"]


def test_the_five_issue_27_cases_on_the_work_order_side():
    """Only the work-order side: cases 2 to 4 are delegation faults APS refuses; here they pass."""
    o = _order()
    in_scope = _call(value=20.5)
    cases = {1: in_scope, 2: in_scope, 3: in_scope, 4: in_scope, 5: _call(value=22.5)}
    got = {n: wo.check(o, c, KEYS, delegation=DELEGATION)["verdict"] for n, c in cases.items()}
    assert got == {1: "pass", 2: "pass", 3: "pass", 4: "pass", 5: "refuse"}


def test_test_keys_are_deterministic_and_labelled():
    assert wo.issuer_test_keys() == wo.issuer_test_keys()
    assert len(wo.issuer_test_keys("a", "b")) == 2 and wo.issuer_test_keys("a")["a"] != wo.issuer_test_keys("b")["b"]
    assert "TEST-ONLY" in wo.TEST_KEY_LABEL
