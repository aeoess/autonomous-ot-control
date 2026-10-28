"""SDD-OT-002 SEC-01: the federation sandbox (SR-01, SR-02, SR-03; acceptance tests ST-01..ST-03).

- ST-01, flag injection: an edge.json whose image is a docker flag, a tag without a digest, or a digest
  other than its pinned image, is listed as a problem by validate_edge_manifest and refused before any
  docker command runs; the docker command line puts fixed options first, the validated image after `--`,
  and the edge's command only after the image;
- ST-02, writes through links: a result.json that is a dangling symlink, a symlink to a real file, a hard
  link or not a regular file is refused and nothing is written through it; the host's own files are created
  with O_EXCL and refuse an existing name or a dangling link;
- ST-03, host execution: an edge that is not on the trust list never runs on the host. With no Docker it
  does not run at all (the marker it would write to $HOME is absent and the pass reports it unavailable);
  with Docker the only process started is `docker run`. A trusted id runs on the host.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from runner import edge_check, edge_pass, edge_sandbox

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "federation" / "edges" / "_template"
GOOD = "python:3.12-slim@sha256:" + "a" * 64


def _edge(tmp_path: Path, name: str, **entry) -> Path:
    d = tmp_path / "edges" / name
    shutil.copytree(TEMPLATE, d, ignore=shutil.ignore_patterns("__pycache__"))
    e = json.loads((d / "edge.json").read_text("utf-8"))
    e["edge_id"] = name
    e["entrypoint"].update(entry)
    (d / "edge.json").write_text(json.dumps(e), "utf-8")
    return d


# -- ST-01 ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("image, why", [
    ("--user", "pinned by digest"),
    ("-v/:/host", "pinned by digest"),
    ("python:3.12-slim", "pinned by digest"),
    ("python:3.12-slim@sha256:" + "a" * 63, "pinned by digest"),
    ("python@sha256:" + "a" * 64 + " --privileged", "pinned by digest"),
    ("--privileged@sha256:" + "a" * 64, "pinned by digest"),
])
def test_st01_an_image_that_is_not_a_pinned_name_is_a_manifest_problem(tmp_path, image, why):
    d = _edge(tmp_path, "hostile-edge", image=image, command=["0:0", "--privileged", "--network=host", "-v", "/:/host"])
    assert any(why in p for p in edge_check.validate_edge_manifest(d)), image


def test_st01_an_image_digest_pin_must_be_the_image_that_runs(tmp_path):
    d = _edge(tmp_path, "pinned-edge", image=GOOD)
    e = json.loads((d / "edge.json").read_text("utf-8"))
    e["pin"] = {"kind": "image_digest", "value": "python:3.12-slim@sha256:" + "b" * 64}
    (d / "edge.json").write_text(json.dumps(e), "utf-8")
    assert any("must equal pin.value" in p for p in edge_check.validate_edge_manifest(d))


def test_st01_a_hostile_manifest_is_refused_before_docker_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("OT_TRUST_EDGES", raising=False)
    monkeypatch.setattr(edge_sandbox.shutil, "which", lambda name: "docker")
    ran = []
    monkeypatch.setattr(edge_sandbox.subprocess, "run", lambda *a, **k: ran.append(a) or pytest.fail("docker ran"))
    d = _edge(tmp_path, "hostile-edge", image="--user",
              command=["0:0", "--privileged", "--network=host", "-v", "/:/host", "python:3.12-slim", "id"])
    rc, msg = edge_check.run_adapter(d, ROOT / "federation" / "fixtures" / "v1", tmp_path / "out")
    assert rc == 126 and "refused before docker run" in msg and ran == []


def test_st01_the_docker_command_line_keeps_edge_values_out_of_option_position(tmp_path):
    d = _edge(tmp_path, "good-edge", image=GOOD, command=["python", "/edge/adapter.py", "--privileged"])
    edge = json.loads((d / "edge.json").read_text("utf-8"))
    argv = edge_sandbox.docker_argv(d, ROOT / "federation" / "fixtures" / "v1", tmp_path, edge)
    i = argv.index("--")
    assert argv[:2] == ["docker", "run"] and argv[i + 1] == GOOD
    assert argv[2:i] == [*edge_sandbox.SANDBOX_FLAGS, *argv[2 + len(edge_sandbox.SANDBOX_FLAGS):i]]
    assert "--privileged" not in argv[:i] and argv[i + 2:] == ["python", "/edge/adapter.py", "--privileged"]
    for flag in ("--network", "none", "--read-only", "ALL", "no-new-privileges:true", "65534:65534"):
        assert flag in argv[:i]
    assert sum(1 for a in argv[:i] if a.endswith(":/out")) == 1 and not any(a.endswith(":/out:ro") for a in argv)


def test_the_committed_edges_ship_digest_pinned_images():
    for f in sorted((ROOT / "federation" / "edges").glob("*/edge.json")):
        image = json.loads(f.read_text("utf-8"))["entrypoint"].get("image")
        assert image is None or edge_sandbox.IMAGE_RE.match(image), f
    assert edge_sandbox.IMAGE_RE.match(edge_sandbox.DEFAULT_IMAGE)


# -- ST-02 ------------------------------------------------------------------------------------------------

def _symlink_or_skip(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this platform does not let the test user create symlinks")


def test_st02_a_dangling_symlink_result_is_refused_and_nothing_is_written_through_it(tmp_path):
    scratch, out, victim = tmp_path / "scratch", tmp_path / "out", tmp_path / "victim" / "authorized_keys"
    scratch.mkdir()
    victim.parent.mkdir()
    _symlink_or_skip(victim, scratch / "result.json")
    assert any("symbolic link" in p for p in edge_sandbox.collect(scratch, out))
    assert not victim.exists() and not (out / "result.json").exists()


def test_st02_a_symlink_to_a_real_file_and_a_hard_link_are_refused(tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("TWIN_ACTUATOR_TOKEN=x", "utf-8")
    s1 = tmp_path / "s1"
    s1.mkdir()
    _symlink_or_skip(secret, s1 / "result.json")
    assert any("symbolic link" in p for p in edge_sandbox.collect(s1, tmp_path / "o1"))
    s2 = tmp_path / "s2"
    s2.mkdir()
    os.link(secret, s2 / "result.json")
    assert any("hard link" in p for p in edge_sandbox.collect(s2, tmp_path / "o2"))
    assert not (tmp_path / "o1" / "result.json").exists() and not (tmp_path / "o2" / "result.json").exists()


def test_st02_only_a_plain_result_is_taken_and_never_over_an_existing_file(tmp_path):
    scratch, out = tmp_path / "scratch", tmp_path / "out"
    scratch.mkdir()
    (scratch / "result.json").write_text('{"verdicts": []}', "utf-8")
    (scratch / "notes.txt").write_text("ignored", "utf-8")
    assert edge_sandbox.collect(scratch, out) == []
    assert (out / "result.json").read_text("utf-8") == '{"verdicts": []}' and not (out / "notes.txt").exists()
    with pytest.raises(FileExistsError):
        edge_sandbox.collect(scratch, out)
    (scratch / "sub").mkdir()
    assert any("not a regular file" in p for p in edge_sandbox.collect(scratch, tmp_path / "o3"))


def test_st02_the_hosts_own_report_files_refuse_a_dangling_link(tmp_path):
    victim = tmp_path / "victim.txt"
    _symlink_or_skip(victim, tmp_path / "report.md")
    with pytest.raises(FileExistsError):
        edge_sandbox.write_new(tmp_path / "report.md", "x")
    assert not victim.exists()
    edge_sandbox.write_new(tmp_path / "fresh.md", "ok")
    with pytest.raises(FileExistsError):
        edge_sandbox.write_new(tmp_path / "fresh.md", "again")


# -- ST-03 ------------------------------------------------------------------------------------------------

MARKER_ADAPTER = '''import os, sys
from pathlib import Path
Path(os.environ.get("HOME", os.environ.get("USERPROFILE", "."))).joinpath("pwned-by-edge").write_text("x")
raise SystemExit(0)
'''


def test_st03_an_untrusted_edge_never_runs_on_the_host(tmp_path, monkeypatch):
    monkeypatch.delenv("OT_TRUST_EDGES", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    d = _edge(tmp_path, "marker-edge", image=GOOD)
    (d / "adapter.py").write_text(MARKER_ADAPTER, "utf-8")
    # No Docker: the edge does not run at all, and the pass reports it unavailable.
    monkeypatch.setattr(edge_sandbox.shutil, "which", lambda name: None)
    rc, msg = edge_check.run_adapter(d, ROOT / "federation" / "fixtures" / "v1", tmp_path / "o1")
    assert rc == 125 and "no sandbox" in msg and not (home / "pwned-by-edge").exists()
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_edge_pass as T
    s = edge_pass.run_pass(T._work(tmp_path), tmp_path / "pass", [d], "st03")
    assert s["edges"][0]["available"] is False and not (home / "pwned-by-edge").exists()
    # With Docker: the only process started is docker run, never the adapter.
    monkeypatch.setattr(edge_sandbox.shutil, "which", lambda name: "docker")
    started = []

    def fake_run(argv, **kw):
        started.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "container exited 1")
    monkeypatch.setattr(edge_sandbox.subprocess, "run", fake_run)
    rc, _ = edge_check.run_adapter(d, ROOT / "federation" / "fixtures" / "v1", tmp_path / "o2")
    assert rc == 1 and len(started) == 1 and started[0][:2] == ["docker", "run"]
    assert not (home / "pwned-by-edge").exists()


def test_st03_only_an_explicitly_trusted_id_runs_on_the_host(tmp_path, monkeypatch):
    monkeypatch.delenv("OT_TRUST_EDGES", raising=False)
    assert not edge_sandbox.trusted("agent-evidence-vectors-axes")
    assert not edge_sandbox.trusted("template-reference-verifier")
    assert edge_sandbox.trusted("authority-template-verifier")  # the testbed's own stand-in, imports the testbed
    monkeypatch.setattr(edge_sandbox, "EXTRA_TRUSTED", {"my-edge"})
    assert edge_sandbox.trusted("my-edge") and not edge_sandbox.trusted("other-edge")
    monkeypatch.setenv("OT_TRUST_EDGES", "a, b")
    assert edge_sandbox.trusted("b") and not edge_sandbox.trusted("c")


def test_the_entry_points_that_run_edges_take_trust_explicitly():
    for mod in ("edge_check", "edge_pass", "edge_mutations", "authority_harness"):
        assert '"--trust-edge"' in (ROOT / "runner" / f"{mod}.py").read_text("utf-8"), mod
    sh = (ROOT / "deploy" / "scripts" / "run-edge.sh").read_text("utf-8")
    assert "docker run" not in sh.replace("# ", "") or "runner.edge_sandbox" in sh
    assert "chmod 777" not in sh and '--trust-edge "$(' in sh
