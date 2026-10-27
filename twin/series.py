"""Verified exogenous series: real price and weather snapshots for the twin.

A scenario may take its outdoor temperature or its electricity price from a
snapshot file written by ``runner.exogenous`` instead of the synthetic curves.
The scenario names the file and its SHA-256. Loading refuses a file whose bytes
do not match, a series that does not cover the scenario span, or a gap, so a
run can never silently fall back to a curve or to zero (SDD §5).

Snapshot schema ``ot-exogenous-snapshot/v1`` (see ``runner/exogenous.py``):
``kind`` (``price`` | ``outdoor``), ``unit``, ``start_utc``, ``step_seconds``
and ``values``, plus the provenance block that ``runner.exogenous verify``
checks against the source.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from twin.clock import parse_utc

SNAPSHOT_SCHEMA = "ot-exogenous-snapshot/v1"
UNITS = {"price": "NOK/kWh", "outdoor": "degC", "solar": "W/m2", "humidity": "%",
         "precipitation": "mm/h"}


class SeriesError(ValueError):
    pass


@dataclass(frozen=True)
class Series:
    kind: str
    unit: str
    start_s: float      # POSIX seconds of the first value
    step_s: int
    values: tuple[float, ...]
    sha256: str
    provenance: dict[str, Any]

    def at(self, posix_s: float) -> float:
        """The value of the step containing ``posix_s``.

        The series' closing instant is included and reads the last step: the
        twin records its trace at the end of the span, and that reading is the
        last hour's value, not an extrapolation past the data.
        """
        idx = int((posix_s - self.start_s) // self.step_s)
        if idx == len(self.values) and posix_s == self.start_s + self.step_s * len(self.values):
            idx -= 1
        if idx < 0 or idx >= len(self.values):
            raise SeriesError(f"{self.kind} series does not cover t={posix_s}")
        return self.values[idx]

    def covers(self, start_s: float, duration_s: int) -> bool:
        return (self.start_s <= start_s
                and start_s + duration_s <= self.start_s + self.step_s * len(self.values))


def load_series(ref: dict[str, Any], base_dir: Path, kind: str) -> Series:
    """Load ``{"series_ref": path, "sha256": hex}`` relative to the scenario file."""
    path, want = ref.get("series_ref"), ref.get("sha256")
    if not isinstance(path, str) or not isinstance(want, str) or len(want) != 64:
        raise SeriesError(f"{kind}: series_ref and a 64-hex sha256 are required")
    p = (base_dir / path).resolve()
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise SeriesError(f"{kind}: cannot read {path}: {e}") from None
    got = hashlib.sha256(raw).hexdigest()
    if got != want:
        raise SeriesError(f"{kind}: {path} sha256 {got} does not match the scenario's {want}")
    snap = json.loads(raw.decode("utf-8"))
    if snap.get("schema") != SNAPSHOT_SCHEMA or snap.get("kind") != kind:
        raise SeriesError(f"{kind}: {path} is not an {SNAPSHOT_SCHEMA} {kind} snapshot")
    if snap.get("unit") != UNITS[kind]:
        raise SeriesError(f"{kind}: unit {snap.get('unit')!r}, expected {UNITS[kind]!r}")
    step = snap.get("step_seconds")
    values = snap.get("values")
    if not isinstance(step, int) or step <= 0 or not isinstance(values, list) or not values:
        raise SeriesError(f"{kind}: step_seconds and a non-empty values list are required")
    out: list[float] = []
    for i, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise SeriesError(f"{kind}: value {i} is missing or not finite ({v!r})")
        out.append(float(v))
    return Series(kind=kind, unit=snap["unit"], start_s=parse_utc(snap["start_utc"]).timestamp(),
                  step_s=step, values=tuple(out), sha256=got,
                  provenance=dict(snap.get("provenance") or {}))
