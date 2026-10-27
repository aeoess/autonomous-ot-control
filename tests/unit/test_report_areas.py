"""The five-area report renders every strategy in the committed results."""
from __future__ import annotations

import json
from pathlib import Path

from runner import report_areas as ra

ROOT = Path(__file__).resolve().parents[2]
RES = json.loads((ROOT / "docs/results/2026-10-27/areas-2025.json").read_text("utf-8"))


def test_every_area_and_strategy_is_rendered():
    md, page = ra.render_md(RES), ra.render_html(RES)
    for area, name in RES["areas"].items():
        assert f"{area} {name}" in md and E(name) in page
    for s in ra.strategies(RES):
        assert f"{s} comfort dh" in md


def test_the_consumer_split_adds_up_to_the_kwh_saved():
    for r in ra.rows(RES):
        for b in r["by"].values():
            assert abs(sum(b["consumers"].values()) - b["kwh_saved"]) < 1.0


def test_committed_reports_are_current():
    d = ROOT / "docs/results/2026-10-27"
    assert (d / "areas-2025.md").read_text("utf-8") == ra.render_md(RES) + "\n"
    assert (d / "areas-report.html").read_text("utf-8") == ra.render_html(RES)


def E(s: str) -> str:
    return ra.E(s)
