"""Plant state schema and immutable measurement views."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

QUALITY_GOOD = "GOOD"
QUALITY_STALE = "STALE"


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, finite numbers only."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def canonical_digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()


def r6(x: float) -> float:
    """Round for traces/digests so tiny float formatting never moves a digest."""
    if not math.isfinite(x):
        raise ValueError("non-finite value in plant state")
    return round(x, 6)


@dataclass
class ZoneState:
    zone_id: str
    tfm: str
    temp_c: float
    setpoint_c: float
    measured_temp_c: float
    sample_at_s: int
    heater_w: float = 0.0
    change_history_s: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OperationRecord:
    """One actuation request as received and decided by the twin.

    Stored for every operation ID, applied or rejected, so a replay returns the
    recorded outcome and never repeats an effect (INV-04).
    """
    operation_id: str
    request_digest: str
    status: str                      # APPLIED | REJECTED
    reason: str | None
    zone_id: str
    requested_setpoint_c: float | None
    previous_setpoint_c: float | None
    new_setpoint_c: float | None
    state_revision_before: int
    state_revision_after: int
    sim_time_s: int
    sim_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ZoneReading:
    """What the read path exposes: measured value plus its age and quality."""
    zone_id: str
    tfm: str
    measured_temp_c: float
    setpoint_c: float
    unit: str
    sample_sim_time_s: int
    age_seconds: int
    quality: str
    state_revision: int
    sim_time_s: int
    sim_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
