"""An edge's agreed fixture scope is honored by the actual CI shell."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import jsonschema
import pytest

from runner import edge_check

ROOT = Path(__file__).resolve().parents[2]


def _edge(tmp_path, selection, boundary="effect_evidence"):
    edge = tmp_path / "probe-edge"
    edge.mkdir()
    manifest = json.loads((ROOT / "federation/edges/_template/edge.json").read_text())
    manifest["edge_id"] = "probe-edge"
    manifest["boundary"] = boundary
    if boundary != "effect_evidence":
        manifest["entrypoint"] = {}
    if selection is not None:
        manifest["fixture_sets"] = selection
    (edge / "edge.json").write_text(json.dumps(manifest))
    return edge


def test_explicit_v1_scope_does_not_enroll_an_edge_in_v2(tmp_path, capsys):
    edge = _edge(tmp_path, ["v1"])
    assert edge_check.validate_edge_manifest(edge) == []
    schema = json.loads((ROOT / "contracts/edge-manifest-v1.schema.json").read_text())
    jsonschema.validate(json.loads((edge / "edge.json").read_text()), schema)
    assert edge_check.main(["--list-fixture-sets", str(edge)]) == 0
    assert capsys.readouterr().out.splitlines() == ["v1"]


def test_legacy_scope_still_runs_every_sealed_set(tmp_path, capsys):
    edge = _edge(tmp_path, None)
    assert edge_check.main(["--list-fixture-sets", str(edge)]) == 0
    assert capsys.readouterr().out.splitlines() == ["v1", "v2"]


@pytest.mark.parametrize("selection", [[], ["v1", "v1"], ["v99"], ["../v1"],
                                        ["v1;echo injected"], "v1", [1], ["v1", ""]])
def test_invalid_scopes_fail_closed(tmp_path, selection, capsys):
    edge = _edge(tmp_path, selection)
    assert edge_check.validate_edge_manifest(edge)
    assert edge_check.main(["--list-fixture-sets", str(edge)]) != 0
    assert not capsys.readouterr().out.strip()


@pytest.mark.parametrize("selection, accepted, boundary", [
    (["v1"], True, "effect_evidence"),
    (["v99"], False, "effect_evidence"),
    (["v1"], True, "runner_validity"),
])
def test_ci_executes_only_declared_sets_and_propagates_selection_failure(tmp_path, selection, accepted, boundary):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "runner", repo / "runner", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "federation/fixtures", repo / "federation/fixtures")
    edge = _edge(tmp_path, selection, boundary)
    (repo / "federation/edges").mkdir()
    shutil.copytree(edge, repo / "federation/edges/probe-edge")
    script = repo / "deploy/scripts/run-edge.sh"
    script.parent.mkdir(parents=True)
    script.write_text('#!/usr/bin/env bash\nprintf "%s %s\\n" "$1" "$2" >> executed.txt\n')
    script.chmod(0o755)
    workflow = (ROOT / ".github/workflows/federation-edge.yml").read_text()
    step = workflow.split("      - name: Run changed edges on their agreed fixture sets\n", 1)[1]
    step = step.split("      - ", 1)[0]
    shell = textwrap.dedent(step.split("        run: |\n", 1)[1])
    assert "        shell: bash\n" in step
    env = {**os.environ, "CHANGED_EDGES": "probe-edge",
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", shell],
                            cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    if accepted and boundary == "effect_evidence":
        assert (repo / "executed.txt").read_text().splitlines() == ["probe-edge v1"]
    else:
        assert not (repo / "executed.txt").exists()
