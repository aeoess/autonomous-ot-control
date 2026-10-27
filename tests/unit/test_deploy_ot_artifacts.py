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
    compose = (ROOT / "compose.governed.yaml").read_text()
    for key in ("REMORA_PG_DSN:", "REMORA_CONTROL_PLANE_DSN:", "REMORA_API_TOKENS:",
                "REMORA_ENV: production", "REMORA_ENABLED_SURFACES: execution",
                "REMORA_ENVELOPE_SIGNING_KEY:", "REMORA_TOOL_REGISTRY_MODULE: ot_registry"):
        assert key in compose, f"missing {key} in compose governed config"
    # The superseded wrong variable name must not reappear.
    assert "REMORA_DATABASE_URL" not in compose


def test_adapter_lock_lines_equal_the_project_lock():
    """deploy/ot/requirements-adapter.lock copies requirements.lock verbatim."""
    def blocks(text: str) -> dict[str, str]:
        out, name = {}, None
        for line in text.splitlines():
            if line and not line[0].isspace() and not line.startswith("#"):
                name = line.split("==")[0]
                out[name] = line
            elif name and line.strip().startswith("--hash"):
                out[name] += line.strip()
        return out
    adapter = blocks((OT / "requirements-adapter.lock").read_text())
    project = blocks((ROOT / "requirements.lock").read_text())
    assert set(adapter) == {"certifi", "httpcore", "httpx"}
    for name, pinned in adapter.items():
        assert project[name] == pinned, name


def test_signed_specs_mirror_the_deployment_contract():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sign_deployment", OT / "sign_deployment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    toolspec = json.loads((ROOT / "contracts" / "ot-toolspec-v1.json").read_text())
    specs = {s["tool_id"]: s for s in mod.build_specs(toolspec, {
        "read_zone_state": "closure-sha256:" + "a" * 64,
        "read_building_energy": "closure-sha256:" + "a" * 64,
        "set_zone_setpoint": "closure-sha256:" + "a" * 64})}
    assert set(specs) == {"read_zone_state", "read_building_energy", "set_zone_setpoint"}
    write = specs["set_zone_setpoint"]
    props = write["argument_schema"]["properties"]
    assert write["argument_schema"]["additionalProperties"] is False
    assert props["zone_id"]["enum"] == ["ZONE-01", "ZONE-02", "ZONE-03"]
    assert (props["setpoint_c"]["minimum"], props["setpoint_c"]["maximum"]) == (19.0, 23.0)
    # REMORA's validator has no multipleOf; signing one would be refused.
    assert "multipleOf" not in json.dumps(write)
    assert write["allowed_targets"] == ["simulated_pilot"]
    assert write["action_type"] == "write" and write["risk_tier"] == "high"
    assert all(s["action_type"] == "read" for k, s in specs.items() if k != "set_zone_setpoint")


def test_zone_03_budget_is_tighter_than_the_interlock():
    """LIM-01 depends on it: REMORA must refuse what the interlock would allow."""
    pol = json.loads((OT / "ot_limits.policy.json").read_text())
    cfg = json.loads((ROOT / "twin" / "fixtures" / "three-zone-v1.json").read_text())
    z3 = next(lim for lim in pol["limits"] if lim["resource"] == "ZONE-03")
    assert z3["max"] / 10 < cfg["guard"]["max_delta_c"]
