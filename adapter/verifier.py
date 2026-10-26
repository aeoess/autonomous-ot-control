"""SDK effect-attestation helper (OT-003).

After a governed dispatch, the effect is checked through the independent
read-only observer (``twin.effect_observer``), which uses a separate credential
and vantage (``same_test_deployment``). This helper binds that observation to a
REMORA operation so it can be recorded as a postcondition result.

Where the pinned ``remora.sdk`` exposes ``build_postcondition`` /
``verify_effect`` / ``record_effect`` (SDD §6), this helper hands the observed
envelope to them so REMORA holds the authoritative effect record. When the SDK
is not available (offline unit tests, the Docker-only simulator path), it still
returns the observed envelope and effect status, but makes **no** claim that
REMORA recorded it — ``remora_recorded`` is ``False`` and the status is never
promoted. A verifier status is never a retry permit (INV-05/INV-10).
"""
from __future__ import annotations

from typing import Any

from twin.effect_observer import (EFFECT_VERIFIED, EFFECT_VERIFIER_FAILED, EffectObserver)


def _load_sdk() -> Any | None:
    try:
        import remora.sdk as sdk  # noqa: PLC0415 - optional, pinned deployment dependency
    except Exception:
        return None
    return sdk


class EffectAttestor:
    def __init__(self, observer: EffectObserver, *, sdk: Any | None = None) -> None:
        self._observer = observer
        # Allow explicit injection for tests; otherwise discover the pinned SDK.
        self._sdk = sdk if sdk is not None else _load_sdk()

    @property
    def sdk_available(self) -> bool:
        return self._sdk is not None

    def attest(self, operation_id: str, *, zone_id: str, expected_setpoint_c: float,
               execution_token: str | None = None) -> dict[str, Any]:
        """Observe the effect and, when the SDK is present, record it in REMORA.

        Returns the observation envelope plus ``effect_status``, ``verified``
        (True only on ``EFFECT_VERIFIED``) and ``remora_recorded``.
        """
        envelope = self._observer.observe(operation_id, zone_id=zone_id,
                                          expected_setpoint_c=expected_setpoint_c)
        status = envelope.get("effect_status", EFFECT_VERIFIER_FAILED)
        result = {**envelope, "verified": status == EFFECT_VERIFIED, "remora_recorded": False}
        if self._sdk is None or execution_token is None:
            return result
        # The exact SDK symbols are reconciled against the pinned commit; a
        # failure to record must not masquerade as verification.
        try:
            postcondition = self._sdk.build_postcondition(
                operation_id=operation_id, observation=envelope)
            self._sdk.record_effect(execution_token, postcondition)
            result["remora_recorded"] = True
        except Exception as e:  # recording failed; keep the honest status
            result["remora_record_error"] = f"{type(e).__name__}: {e}"
        return result
