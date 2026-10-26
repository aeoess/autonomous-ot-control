"""OT-003/OT-004: deployment-owned artifacts are present and well-formed.

Structural checks only (no REMORA import): the intent source and limits policy
parse and cover the frozen v1 write tool, and the images/compose reference the
real REMORA runtime contract verified at the pinned commit.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OT = ROOT / "deploy" / "ot"


def test_intent_source_parses_and_has_demo_order():
    table = json.loads((OT / "ot_intents.json").read_text())
    assert "WORK-ORDER-001" in table
    assert table["WORK-ORDER-001"]["task_text"]


def test_limits_policy_meters_the_write_tool():
    pol = json.loads((OT / "ot_limits.policy.json").read_text())
    assert pol["schema"] == "remora-cumulative-limits/v1"
    assert pol["coverage"] == "all_writes"
    assert "set_zone_setpoint" in pol["meters"]
    zones = {lim["resource"] for lim in pol["limits"]}
    assert {"ZONE-01", "ZONE-02", "ZONE-03"} <= zones


def test_remora_image_runs_the_real_server_and_registry():
    df = (ROOT / "deploy" / "Dockerfile.remora-ot").read_text()
    assert 'CMD ["uvicorn", "servers.api:app"' in df
    assert "REMORA_TOOL_REGISTRY_MODULE=ot_registry" in df
    assert 'pip install -e "/opt/remora[api,postgres,security]"' in df


def test_governed_image_installs_the_sdk():
    df = (ROOT / "deploy" / "Dockerfile.governed").read_text()
    assert "pip install --no-deps /opt/remora" in df
    assert "runner.governed" in df


def test_compose_uses_real_remora_env_contract():
    compose = (ROOT / "compose.yaml").read_text()
    for key in ("REMORA_PG_DSN:", "REMORA_CONTROL_PLANE_DSN:", "REMORA_API_TOKENS:",
                "REMORA_ENV: production", "REMORA_ENABLED_SURFACES: execution",
                "REMORA_ENVELOPE_SIGNING_KEY:", "REMORA_TOOL_REGISTRY_MODULE: ot_registry"):
        assert key in compose, f"missing {key} in compose governed config"
    # The superseded wrong variable name must not reappear.
    assert "REMORA_DATABASE_URL" not in compose
