"""OT-003: the deployment-owned REMORA tool registry (deploy/ot/ot_registry.py).

Verifies the module matches REMORA's real registration contract — it exposes
``register_tools(register)`` and registers ``fn(arguments)->dict`` callables —
and that the write callable applies exactly one governed effect, bound to the
injected REMORA proposal id. The module is loaded by path because it is a
deployment module (copied into the REMORA image), not an installed package. It
imports only REMORA-free helpers, so this runs offline.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapter.actuation import ActuationError, ActuatorClient
from adapter.contracts import ToolSpec
from tests.conftest import plant_fingerprint
from twin.api import build_actuator_app, build_read_app

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = "remora-proposal-abc12345"


def _load_module():
    path = ROOT / "deploy" / "ot" / "ot_registry.py"
    spec = importlib.util.spec_from_file_location("ot_registry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def otreg():
    return _load_module()


@pytest.fixture
def tools(otreg, twin, tokens):
    read_client = TestClient(build_read_app(twin, tokens))
    actuator = ActuatorClient("http://testserver", tokens.actuator,
                              client=TestClient(build_actuator_app(twin, tokens)))
    return otreg.OtTools(actuator=actuator, read_base_url="http://testserver",
                         read_token=tokens.read, toolspec=ToolSpec.load(),
                         read_client=read_client, proposal_id=lambda: PROPOSAL)


def test_register_tools_matches_remora_contract(otreg, tools, monkeypatch):
    monkeypatch.setattr(otreg, "_TOOLS", tools)
    registered: dict[str, object] = {}
    otreg.register_tools(lambda name, fn: registered.__setitem__(name, fn))
    assert set(registered) == {"read_zone_state", "read_building_energy", "set_zone_setpoint"}
    # Every registered callable takes a single `arguments` dict (fn(arguments)->dict).
    out = registered["read_zone_state"]({"zone_id": "ZONE-01"})
    assert out["zone_id"] == "ZONE-01" and "setpoint_c" in out


def test_write_callable_applies_one_effect_bound_to_proposal(tools, twin):
    rec = tools.set_zone_setpoint({"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert rec["status"] == "APPLIED" and rec["operation_id"] == PROPOSAL
    assert twin.revision == 1 and twin.zones["ZONE-01"].setpoint_c == 20.5
    # Re-invocation of the same proposal is idempotent (INV-04).
    fp = plant_fingerprint(twin)
    rec2 = tools.set_zone_setpoint({"zone_id": "ZONE-01", "setpoint_c": 20.5})
    assert rec2["replayed"] is True and plant_fingerprint(twin) == fp and twin.revision == 1


def test_write_callable_fails_closed_on_bad_argument(tools, twin):
    with pytest.raises(ActuationError):
        tools.set_zone_setpoint({"zone_id": "ZONE-01", "setpoint_c": 25.0})
    assert twin.revision == 0 and not twin.operations


def test_read_tools(tools):
    energy = tools.read_building_energy({})
    assert energy["estimate"] is True and energy["unit_energy"] == "kWh"
    with pytest.raises(ValueError):
        tools.read_building_energy({"zone_id": "ZONE-01"})  # takes no args
    with pytest.raises(ValueError):
        tools.read_zone_state({"zone_id": "ZONE-99"})       # unknown zone


def test_limit_hooks(tools, otreg, twin, monkeypatch):
    monkeypatch.setattr(otreg, "_TOOLS", tools)
    # observed meter reads the plant's own setpoint.
    assert otreg.observe_limit_value("ot-pilot", "setpoint_movement", "ZONE-01") == \
        twin.zones["ZONE-01"].setpoint_c
    with pytest.raises(KeyError):
        otreg.observe_limit_value("ot-pilot", "valve_travel", "V-1")
    # evidence hook resolves UNKNOWN from the twin operation log.
    tools.set_zone_setpoint({"zone_id": "ZONE-02", "setpoint_c": 20.5})
    ev = otreg.check_limit_evidence("ot-pilot", {}, f"twin-op:{PROPOSAL}")
    assert ev["executed"] is True and ev["source"] == "twin-operation-log"
    assert otreg.check_limit_evidence("ot-pilot", {}, "twin-op:never")["executed"] is False
    with pytest.raises(ValueError):
        otreg.check_limit_evidence("ot-pilot", {}, "bad-ref")
