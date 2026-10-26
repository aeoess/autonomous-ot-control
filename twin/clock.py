"""Virtual simulation clock.

The simulation clock is *not* REMORA's authority clock. It only drives plant
physics and scenario time. Grant/lease expiry always uses the authoritative
service's wall clock (SDD §5).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"timestamp must be UTC ISO-8601 ending in 'Z': {value!r}")
    dt = datetime.fromisoformat(value[:-1] + "+00:00")
    return dt.astimezone(UTC)


def format_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class VirtualClock:
    start_utc: datetime
    step_seconds: int
    now_s: int = 0

    def __post_init__(self) -> None:
        if self.step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        if self.now_s < 0 or self.now_s % self.step_seconds:
            raise ValueError("now_s must be a non-negative multiple of step_seconds")

    def tick(self) -> int:
        self.now_s += self.step_seconds
        return self.now_s

    def utc_at(self, sim_s: int) -> datetime:
        return self.start_utc + timedelta(seconds=sim_s)

    @property
    def now_utc(self) -> datetime:
        return self.utc_at(self.now_s)

    def stamp(self, sim_s: int | None = None) -> dict[str, object]:
        """Simulation time and the simulated UTC instant as separate fields (SDD §12)."""
        s = self.now_s if sim_s is None else sim_s
        return {"sim_time_s": s, "sim_utc": format_utc(self.utc_at(s))}
