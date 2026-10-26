"""Independent read-only observation of an actuation's effect.

"Independent" means a separate credential and read path from the dispatcher
(vantage ``same_test_deployment``), not an independently operated party. The
observer cannot command the plant and never edits an authorized-call record.

Status vocabulary mirrors the SDD projection (§12):
  EFFECT_VERIFIED       operation APPLIED and the zone now reports the target
  EFFECT_MISMATCH       the plant state contradicts the expected effect
  EFFECT_UNOBSERVABLE   no record, stale reading, or plant unreachable
  EFFECT_VERIFIER_FAILED  the observer itself errored
None of these is a retry permit.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from twin.state import QUALITY_GOOD, canonical_digest

SOURCE = "simulated-twin-read-only-observer/v1"
VANTAGE = "same_test_deployment"

EFFECT_VERIFIED = "EFFECT_VERIFIED"
EFFECT_MISMATCH = "EFFECT_MISMATCH"
EFFECT_UNOBSERVABLE = "EFFECT_UNOBSERVABLE"
EFFECT_VERIFIER_FAILED = "EFFECT_VERIFIER_FAILED"


class EffectObserver:
    def __init__(self, base_url: str, read_token: str, *, tenant_id: str, run_id: str,
                 client: httpx.Client | None = None, timeout_s: float = 5.0) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._headers = {"Authorization": f"Bearer {read_token}"}
        self.tenant_id = tenant_id
        self.run_id = run_id

    def _get(self, path: str) -> httpx.Response:
        return self._client.get(path, headers=self._headers)

    def observe(self, operation_id: str, *, zone_id: str, expected_setpoint_c: float) -> dict[str, Any]:
        """Return an ``ot-observation/v1`` envelope with an effect status."""
        observed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        base: dict[str, Any] = {
            "schema": "ot-observation/v1", "tenant_id": self.tenant_id, "run_id": self.run_id,
            "operation_id": operation_id, "observed_at": observed_at, "zone_id": zone_id,
            "expected_setpoint_c": expected_setpoint_c, "source": SOURCE, "vantage": VANTAGE,
        }
        try:
            status = self._get("/v1/status")
            op = self._get(f"/v1/operations/{operation_id}")
            zone = self._get(f"/v1/zones/{zone_id}")
        except httpx.HTTPError as e:
            return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                               "detail": f"plant_unreachable:{type(e).__name__}"})
        try:
            if status.status_code == 401 or op.status_code == 401 or zone.status_code == 401:
                return self._seal({**base, "effect_status": EFFECT_VERIFIER_FAILED,
                                   "detail": "observer_unauthorized"})
            st = status.json()
            base["building_id"] = st["building_id"]
            if zone.status_code == 404:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE, "detail": "unknown_zone"})
            z = zone.json()
            base.update({"state_revision": z["state_revision"], "observed_setpoint_c": z["setpoint_c"],
                         "measurement_quality": z["quality"], "sim_time_s": z["sim_time_s"],
                         "sim_utc": z["sim_utc"]})
            if op.status_code == 404:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                                   "detail": "no_operation_record"})
            rec = op.json()
            base["operation_status"] = rec["status"]
            if rec["zone_id"] != zone_id:
                return self._seal({**base, "effect_status": EFFECT_MISMATCH, "detail": "zone_mismatch"})
            if rec["status"] != "APPLIED":
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": f"operation_rejected:{rec.get('reason')}"})
            if abs(float(rec["new_setpoint_c"]) - expected_setpoint_c) > 1e-6:
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": "recorded_setpoint_differs"})
            if z["quality"] != QUALITY_GOOD:
                return self._seal({**base, "effect_status": EFFECT_UNOBSERVABLE,
                                   "detail": "stale_measurement"})
            if abs(float(z["setpoint_c"]) - expected_setpoint_c) > 1e-6:
                # Applied once, but a later operation moved it. Report, do not guess.
                later = z["state_revision"] > rec["state_revision_after"]
                return self._seal({**base, "effect_status": EFFECT_MISMATCH,
                                   "detail": "superseded_by_later_operation" if later
                                   else "plant_setpoint_differs"})
            return self._seal({**base, "effect_status": EFFECT_VERIFIED, "detail": None})
        except (ValueError, KeyError, TypeError) as e:
            return self._seal({**base, "effect_status": EFFECT_VERIFIER_FAILED,
                               "detail": f"malformed_response:{type(e).__name__}"})

    @staticmethod
    def _seal(env: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in env.items() if k != "observation_digest"}
        return {**body, "observation_digest": canonical_digest(body)}
