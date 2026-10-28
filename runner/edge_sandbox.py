"""The one sandbox every federation adapter runs in (SDD-OT-002 SEC-01: SR-01, SR-02, SR-03).

A contributed edge is third-party code. It never runs on a host process that holds a credential:

- **SR-01, no injection.** Every value from an edge.json that reaches the docker command line, a path or a
  file name is validated before use. The image must be pinned by digest, ``name[:tag]@sha256:<64 hex>``,
  must not start with ``-``, and must equal ``pin.value`` when the pin is an image digest. The docker
  arguments are a list (no shell), the image is the one positional argument after the options, and the
  edge's own command only follows it, where docker no longer reads options.
- **SR-02, no writes through links.** The container writes to a fresh private directory, never to the
  run's output directory. Afterwards the host takes only ``result.json``, refuses a symlink, a hard link or
  anything that is not a regular file, caps its size, and writes it into the output directory with
  ``O_CREAT | O_EXCL`` (and ``O_NOFOLLOW`` where the platform has it). Every file the host writes for a run
  goes through ``write_new``.
- **SR-03, no host execution.** An adapter runs on the host only when its edge id is on an explicit trust
  list: ``--trust-edge <id>`` on the command line, or ``OT_TRUST_EDGES`` (comma-separated ids; the test
  suite sets ``*``). Otherwise it runs in this sandbox, and when Docker is not available it does not run
  at all. The testbed's own authority stand-in verifier imports the testbed and is the only default entry.

Sandbox: no network, read-only root, no capabilities, no new privileges, non-root, pids, memory and CPU
bounded, a small tmpfs, the edge and the fixtures read-only.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9./_-]*(:[A-Za-z0-9_.-]+)?@sha256:[0-9a-f]{64}$")
#: python:3.12-slim as pulled and used by every sandboxed run so far; a tag alone is never used.
DEFAULT_IMAGE = "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
SAFE_ARG_RE = re.compile(r"^[^\x00\r\n]{1,4096}$")
#: Edge ids that may run on the host by default: testbed code reviewed in this repository that cannot run in
#: the stdlib-only image (it imports the testbed). Contributed edges are never on this list.
DEFAULT_TRUSTED = frozenset({"authority-template-verifier"})
RESULT_NAME = "result.json"
MAX_RESULT_BYTES = 16 * 1024 * 1024
SANDBOX_FLAGS = ("--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges:true", "--user", "65534:65534",
                 "--pids-limit", "128", "--memory", "512m", "--cpus", "1", "--tmpfs", "/tmp:size=16m")
EXTRA_TRUSTED: set[str] = set()  # --trust-edge, set by the command-line entry points
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_BINARY = getattr(os, "O_BINARY", 0)


class SandboxRefused(RuntimeError):
    """The edge cannot be run safely: a manifest value that would reach docker or a path, or no sandbox."""


def manifest_problems(edge: dict[str, Any]) -> list[str]:
    """SR-01: problems with the values of an edge.json that reach docker, a path or a file name."""
    problems: list[str] = []
    ep = edge.get("entrypoint") if isinstance(edge.get("entrypoint"), dict) else {}
    image = ep.get("image")
    if image is not None:
        if not isinstance(image, str) or image.startswith("-") or not IMAGE_RE.match(image):
            problems.append("entrypoint.image must be pinned by digest, name[:tag]@sha256:<64 hex>, "
                            "and must not start with '-'")
        pin = edge.get("pin") if isinstance(edge.get("pin"), dict) else {}
        if pin.get("kind") == "image_digest" and image != pin.get("value"):
            problems.append("entrypoint.image must equal pin.value when pin.kind is image_digest")
    cmd = ep.get("command")
    if cmd is not None:
        if not isinstance(cmd, list) or not all(isinstance(a, str) and SAFE_ARG_RE.match(a) for a in cmd):
            problems.append("entrypoint.command must be a list of non-empty strings without NUL or line breaks")
    return problems


def trusted(edge_id: str) -> bool:
    env = {x.strip() for x in os.environ.get("OT_TRUST_EDGES", "").split(",") if x.strip()}
    return "*" in env or edge_id in env or edge_id in EXTRA_TRUSTED or edge_id in DEFAULT_TRUSTED


def docker_argv(edge_dir: Path, fixtures: Path, scratch: Path, edge: dict[str, Any]) -> list[str]:
    """The exact docker command line: fixed options, three mounts, the validated image, then the command."""
    problems = manifest_problems(edge)
    if problems:
        raise SandboxRefused("; ".join(problems))
    ep = edge.get("entrypoint") or {}
    image = ep.get("image") or DEFAULT_IMAGE
    cmd = list(ep.get("command") or [])
    if not cmd:
        raise SandboxRefused("entrypoint.command is empty")
    mounts = ["-v", f"{edge_dir.resolve()}:/edge:ro", "-v", f"{fixtures.resolve()}:/fixtures:ro",
              "-v", f"{scratch.resolve()}:/out"]
    return ["docker", "run", *SANDBOX_FLAGS, *mounts, "--", image, *cmd]


def write_new(path: Path, data: str | bytes) -> None:
    """Create a file that must not exist yet, never through a link (SR-02)."""
    if os.path.lexists(path):
        raise FileExistsError(f"{path} exists (or is a link): a run never overwrites or follows anything")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)


def collect(scratch: Path, out: Path) -> list[str]:
    """Take result.json out of the sandbox's directory; refuse anything that is not a plain, single file."""
    problems: list[str] = []
    for entry in sorted(os.scandir(scratch), key=lambda e: e.name):
        st = os.lstat(entry.path)
        if stat.S_ISLNK(st.st_mode):
            problems.append(f"{entry.name} is a symbolic link")
        elif not stat.S_ISREG(st.st_mode):
            problems.append(f"{entry.name} is not a regular file")
        elif st.st_nlink > 1:
            problems.append(f"{entry.name} is a hard link")
    if problems:
        return problems
    src = scratch / RESULT_NAME
    if not src.is_file():
        return [f"the edge wrote no {RESULT_NAME}"]
    if src.stat().st_size > MAX_RESULT_BYTES:
        return [f"{RESULT_NAME} is larger than {MAX_RESULT_BYTES} bytes"]
    fd = os.open(src, os.O_RDONLY | O_NOFOLLOW | O_BINARY)
    with os.fdopen(fd, "rb") as f:
        data = f.read(MAX_RESULT_BYTES + 1)
    out.mkdir(parents=True, exist_ok=True)
    write_new(out / RESULT_NAME, data)
    return []


def run(edge_dir: Path, fixtures: Path, out: Path, edge: dict[str, Any], timeout_s: int) -> tuple[int, str]:
    """Run the edge in the sandbox. (exit code, message): 125 no sandbox, 126 refused, 124 timeout."""
    try:
        if shutil.which("docker") is None:
            raise SandboxRefused("no sandbox: Docker is not available, and the edge is not trusted to run on the host")
        scratch = Path(tempfile.mkdtemp(prefix="edge-sandbox-"))
    except SandboxRefused as e:
        return 125, str(e)
    try:
        try:
            argv = docker_argv(edge_dir, fixtures, scratch, edge)
        except SandboxRefused as e:
            return 126, f"refused before docker run: {e}"
        os.chmod(scratch, 0o733)  # the container runs as nobody; others may write, never list
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s,
                                  env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        except subprocess.TimeoutExpired:
            return 124, f"sandboxed adapter killed after {timeout_s}s"
        tail = (proc.stderr or "")[-4000:]
        if proc.returncode != 0:
            return proc.returncode, tail
        problems = collect(scratch, out)
        if problems:
            return 126, "unsafe sandbox output: " + "; ".join(problems)
        return 0, tail
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
