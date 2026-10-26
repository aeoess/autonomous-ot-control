from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from twin.api import Tokens
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "twin" / "fixtures" / "three-zone-v1.json"
BASELINE = ROOT / "scenarios" / "baseline-day-v1.json"
STALE = ROOT / "scenarios" / "stale-observation-v1.json"
VECTORS = ROOT / "scenarios" / "blocked-overlimit-v1.json"

TEST_ENV = {
    "TWIN_READ_TOKEN": "test-read-token-0123456789abcdef",
    "TWIN_ACTUATOR_TOKEN": "test-actuator-token-0123456789abcd",
    "TWIN_CONTROL_TOKEN": "test-control-token-0123456789abcde",
}


@pytest.fixture
def config() -> dict:
    return copy.deepcopy(load_config(CONFIG))


@pytest.fixture
def twin(config) -> Twin:
    return Twin(config, load_scenario(BASELINE))


@pytest.fixture
def tokens() -> Tokens:
    return Tokens.from_env(TEST_ENV)


@pytest.fixture
def vectors() -> list[dict]:
    return json.loads(VECTORS.read_text())["vectors"]


def cmd(twin: Twin, op: str, zone: str = "ZONE-01", sp: float = 20.5, **kw) -> dict:
    body = {"schema": "ot-actuation-command/v1", "operation_id": op, "zone_id": zone,
            "setpoint_c": sp, "units": "degC", "expected_state_revision": twin.revision}
    body.update(kw)
    return body


def plant_fingerprint(twin: Twin) -> tuple:
    return (twin.revision, tuple((z, s.setpoint_c, tuple(s.change_history_s))
                                 for z, s in sorted(twin.zones.items())))
