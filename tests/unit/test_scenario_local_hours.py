"""Occupancy in local Oslo time with summer time (scenarios with weekday_hours_local)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SC = load_scenario(ROOT / "scenarios" / "no1-2026-metered-period-hvac-v1.json")
START = datetime(2026, 2, 16, tzinfo=timezone.utc)


def at(iso: str) -> int:
    return int((datetime.fromisoformat(iso) - START).total_seconds())


def test_local_hours_follow_summer_time():
    assert SC.occupied(at("2026-02-17T07:00:00+00:00"))        # 08 local, winter
    assert not SC.occupied(at("2026-02-17T06:00:00+00:00"))    # 07 local
    assert not SC.occupied(at("2026-02-17T15:00:00+00:00"))    # 16 local
    assert SC.occupied(at("2026-06-15T06:00:00+00:00"))        # 08 local, summer
    assert not SC.occupied(at("2026-06-15T05:00:00+00:00"))    # 07 local
    assert not SC.occupied(at("2026-06-15T14:00:00+00:00"))    # 16 local
    assert SC.scheduled_open(at("2026-06-15T06:00:00+00:00"))


def test_holidays_and_weekends_are_unoccupied():
    assert not SC.occupied(at("2026-04-03T09:00:00+00:00"))    # Good Friday
    assert not SC.occupied(at("2026-02-21T09:00:00+00:00"))    # Saturday


def test_all_2025_scenarios_use_the_measured_local_hours():
    import json
    for a in ("no1", "no2", "no3", "no4", "no5"):
        occ = json.loads((ROOT / "scenarios" / f"{a}-2025-hvac-v1.json").read_text("utf-8"))["occupancy"]
        assert occ["weekday_hours_local"] == [8, 16] and "weekday_hours_utc" not in occ
