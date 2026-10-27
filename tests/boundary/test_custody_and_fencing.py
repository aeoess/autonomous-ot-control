"""OT-004: token custody and network fencing are structural, not incidental.

These assertions pin the trust boundary in the compose topology, the images and
the proposing-runner source so a future edit cannot quietly hand the actuator
credential or a plant-private route to an untrusted service (INV-01/INV-02).
They are stdlib-only and run under the locked deps (no YAML dependency).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "compose.yaml").read_text()
# The governed overlay is a separate file (so the simulator demo needs no
# governed secret); its services join the same networks and are checked alike.
GOVERNED = (ROOT / "compose.governed.yaml").read_text()
LOCK = json.loads((ROOT / "upstream" / "remora.lock.json").read_text())

ACTUATOR_TOKEN = "TWIN_ACTUATOR_TOKEN"
PLANT_NET = "plant-private"
# Services that must never hold the write credential or a plant-private route.
UNTRUSTED = ("runner", "governed", "harness", "postgres")
# Services that legitimately hold the write credential (verify vs. present).
WRITE_CUSTODIANS = ("twin", "remora")


def _strip_comment(line: str) -> str:
    # Drop inline/line comments so prose that names a token or network (e.g.
    # "# Deliberately absent: TWIN_ACTUATOR_TOKEN") can't satisfy or defeat a
    # structural check. No compose value here contains a literal '#'.
    return line.split("#", 1)[0]


def _services(compose: str) -> dict[str, str]:
    """Split the `services:` mapping into {service_name: block_text} by indent.

    Comments are stripped so checks test real YAML content, not documentation.
    """
    lines = [_strip_comment(ln) for ln in compose.splitlines()]
    out: dict[str, str] = {}
    in_services = False
    name: str | None = None
    buf: list[str] = []
    for line in lines:
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        if re.match(r"^\S", line):  # dedent to column 0 ends the services section
            break
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            if name is not None:
                out[name] = "\n".join(buf)
            name, buf = m.group(1), []
        elif name is not None:
            buf.append(line)
    if name is not None:
        out[name] = "\n".join(buf)
    return out


SERVICES = {**_services(COMPOSE), **_services(GOVERNED)}


def test_all_expected_services_present():
    assert set(SERVICES) >= {"twin", "runner", "postgres", "remora", "governed", "harness"}


def test_write_credential_custody():
    for svc in WRITE_CUSTODIANS:
        assert ACTUATOR_TOKEN in SERVICES[svc], f"{svc} should hold the actuator token"
    for svc in UNTRUSTED:
        assert ACTUATOR_TOKEN not in SERVICES[svc], f"{svc} must NOT hold the actuator token"


def test_plant_private_network_is_fenced():
    for svc in WRITE_CUSTODIANS:
        assert PLANT_NET in SERVICES[svc], f"{svc} should be on {PLANT_NET}"
    for svc in UNTRUSTED:
        assert PLANT_NET not in SERVICES[svc], f"{svc} must NOT join {PLANT_NET}"


def test_proposing_runner_is_sdk_only():
    g = SERVICES["governed"]
    assert "REMORA_OPERATOR_TOKEN" in g  # role-restricted propose credential
    assert "TWIN_CONTROL_TOKEN" not in g
    # It reaches REMORA over the authority network and the twin only to read.
    assert "authority" in g and "observe" in g


def test_runner_image_excludes_the_adapter():
    # The deployment-owned actuator callable belongs only in the REMORA image.
    runner_df = (ROOT / "deploy" / "Dockerfile.runner").read_text()
    assert "COPY adapter" not in runner_df


def test_remora_image_pins_commit_and_installs_adapter():
    df = (ROOT / "deploy" / "Dockerfile.remora-ot").read_text()
    assert "COPY adapter" in df
    assert LOCK["commit"] in df, "Dockerfile.remora-ot must pin the locked REMORA commit"
    # compose passes the same pinned commit as the default build arg.
    assert LOCK["commit"] in GOVERNED


def test_the_harness_is_credential_scoped():
    h = SERVICES["harness"]
    assert "TWIN_CONTROL_TOKEN" not in h
    assert "authority" in h and "observe" in h


def test_remora_holds_only_public_signing_values():
    r = SERVICES["remora"]
    for public in ("REMORA_TOOLSPEC_VERIFY_KEYS", "REMORA_TOOLSPEC_PINNED_DIGEST",
                   "REMORA_LIMITS_TRUSTED_KEYS"):
        assert public in r
    for seed in ("SIGNING_SEED", "REMORA_TOOLSPEC_SIGNING_KEY", "OT_TOOLSPEC_SIGNING"):
        assert seed not in r, f"remora must not receive {seed}"
    assert 'REMORA_REQUIRE_LIMITS: "1"' in r


def test_governed_source_holds_no_actuator_capability():
    src = (ROOT / "runner" / "governed.py").read_text()
    assert ACTUATOR_TOKEN not in src
    # The proposing side imports no actuator-backed callable from the adapter.
    assert "ActuatorClient" not in src and "set_zone_setpoint(" not in src
