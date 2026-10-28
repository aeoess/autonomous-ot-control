"""SDD-OT-002 SR-23 (ST-21): nothing the report shows becomes markup.

The evidence report is built from runner-written artifacts. An outcome, a count or a determinism field that
carries HTML must come out escaped, and an outcome's CSS class comes from a fixed map, never from the data.
Built from the committed 2026-10-27 results with hostile values injected.
"""
from __future__ import annotations

import copy
from pathlib import Path

from runner import report_html as rh

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = 'x" onmouseover="alert(1)'
TAG = "<script>alert(1)</script>"


def test_st21_hostile_values_are_escaped_and_classes_come_from_the_map():
    d = copy.deepcopy(rh.load(ROOT / "docs" / "results" / "2026-10-27"))
    d["accept"][0]["outcome"] = PAYLOAD
    d["demo"]["counts"]["applied"] = TAG
    d["demo"]["summary"]["determinism"]["match"] = TAG
    d["demo"]["audit"]["records_checked"] = TAG
    d["demo"]["summary"]["governed_P1"]["setpoint_changes_count"] = TAG
    page = rh.render(d)
    assert PAYLOAD not in page and TAG not in page
    assert "x&quot; onmouseover=&quot;alert(1)" in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert 'class="pill unknown"' in page  # an outcome outside the map gets the neutral class
    assert {"pass", "fail"} & {c for c in rh.PILL.values()}


def test_known_outcomes_keep_their_classes():
    page = rh.render(rh.load(ROOT / "docs" / "results" / "2026-10-27"))
    assert 'class="pill pass"' in page and 'class="pill unknown"' not in page
