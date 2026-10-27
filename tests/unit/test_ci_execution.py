"""Exercise the CI shell and collection boundaries with real failing tests."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _workflow_step(name: str) -> tuple[str, bool]:
    workflow = (ROOT / ".github/workflows/lint-test.yml").read_text("utf-8")
    step = workflow.split(f"      - name: {name}\n", 1)[1]
    step = step.split("      - ", 1)[0]
    lines = step.split("        run: |\n", 1)[1].splitlines()
    body = []
    for line in lines:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)), "        shell: bash\n" in step


@pytest.mark.parametrize("source, succeeds", [
    ("def test_probe(): assert False\n", False),
    ("import ci_probe_dependency_that_does_not_exist\n", False),
    ("import pytest\ndef test_probe(): pytest.skip('probe')\n", False),
    ("", False),  # pytest exits 5 when no tests were collected
    ("def test_probe(): assert True\n", True),
])
def test_ci_step_preserves_pytest_failure_and_rejects_skips(tmp_path, source, succeeds):
    (tmp_path / "test_probe.py").write_text(source, "utf-8")
    script, explicit_bash = _workflow_step("Tests (skips are failures)")
    flags = ["--noprofile", "--norc", "-eo", "pipefail"] if explicit_bash else ["-e"]
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    for name in ("PYTHONPATH", "PYTEST_ADDOPTS"):
        env.pop(name, None)
    run = subprocess.run(["bash", *flags, "-c", script], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=30)
    assert (run.returncode == 0) is succeeds, run.stdout + run.stderr


@pytest.mark.parametrize("required", [False, True])
def test_missing_sdk_is_optional_only_outside_the_sdk_job(required):
    """An unavailable SDK must be handled before its test module is imported."""
    code = """
import importlib.abc
import sys
class NoRemora(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'remora' or fullname.startswith('remora.'):
            raise ModuleNotFoundError('SDK deliberately unavailable')
sys.meta_path.insert(0, NoRemora())
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
"""
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    args = ['--collect-only', '-q', '-p', 'no:cacheprovider',
            'tests/sdk', 'tests/unit/test_model.py']
    if required:
        args.append('--require-remora-sdk')
    run = subprocess.run([sys.executable, "-c", code, *args], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30)
    if required:
        assert run.returncode != 0
        assert "requires the pinned remora.sdk" in run.stderr
    else:
        assert run.returncode == 0, run.stdout + run.stderr
        assert "test_attestor_binding_sdk.py::" not in run.stdout


@pytest.mark.parametrize("author, email, accepted", [
    ("darklordVirtual", "support@luftfiber.no", True),
    ("Stian Skogbrott", "36493828+darklordVirtual@users.noreply.github.com", True),
    ("Stian Skogbrott", "someone-else@example.invalid", False),
    ("Someone Else", "36493828+darklordVirtual@users.noreply.github.com", False),
])
def test_owner_identity_gate_accepts_only_the_owner_identities(tmp_path, author, email, accepted):
    def git(*args):
        run = subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)
        return run.stdout.strip()

    git("init", "-q")
    git("-c", "user.name=darklordVirtual", "-c", "user.email=support@luftfiber.no",
        "commit", "--allow-empty", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("-c", f"user.name={author}", "-c", f"user.email={email}",
        "commit", "--allow-empty", "-qm", "probe")
    head = git("rev-parse", "HEAD")
    script, _ = _workflow_step("Authors and trailers")
    script = script.replace("${{ github.event.pull_request.base.sha }}", base)
    script = script.replace("${{ github.event.pull_request.head.sha }}", head)
    run = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path,
                         capture_output=True, text=True, timeout=30)
    assert (run.returncode == 0) is accepted, run.stdout + run.stderr
