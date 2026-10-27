"""Real exogenous data: fetch, verify and the twin's refusal of a bad snapshot.

All offline: a fake ``get`` stands in for the network, so the parsing, the
local-day boundary, the verify outcomes and the twin's digest check are pinned
without depending on a live API.
"""
from __future__ import annotations

import copy
import hashlib
import json
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from runner import exogenous as ex
from twin.scenarios import ScenarioError, load_scenario

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "twin" / "fixtures" / "site-demo-building-001.json"
WEEK = ROOT / "scenarios" / "trondheim-week-2026w03-v1.json"


def _price_day(d: date, base: float) -> bytes:
    # Local day d (Europe/Oslo, UTC+1 in January) runs 23:00Z the day before to
    # 23:00Z; the source publishes it with +01:00 offsets.
    oslo = timezone(timedelta(hours=1))
    start = datetime(d.year, d.month, d.day, tzinfo=oslo)
    rows = [{"NOK_per_kWh": round(base + h / 100, 5), "EUR_per_kWh": 0.1, "EXR": 11.0,
             "time_start": (start + timedelta(hours=h)).isoformat(),
             "time_end": (start + timedelta(hours=h + 1)).isoformat()} for h in range(24)]
    return json.dumps(rows).encode()


def _weather(first: date, last: date, offset: float = 0.0, stamp: float = 0.29) -> bytes:
    n = ((last - first).days + 1) * 24
    t0 = datetime(first.year, first.month, first.day)
    return json.dumps({"generationtime_ms": stamp, "hourly_units": {"temperature_2m": "°C"},
                       "hourly": {"time": [(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
                                           for i in range(n)],
                                  "temperature_2m": [round(-3 + i / 10 + offset, 1)
                                                     for i in range(n)]}}).encode()


class FakeNet:
    def __init__(self, first: date, last: date) -> None:
        self.pages: dict[str, bytes] = {}
        for i in range((last - first).days + 2):
            d = first + timedelta(days=i)
            self.pages[ex.PRICE_URL.format(y=d.year, md=d.strftime("%m-%d"), area="NO3")] = \
                _price_day(d, 1.0 + i)
        site = ex.load_site(SITE)
        for url in ex.weather_urls(site["location"]["lat"], site["location"]["lon"], first, last):
            self.pages[url] = _weather(first, last)
        self.down = False

    def __call__(self, url: str) -> bytes:
        if self.down:
            raise urllib.error.URLError("offline")
        return self.pages[url]


D1, D2 = date(2026, 1, 15), date(2026, 1, 16)


def test_site_declares_a_consistent_price_area():
    site = ex.load_site(SITE)
    assert site["price_area"]["code"] == "NO3" and site["fictitious"] is True


def test_a_coordinate_outside_the_declared_area_is_refused(tmp_path):
    site = json.loads(SITE.read_text("utf-8"))
    site["location"]["lat"] = 59.91  # Oslo is NO1, not NO3
    p = tmp_path / "site.json"
    p.write_text(json.dumps(site), "utf-8")
    with pytest.raises(ex.ExogenousError, match="outside the declared NO3 box"):
        ex.load_site(p)


def test_price_covers_the_utc_window_from_local_days():
    net = FakeNet(D1, D2)
    snap = ex.fetch_kind("price", ex.load_site(SITE), D1, D2, get=net)
    assert snap["start_utc"] == "2026-01-15T00:00:00Z" and len(snap["values"]) == 48
    # 23:00Z on the 16th is the first hour of local day the 17th.
    assert len(snap["provenance"]["requests"]) == 3
    assert all(len(r["sha256"]) == 64 for r in snap["provenance"]["requests"])


def test_a_gap_in_the_source_is_refused():
    net = FakeNet(D1, D1)
    rows = json.loads(net.pages[ex.price_urls("NO3", D1, D1)[0]])
    del rows[5]
    net.pages[ex.price_urls("NO3", D1, D1)[0]] = json.dumps(rows).encode()
    with pytest.raises(ex.ExogenousError, match="no value for"):
        ex.fetch_kind("price", ex.load_site(SITE), D1, D1, get=net)


def test_verify_outcomes():
    net = FakeNet(D1, D1)
    site = ex.load_site(SITE)
    price = ex.fetch_kind("price", site, D1, D1, get=net)
    weather = ex.fetch_kind("outdoor", site, D1, D1, get=net)
    assert ex.verify_snapshot(price, get=net)["outcome"] == "VERIFIED_IDENTICAL"
    # Same values, different bytes (a timing field): equivalent, not identical.
    url = weather["provenance"]["requests"][0]["url"]
    net.pages[url] = _weather(D1, D1, stamp=9.9)
    assert ex.verify_snapshot(weather, get=net)["outcome"] == "VERIFIED_EQUIVALENT"
    # A changed value is a divergence.
    net.pages[url] = _weather(D1, D1, offset=0.1)
    assert ex.verify_snapshot(weather, get=net)["outcome"] == "DIVERGED"
    # Tampering with the snapshot is caught too.
    forged = copy.deepcopy(price)
    forged["values"][3] = 0.01
    assert ex.verify_snapshot(forged, get=net)["outcome"] == "DIVERGED"
    # No answer is never a pass.
    net.down = True
    assert ex.verify_snapshot(price, get=net)["outcome"] == "UNREACHABLE"


def test_the_committed_week_loads_and_is_real_data():
    sc = load_scenario(WEEK)
    assert sc.price_series is not None and sc.outdoor_series is not None
    assert sc.price_series.provenance["source"]["area"] == "NO3"
    assert len(sc.price_series.values) == len(sc.outdoor_series.values) == 7 * 24
    assert sc.price_per_kwh(0) == sc.price_series.values[0]
    assert sc.outdoor_c(3600 * 5) == sc.outdoor_series.values[5]


def test_a_snapshot_whose_bytes_changed_is_refused(tmp_path):
    raw = json.loads(WEEK.read_text("utf-8"))
    data_dir = WEEK.parent
    price_path = data_dir / raw["price"]["series_ref"]
    snap = json.loads(price_path.read_text("utf-8"))
    snap["values"][0] = 0.0
    (tmp_path / "p.json").write_text(json.dumps(snap), "utf-8")
    raw["price"] = {"series_ref": "p.json", "sha256": raw["price"]["sha256"]}
    raw["outdoor"]["series_ref"] = str((data_dir / raw["outdoor"]["series_ref"]).resolve())
    with pytest.raises(ScenarioError, match="does not match"):
        load_scenario(raw, base_dir=tmp_path)


def test_a_snapshot_shorter_than_the_scenario_is_refused():
    raw = json.loads(WEEK.read_text("utf-8"))
    raw["duration_seconds"] = 8 * 86400
    with pytest.raises(ScenarioError, match="does not cover"):
        load_scenario(raw, base_dir=WEEK.parent)


def test_the_scenario_digest_binds_the_data():
    """Evidence pins the scenario digest; it must move when the data does."""
    raw = json.loads(WEEK.read_text("utf-8"))
    a = load_scenario(raw, base_dir=WEEK.parent).digest
    other = copy.deepcopy(raw)
    other["price"]["sha256"] = hashlib.sha256(b"other").hexdigest()
    other["price"]["series_ref"] = raw["outdoor"]["series_ref"]  # never loaded: digest only
    from twin.state import canonical_digest

    assert a == canonical_digest(raw) != canonical_digest(other)
