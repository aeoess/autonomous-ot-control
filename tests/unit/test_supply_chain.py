"""SDD-OT-002 SEC-04 (SR-21, SR-22): every install, image and action is pinned; every container is hardened.

F-04: the REMORA image, the one that holds the actuator credential, installed REMORA's extras from PyPI
without pins or hashes (and upgraded pip unpinned), while every less privileged image used --require-hashes.
F-20: base images by floating tag, Actions by tag, Dependabot for one edge directory, remora without the
hardening anchor and owning its own code. Checked here as text, so a regression is a failing test in CI.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = sorted((ROOT / "deploy").glob("Dockerfile*"))
LOCK_ENTRY = r"^([A-Za-z0-9_.-]+)==\S+((?:\s*\\\n\s*--hash=sha256:[0-9a-f]{64})+)"


def _hashed(lock: Path) -> list[str]:
    text = lock.read_text("utf-8")
    names = re.findall(r"^([A-Za-z0-9_.-]+)==", text, re.M)
    assert names and len(re.findall(LOCK_ENTRY, text, re.M)) == len(names), lock.name
    return names


def test_every_base_image_is_pinned_by_digest():
    froms = [ln for f in DOCKERFILES for ln in f.read_text("utf-8").splitlines() if ln.startswith("FROM ")]
    assert froms and all("@sha256:" in ln for ln in froms), [ln for ln in froms if "@sha256:" not in ln]
    digests = {re.search(r"@sha256:[0-9a-f]{64}", ln).group(0) for ln in froms}
    assert len(digests) == 1  # one python base everywhere


def test_compose_pulls_nothing_by_tag():
    for f in ("compose.yaml", "compose.governed.yaml"):
        for image in re.findall(r"^\s+image:\s*(\S+)", (ROOT / f).read_text("utf-8"), re.M):
            assert "@sha256:" in image or image.startswith("autonomous-ot-control/"), (f, image)


def test_every_action_is_pinned_by_commit():
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for use in re.findall(r"uses:\s*(\S+)", wf.read_text("utf-8")):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", use), (wf.name, use)


def test_dependabot_watches_every_pinned_surface():
    text = (ROOT / ".github" / "dependabot.yml").read_text("utf-8")
    pairs = set(re.findall(r"package-ecosystem:\s*(\S+)\s*\n\s*directory:\s*(\S+)", text))
    assert {("pip", "/"), ("docker", "/deploy"), ("github-actions", "/")} <= pairs


def test_every_pip_install_in_an_image_is_hash_locked_or_local_without_deps():
    for f in DOCKERFILES:
        text = f.read_text("utf-8")
        assert "--upgrade pip" not in text, f.name
        for line in re.findall(r"pip (?:install|wheel)[^\n]*", text):
            ok = "--require-hashes" in line or ("--no-deps" in line and re.search(r"(/opt/remora|\.whl)", line))
            assert ok, (f.name, line)
            if "/opt/remora" in line and "wheel" not in line and ".whl" not in line:
                assert "--no-build-isolation" in line, (f.name, line)
        assert not re.search(r"pip wheel(?![^\n]*--no-build-isolation)", text), f.name


def test_the_remora_image_lock_is_complete_and_bound_to_the_pinned_commit():
    lock = ROOT / "deploy" / "ot" / "requirements-remora.lock"
    names = set(_hashed(lock))
    assert {"fastapi", "uvicorn", "psycopg", "psycopg2-binary", "cryptography", "httpx", "hatchling",
            "editables"} <= names
    commit = json.loads((ROOT / "upstream" / "remora.lock.json").read_text("utf-8"))["commit"]
    assert f"# REMORA commit: {commit}" in lock.read_text("utf-8")
    _hashed(ROOT / "deploy" / "requirements-build.lock")
    assert "requirements-remora.lock" in (ROOT / "deploy" / "Dockerfile.remora-ot").read_text("utf-8")


def _services(compose: Path) -> dict[str, str]:
    text, out, name, buf = compose.read_text("utf-8"), {}, None, []
    in_services = False
    for line in text.splitlines():
        if line.startswith("services:"):
            in_services = True
            continue
        if in_services and line and not line.startswith(" "):
            break
        m = re.match(r"^  ([a-z][\w-]*):\s*$", line)
        if in_services and m:
            if name:
                out[name] = "\n".join(buf)
            name, buf = m.group(1), []
        elif name:
            buf.append(line)
    if name:
        out[name] = "\n".join(buf)
    return out


def test_every_container_but_postgres_runs_under_the_hardening_anchor():
    """postgres is the documented exception: its entrypoint switches user (needs SETUID/SETGID)."""
    seen = {}
    for f in ("compose.yaml", "compose.governed.yaml"):
        for svc, block in _services(ROOT / f).items():
            seen[svc] = "<<: *hardening" in block
    assert seen.get("remora") is True and seen.get("twin") is True
    assert {s for s, hardened in seen.items() if not hardened} <= {"postgres"}, seen
    assert "chown -R remora:remora /var/lib/remora /app" not in (ROOT / "deploy" / "Dockerfile.remora-ot").read_text("utf-8")
