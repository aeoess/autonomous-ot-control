"""The area reports render every strategy in the committed results and are current."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner import report_areas as ra

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "docs/results/2026-10-27"
STEMS = ("areas-2025", "abc-2025")


def _res(stem: str) -> dict:
    return json.loads((D / f"{stem}.json").read_text("utf-8"))


@pytest.mark.parametrize("stem", STEMS)
def test_every_area_and_strategy_is_rendered(stem):
    res = _res(stem)
    md, page = ra.render_md(res), ra.render_html(res, f"docs/results/2026-10-27/{stem}.json")
    for area, name in res["areas"].items():
        assert f"{area} {name}" in md and ra.E(name) in page
    for s in ra.strategies(res):
        assert f"{s} comfort dh" in md


@pytest.mark.parametrize("stem", STEMS)
def test_the_consumer_split_adds_up_to_the_kwh_saved(stem):
    for r in ra.rows(_res(stem)):
        for b in r["by"].values():
            assert abs(sum(b["consumers"].values()) - b["kwh_saved"]) < 1.0


@pytest.mark.parametrize("stem", STEMS)
def test_committed_reports_are_current(stem):
    res = _res(stem)
    assert (D / f"{stem}.md").read_text("utf-8") == ra.render_md(res) + "\n"
    assert (D / f"{stem}.html").read_text("utf-8") == ra.render_html(res, f"docs/results/2026-10-27/{stem}.json")


def test_the_abc_file_scores_the_validator_and_fdd():
    res = _res("abc-2025")
    assert res["faults"] is True and res["strategies"] == ["P0", "P3raw", "P3"]
    for area in res["areas"]:
        p3, raw = res["caretaker"][area]["P3"], res["caretaker"][area]["P3raw"]
        assert raw["optimizer_faults_caught"] == 0 and raw["fdd_alarms"] == 0
        assert p3["optimizer_faults_caught"] > 0 and p3["fdd_true_alarms"] == p3["fdd_alarms"]
