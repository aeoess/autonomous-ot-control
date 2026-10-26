"""OT-003: the signed ToolSpec loads, mirrors the guard, and fails closed."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from adapter.contracts import DEFAULT_TOOLSPEC_PATH, ToolSpec, ToolSpecError
from twin.guard import GuardConfig

ROOT = Path(__file__).resolve().parents[2]
SPEC_SCHEMA = jsonschema.Draft202012Validator(
    json.loads((ROOT / "contracts" / "ot-toolspec-v1.schema.json").read_text()))
FIXTURE = json.loads((ROOT / "twin" / "fixtures" / "three-zone-v1.json").read_text())


@pytest.fixture
def spec() -> ToolSpec:
    return ToolSpec.load()


def test_committed_toolspec_matches_its_schema():
    SPEC_SCHEMA.validate(json.loads(DEFAULT_TOOLSPEC_PATH.read_text()))


def test_committed_toolspec_is_unsigned():
    # A real signature/key must never be committed (SECURITY.md).
    sig = json.loads(DEFAULT_TOOLSPEC_PATH.read_text())["signature"]
    assert sig["key_id"] == "NOT_ESTABLISHED" and sig["value"] == "NOT_ESTABLISHED"


def test_toolspec_limits_mirror_twin_guard(spec):
    g = GuardConfig.from_config(FIXTURE)
    assert spec.limits.to_dict() == {
        "max_delta_c": g.max_delta_c, "min_interval_seconds": g.min_interval_seconds,
        "max_changes_per_hour": g.max_changes_per_hour,
        "max_observation_age_seconds": g.max_observation_age_seconds}


def test_toolspec_bounds_within_zone_bounds(spec):
    for z in FIXTURE["zones"]:
        assert spec.min_setpoint_c >= z["min_setpoint_c"]
        assert spec.max_setpoint_c <= z["max_setpoint_c"]


def test_digest_is_stable_and_ignores_signature(spec):
    d1 = spec.digest
    other = copy.deepcopy(spec.raw)
    other["signature"] = {"algorithm": "HMAC-SHA256", "key_id": "k1", "value": "deadbeef"}
    assert ToolSpec.load(other).digest == d1  # signature not part of the attested bytes


def test_attestation_roundtrip_and_tamper(spec):
    mac = spec.attest("dev-signing-key")
    assert spec.verify_attestation("dev-signing-key", mac)
    assert not spec.verify_attestation("wrong-key", mac)
    tampered = copy.deepcopy(spec.raw)
    tampered["tools"][2]["arguments"]["setpoint_c"]["maximum"] = 25.0
    assert not ToolSpec.load(tampered).verify_attestation("dev-signing-key", mac)


@pytest.mark.parametrize("args,canon", [
    ({"zone_id": "ZONE-01", "setpoint_c": 20.5}, {"zone_id": "ZONE-01", "setpoint_c": 20.5}),
    ({"zone_id": "ZONE-02", "setpoint_c": 21}, {"zone_id": "ZONE-02", "setpoint_c": 21.0}),
])
def test_canonical_arguments_ok(spec, args, canon):
    assert spec.canonical_setpoint_arguments(args) == canon


@pytest.mark.parametrize("args,match", [
    ({"zone_id": "ZONE-99", "setpoint_c": 20.5}, "not in signed"),
    ({"zone_id": "ZONE-01", "setpoint_c": 25.0}, "outside signed bounds"),
    ({"zone_id": "ZONE-01", "setpoint_c": 20.55}, "resolution"),
    ({"zone_id": "ZONE-01", "setpoint_c": float("nan")}, "finite"),
    ({"zone_id": "ZONE-01", "setpoint_c": True}, "finite"),
    ({"zone_id": "ZONE-01", "setpoint_c": "20.5"}, "finite"),
    ({"zone_id": "ZONE-01", "setpoint_c": 20.5, "tenant": "x"}, "unexpected argument"),
    ({"zone_id": "ZONE-01"}, "finite"),
])
def test_canonical_arguments_fail_closed(spec, args, match):
    with pytest.raises(ToolSpecError, match=match):
        spec.canonical_setpoint_arguments(args)


def test_malformed_specs_rejected():
    for mutate in (
        lambda s: s.update(schema="ot-toolspec/v2"),
        lambda s: s["tools"].pop(),
        lambda s: s["tools"][2].update(effect="READ"),
        lambda s: s["tools"][2].pop("limits"),
    ):
        raw = copy.deepcopy(ToolSpec.load().raw)
        mutate(raw)
        with pytest.raises(ToolSpecError):
            ToolSpec.load(raw)
