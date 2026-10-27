"""Fetch and verify the twin's real exogenous data: day-ahead prices and weather.

The site file (``twin/fixtures/site-*.json``) gives a fictitious building a real
coordinate and its declared Norwegian price area. This tool fetches, for that
site and a UTC date range:

* **price**: day-ahead NOK/kWh for the price area from hvakosterstrommen.no,
  which republishes the ENTSO-E / Nord Pool day-ahead result per area;
* **outdoor**: hourly 2 m air temperature at the coordinate from the
  Open-Meteo historical archive (ERA5-based reanalysis).

It writes one ``ot-exogenous-snapshot/v1`` file per kind. Each file keeps every
source URL, the SHA-256 of each raw response, the retrieval time and the
attribution. A snapshot is data for the twin, never authority for REMORA.

``verify`` re-fetches every URL a snapshot names and compares the response with
what was recorded:

* ``VERIFIED_IDENTICAL``: same bytes;
* ``VERIFIED_EQUIVALENT``: different bytes, same normalized values;
* ``DIVERGED``: the values differ;
* ``UNREACHABLE``: no answer, which is not a pass.

Only the two VERIFIED outcomes exit 0. This is a re-fetch from one source. It
is not corroboration by a second source: a second, independent price source
(ENTSO-E Transparency, which needs a token) is ``NOT_ESTABLISHED`` here.

    python -m runner.exogenous fetch --site twin/fixtures/site-demo-building-001.json \\
        --from 2026-01-15 --to 2026-01-15 --out scenarios/data/demo-building-001
    python -m runner.exogenous verify scenarios/data/demo-building-001/*.json

Stdlib only. The User-Agent names this project (MET/Open-Meteo etiquette) and
carries no personal contact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA = "ot-exogenous-snapshot/v1"
USER_AGENT = "autonomous-ot-control/0.1 (+https://github.com/darklordVirtual/autonomous-ot-control)"
PRICE_URL = "https://www.hvakosterstrommen.no/api/v1/prices/{y}/{md}_{area}.json"
WEATHER_URL = ("https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
               "&start_date={start}&end_date={end}&hourly=temperature_2m&timezone=UTC")
AREAS = ("NO1", "NO2", "NO3", "NO4", "NO5")
STEP_S = 3600

Fetch = Callable[[str], bytes]


class ExogenousError(RuntimeError):
    pass


def http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https hosts
        return r.read()


def load_site(path: str | Path) -> dict[str, Any]:
    site = json.loads(Path(path).read_text("utf-8"))
    if site.get("schema") != "ot-site/v1":
        raise ExogenousError("unsupported site schema")
    lat, lon = float(site["location"]["lat"]), float(site["location"]["lon"])
    area = site["price_area"]["code"]
    if area not in AREAS:
        raise ExogenousError(f"unknown price area {area!r}")
    box = site["price_area"]["consistency_box"]
    if not (box["lat_min"] <= lat <= box["lat_max"] and box["lon_min"] <= lon <= box["lon_max"]):
        raise ExogenousError(f"coordinate {lat},{lon} is outside the declared {area} box")
    return site


def _days(first: date, last: date) -> list[date]:
    if last < first:
        raise ExogenousError("--to is before --from")
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_prices(raw: bytes) -> list[tuple[datetime, float]]:
    """hvakosterstrommen rows -> [(start, NOK/kWh)], hourly, sorted, gap-free."""
    rows = json.loads(raw.decode("utf-8"))
    out = []
    for r in rows:
        start = datetime.fromisoformat(r["time_start"])
        end = datetime.fromisoformat(r["time_end"])
        if (end - start).total_seconds() != STEP_S:
            raise ExogenousError(f"price row is not one hour: {r['time_start']}..{r['time_end']}")
        out.append((start.astimezone(UTC), float(r["NOK_per_kWh"])))
    return sorted(out)


def parse_weather(raw: bytes) -> list[tuple[datetime, float]]:
    body = json.loads(raw.decode("utf-8"))
    hourly = body["hourly"]
    if body.get("hourly_units", {}).get("temperature_2m") != "°C":
        raise ExogenousError("weather unit is not °C")
    out = []
    for t, v in zip(hourly["time"], hourly["temperature_2m"], strict=True):
        if v is None:
            raise ExogenousError(f"weather value missing at {t}")
        out.append((datetime.fromisoformat(t).replace(tzinfo=UTC), float(v)))
    return out


def _series(points: list[tuple[datetime, float]], start: datetime, end: datetime) -> list[float]:
    """Values for every hour in [start, end), or refuse on a gap or duplicate."""
    by_t = {}
    for t, v in points:
        if t in by_t:
            raise ExogenousError(f"duplicate value at {_iso(t)}")
        by_t[t] = v
    values, t = [], start
    while t < end:
        if t not in by_t:
            raise ExogenousError(f"no value for {_iso(t)}")
        values.append(by_t[t])
        t += timedelta(seconds=STEP_S)
    return values


def _window(first: date, last: date) -> tuple[datetime, datetime]:
    start = datetime(first.year, first.month, first.day, tzinfo=UTC)
    return start, start + timedelta(days=(last - first).days + 1)


def price_urls(area: str, first: date, last: date) -> list[str]:
    # Prices are published per local (Europe/Oslo) day, which starts at 23:00
    # or 22:00 UTC the day before. The UTC window's last hour or two therefore
    # belong to the next local day, which is fetched as well.
    return [PRICE_URL.format(y=d.year, md=d.strftime("%m-%d"), area=area)
            for d in _days(first, last + timedelta(days=1))]


def weather_urls(lat: float, lon: float, first: date, last: date) -> list[str]:
    return [WEATHER_URL.format(lat=lat, lon=lon, start=first.isoformat(), end=last.isoformat())]


def fetch_kind(kind: str, site: dict[str, Any], first: date, last: date,
               get: Fetch = http_get, now: datetime | None = None) -> dict[str, Any]:
    area = site["price_area"]["code"]
    lat, lon = float(site["location"]["lat"]), float(site["location"]["lon"])
    urls = price_urls(area, first, last) if kind == "price" else weather_urls(lat, lon, first, last)
    parse = parse_prices if kind == "price" else parse_weather
    raws: dict[str, str] = {}
    points: list[tuple[datetime, float]] = []
    for url in urls:
        try:
            raw = get(url)
        except (urllib.error.URLError, OSError) as e:
            raise ExogenousError(f"{kind}: {url} unreachable: {e}") from None
        raws[url] = hashlib.sha256(raw).hexdigest()
        points += parse(raw)
    start, end = _window(first, last)
    values = _series(points, start, end)
    if kind == "price":
        source = {"name": "hvakosterstrommen.no", "upstream": "ENTSO-E / Nord Pool day-ahead",
                  "area": area, "attribution": "Prices from hvakosterstrommen.no "
                  "(day-ahead result republished per Norwegian price area)"}
        limitations = ["single source, re-fetch verified only; a second independent price "
                       "source (ENTSO-E Transparency) is NOT_ESTABLISHED",
                       "spot price only: no grid tariff, VAT or supplier markup",
                       "hourly as published by the source; whether an hour is an aggregate "
                       "of 15-minute day-ahead products is UNCERTAIN"]
    else:
        source = {"name": "Open-Meteo historical weather API", "upstream": "ERA5 reanalysis",
                  "point": {"lat": lat, "lon": lon},
                  "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)"}
        limitations = ["reanalysis at the grid cell nearest the coordinate, not a station "
                       "observation", "single source, re-fetch verified only"]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "kind": kind,
        "unit": "NOK/kWh" if kind == "price" else "degC",
        "site": site["building_id"],
        "start_utc": _iso(start),
        "step_seconds": STEP_S,
        "values": values,
        "provenance": {"source": source, "retrieved_utc": _iso(now or datetime.now(UTC)),
                       "requests": [{"url": u, "sha256": h} for u, h in raws.items()],
                       "range": {"from": first.isoformat(), "to": last.isoformat()},
                       "limitations": limitations},
    }


def verify_snapshot(snap: dict[str, Any], get: Fetch = http_get) -> dict[str, Any]:
    kind = snap["kind"]
    parse = parse_prices if kind == "price" else parse_weather
    identical, points, problems = True, [], []
    for req in snap["provenance"]["requests"]:
        try:
            raw = get(req["url"])
        except (urllib.error.URLError, OSError) as e:
            return {"kind": kind, "outcome": "UNREACHABLE", "detail": f"{req['url']}: {e}"}
        if hashlib.sha256(raw).hexdigest() != req["sha256"]:
            identical = False
        try:
            points += parse(raw)
        except (ExogenousError, ValueError, KeyError) as e:
            problems.append(f"{req['url']}: {e}")
    if problems:
        return {"kind": kind, "outcome": "DIVERGED", "detail": "; ".join(problems)}
    start = datetime.fromisoformat(snap["start_utc"].replace("Z", "+00:00"))
    end = start + timedelta(seconds=snap["step_seconds"] * len(snap["values"]))
    try:
        values = _series(points, start, end)
    except ExogenousError as e:
        return {"kind": kind, "outcome": "DIVERGED", "detail": str(e)}
    if values != snap["values"]:
        diff = sum(1 for a, b in zip(values, snap["values"], strict=True) if a != b)
        return {"kind": kind, "outcome": "DIVERGED", "detail": f"{diff} of {len(values)} values differ"}
    return {"kind": kind, "outcome": "VERIFIED_IDENTICAL" if identical else "VERIFIED_EQUIVALENT",
            "detail": f"{len(values)} hourly values re-fetched"}


def _write(path: Path, snap: dict[str, Any]) -> str:
    data = (json.dumps(snap, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.exogenous")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--site", required=True)
    f.add_argument("--from", dest="first", required=True, type=date.fromisoformat)
    f.add_argument("--to", dest="last", required=True, type=date.fromisoformat)
    f.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("snapshots", nargs="+")
    a = p.parse_args(argv)
    if a.cmd == "fetch":
        site = load_site(a.site)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for kind in ("price", "outdoor"):
            snap = fetch_kind(kind, site, a.first, a.last)
            tag = site["price_area"]["code"] if kind == "price" else "temperature"
            name = f"{kind}-{tag}-{a.first.isoformat()}_{a.last.isoformat()}.json"
            digest = _write(out / name, snap)
            print(json.dumps({"file": str(out / name), "sha256": digest,
                              "values": len(snap["values"])}))
        return 0
    results = []
    for path in a.snapshots:
        r = verify_snapshot(json.loads(Path(path).read_text("utf-8")))
        results.append({"file": path, **r})
        print(json.dumps(results[-1], ensure_ascii=False))
    return 0 if all(r["outcome"].startswith("VERIFIED") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
