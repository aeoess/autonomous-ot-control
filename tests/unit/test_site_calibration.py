"""runner.site_calibration on a synthetic export in the anonymised hourly-meter v2 shape.

The synthetic site is built from known parameters, so the targets can be checked against them:
- the quality gate drops flagged rows, the DQ03 window, spikes and the partial last hour, and keeps
  absent hours absent;
- the heating signature recovers the slope, the balance temperature and the weather-independent base;
- the load shape recovers the night base and the weekday/weekend pattern;
- the targets name their assumptions and what they do not establish; no address field exists anywhere.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runner import site_calibration as sc

UTC = timezone.utc
COLS = ["time_start_local_with_utc_offset", "energy_kwh", "mean_power_kw_as_reported",
        "max_power_kw_as_reported", "raw_sample_count", "quality_flags"]
T0 = datetime(2026, 2, 16, 0, tzinfo=UTC)
DAYS = 60
SLOPE_KWH_DAY_K, BALANCE_C, BASE_KWH_DAY = 30.0, 12.0, 100.0


def outdoor(t: datetime) -> float:
    d = (t - T0).days
    return -8.0 + 0.4 * d + 3.0 * ((t.hour - 12) / 12.0)  # warming over the period, daily swing


def _local_str(t: datetime) -> str:
    return (t + timedelta(hours=1)).replace(tzinfo=timezone(timedelta(hours=1))).isoformat()


def make_site(root: Path) -> tuple[Path, Path]:
    (root / "meters").mkdir(parents=True)
    hours = [T0 + timedelta(hours=h) for h in range(DAYS * 24)]
    rows: dict[str, list] = {"E01": [], "E02": [], "H01": [], "H02": []}
    for t in hours:
        lt = t + timedelta(hours=1)
        weekday = lt.weekday() < 5
        occ = weekday and 8 <= lt.hour < 16
        rows["E01"].append([_local_str(t), 10.0 + (20.0 if occ else 0.0), None, None, 60, []])
        rows["E02"].append([_local_str(t), 2.0, None, None, 60, []])
        # heat: daily total = base + slope*max(0, Tb - Tday), spread evenly; split over two meters
    for d in range(DAYS):
        day = [T0 + timedelta(hours=d * 24 + h) for h in range(24)]
        tmean = sum(outdoor(t) for t in day) / 24
        total = BASE_KWH_DAY + SLOPE_KWH_DAY_K * max(0.0, BALANCE_C - tmean)
        for t in day:
            rows["H01"].append([_local_str(t), total / 24 * 0.8, None, None, 60, []])
            rows["H02"].append([_local_str(t), total / 24 * 0.2, None, None, 60, []])
    rows["E01"][100][5] = ["SYSTEMIC_ENERGY_POWER_FACTOR_ANOMALY"]   # flagged
    rows["E01"][200][1] = 5000.0                                      # spike
    del rows["E02"][300]                                              # absent hour stays absent
    meta = []
    for mid, rs in rows.items():
        cat = "electricity" if mid.startswith("E") else "heating"
        (root / "meters" / f"{mid}.json").write_text(json.dumps(
            {"schema_version": "2.0.0", "meter_id": mid, "energy_category": cat, "hourly_columns": COLS, "readings": rs}), "utf-8")
        meta.append({"meter_id": mid, "energy_category": cat, "meter_role_sanitized": "synthetic"})
    last = hours[-1]
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": "2.0.0", "site_id": "SITE_TEST", "source_sha256": "0" * 64,
        "period": {"first_hour_utc": hours[0].isoformat(), "last_hour_utc": last.isoformat()}, "meters": meta}), "utf-8")
    weather = root / "outdoor.json"
    weather.write_text(json.dumps({"schema": "ot-exogenous-snapshot/v1", "start_utc": T0.isoformat().replace("+00:00", "Z"),
                                   "step_seconds": 3600, "values": [outdoor(t) for t in hours]}), "utf-8")
    return root, weather


def test_gate_excludes_what_the_audit_names_and_keeps_absence(tmp_path):
    data, _ = make_site(tmp_path / "site")
    g = sc.gate(sc.load_meters(data), last_hour_utc=T0 + timedelta(hours=DAYS * 24 - 1))
    ex = g["excluded"]["E01"]
    assert ex["flagged"] == 1 and ex["spike"] == 1 and ex["partial_last_hour"] == 1
    # DQ03, 2026-03-09 12:00 to 2026-03-13 10:00 UTC, lies inside the synthetic period: 94 hours.
    assert ex["dq03_window"] == 94
    s = g["series"]
    assert len(s["E01"]) == DAYS * 24 - 1 - 1 - 1 - 94
    missing = T0 + timedelta(hours=300)
    assert missing not in s["E02"], "an absent hour is never filled"


def test_signature_and_shape_recover_the_known_parameters(tmp_path):
    data, weather = make_site(tmp_path / "site")
    t = sc.targets(data, weather)
    hs = t["heating_signature"]
    assert abs(hs["kwh_per_day_per_k"] - SLOPE_KWH_DAY_K) / SLOPE_KWH_DAY_K < 0.05
    assert abs(hs["balance_temp_c"] - BALANCE_C) <= 0.5
    assert abs(hs["base_kwh_per_day"] - BASE_KWH_DAY) / BASE_KWH_DAY < 0.1
    assert hs["r2"] > 0.95 and hs["balance_identified"] is True
    ls = t["electricity_load_shape"]
    assert abs(ls["night_base_kw"] - 12.0) < 0.01 and abs(ls["weekday_day_kw"] - 32.0) < 0.01
    assert ls["median_kw_by_hour"]["weekend"][10] == 12.0
    assert t["site_id"] == "SITE_TEST" and t["not_established"] and t["assumptions"]
    text = json.dumps(t).lower()
    for word in ("address", "adresse", "gnr", "bnr", "målepunkt"):
        assert word not in text


def test_balance_is_not_identified_without_days_above_it():
    pts = [(f"d{i}", 100.0 + 30.0 * (12.0 - t), t) for i, t in enumerate([-5.0 + 0.2 * i for i in range(40)])]
    sig = sc.fit_signature(pts)
    assert sig is not None and sig["balance_identified"] is False


def test_fit_rejects_too_few_days():
    assert sc.fit_signature([("d", 1.0, 0.0)] * 5) is None


def test_twin_targets_run_on_a_calibrated_fixture():
    """The twin side of the comparison: a short run gives a load shape; a heating signature needs a
    district-heated fixture and at least ten days, otherwise it is reported as not comparable or None."""
    root = Path(__file__).resolve().parents[2]
    t = sc.twin_targets(root / "twin" / "fixtures" / "calibrated" / "kontor-1920-fjernvarme-v1.json", "NO1", days=3)
    assert t["heating_comparable"] is True and t["heating_signature"] is None
    assert t["electricity_load_shape"]["hours_used"] == 72
    t2 = sc.twin_targets(root / "twin" / "fixtures" / "calibrated" / "kontor-small-v1.json", "NO1", days=2)
    assert t2["heating_comparable"] is False and t2["heating_signature"] is None
