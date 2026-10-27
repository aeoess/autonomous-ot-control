from __future__ import annotations

import json

import pytest

from runner import baseline
from runner.evidence import write_bundle
from runner.scoring import compare, score
from tests.conftest import BASELINE


def test_fresh_directory_required(tmp_path):
    kw = dict(suite_id="s", run_id="r1", manifest_extra={}, metrics={}, trace=[],
              claims={"x": "IMPLEMENTED"})
    write_bundle(tmp_path, **kw)
    with pytest.raises(FileExistsError):
        write_bundle(tmp_path, **kw)


def test_invalid_claim_status(tmp_path):
    with pytest.raises(ValueError):
        write_bundle(tmp_path, suite_id="s", run_id="r", manifest_extra={}, metrics={}, trace=[],
                     claims={"x": "PROBABLY_FINE"})


def test_failed_results_counted(tmp_path):
    root = write_bundle(tmp_path, suite_id="s", run_id="r", manifest_extra={}, metrics={}, trace=[],
                        claims={}, results=[{"case_id": "a", "outcome": "PASS"},
                                            {"case_id": "b", "outcome": "FAIL"}])
    m = json.loads((root / "manifest.json").read_text())
    assert m["counts"] == {"cases": 2, "passed": 1, "failed": 1, "skipped": 0, "not_run": 0}


def test_baseline_cli_reproducible(tmp_path):
    for rid in ("a", "b"):
        assert baseline.main(["--scenario", str(BASELINE), "--out", str(tmp_path), "--run-id", rid]) == 0
    ma, mb = (json.loads((tmp_path / r / "manifest.json").read_text()) for r in "ab")
    assert ma["trace_digest"] == mb["trace_digest"]
    assert ma["metrics_digest"] == mb["metrics_digest"]
    assert ma["claims"]["real_building_savings"] == "NOT_ESTABLISHED"
    assert ma["claims"]["remora_governed_dispatch"] == "NOT_ESTABLISHED"


def test_compare_requires_same_span(config):
    m = score(baseline.run_baseline(config, BASELINE))
    with pytest.raises(ValueError):
        compare(m, m | {"simulated_seconds": 1})
    assert compare(m, m)["energy_kwh"]["delta"] == 0
