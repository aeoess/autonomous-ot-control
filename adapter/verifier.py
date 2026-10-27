"""SDK effect-attestation helper (OT-003), aligned to the pinned remora.sdk.

After a governed dispatch, the effect is observed through the independent
read-only observer (``twin.effect_observer``) and recorded in REMORA using the
real effect API verified at commit 2594d08b:

    build_postcondition(*, tool_id, target_selector, expected_fields,
                        comparison_rules=None, reader="", ...) -> PostconditionSpec
    verify_effect(spec, observed, *, proposal_id, execution_id, toolspec_hash,
                  verifier_identity, ...) -> EffectVerificationView
    RemoraClient.record_effect(proposal_id, EffectVerificationView) -> ...

Verification runs in this process because the reader holds the read credential;
only hashes cross back to REMORA. ``observed=None`` (reader could not see the
object) is UNOBSERVABLE, never MISMATCH, and is never a retry permit
(INV-05/INV-10). The SDK is imported lazily (it ships only in the REMORA image /
governed runner image) and may be injected for tests.
"""
from __future__ import annotations

from typing import Any

from twin.effect_observer import (EFFECT_UNOBSERVABLE, EFFECT_VERIFIER_FAILED, EffectObserver)

TOOL_ID = "set_zone_setpoint"
DEFAULT_VERIFIER_IDENTITY = "ot-effect-observer/v1"


def _load_sdk() -> Any | None:
    try:
        import remora.sdk as sdk  # noqa: PLC0415 - optional, pinned deployment dependency
    except Exception:
        return None
    return sdk


class EffectAttestor:
    def __init__(self, observer: EffectObserver, *, sdk: Any | None = None,
                 verifier_identity: str = DEFAULT_VERIFIER_IDENTITY) -> None:
        self._observer = observer
        self._sdk = sdk if sdk is not None else _load_sdk()
        self._verifier_identity = verifier_identity

    @property
    def sdk_available(self) -> bool:
        return self._sdk is not None

    def attest(self, *, proposal_id: str, execution_id: str, toolspec_hash: str,
               zone_id: str, expected_setpoint_c: float, operation_id: str,
               client: Any | None = None) -> dict[str, Any]:
        """Observe the effect, build+verify a postcondition, and record it.

        Returns the observation envelope plus the SDK verification view (as a
        dict) and ``remora_recorded``. Without the SDK, returns the honest
        observation only and records nothing.
        """
        envelope = self._observer.observe(operation_id, zone_id=zone_id,
                                          expected_setpoint_c=expected_setpoint_c)
        result: dict[str, Any] = {"observation": envelope, "remora_recorded": False}
        if self._sdk is None:
            result["verification"] = None
            return result

        spec = self._sdk.build_postcondition(
            tool_id=TOOL_ID,
            target_selector={"zone_id": zone_id},
            expected_fields={"setpoint_c": expected_setpoint_c},
            reader=self._verifier_identity,
        )
        observed = self._observed_fields(envelope)
        view = self._sdk.verify_effect(
            spec, observed,
            proposal_id=proposal_id, execution_id=execution_id,
            toolspec_hash=toolspec_hash, verifier_identity=self._verifier_identity,
        )
        result["verification"] = view.to_dict() if hasattr(view, "to_dict") else view
        if client is not None:
            try:
                recorded = client.record_effect(proposal_id, view)
                result["remora_recorded"] = True
                result["recorded"] = recorded.to_dict() if hasattr(recorded, "to_dict") else recorded
            except Exception as e:  # recording failed; do not claim it succeeded
                result["remora_record_error"] = f"{type(e).__name__}: {e}"
        return result

    @staticmethod
    def _observed_fields(envelope: dict[str, Any]) -> dict[str, Any] | None:
        """The observed delta fields, or None when the object was unobservable.

        A stale/absent reading is UNOBSERVABLE: not knowing is not the same as
        knowing the value is wrong, so hand the SDK ``None`` rather than a guess.
        """
        status = envelope.get("effect_status")
        if status in (EFFECT_UNOBSERVABLE, EFFECT_VERIFIER_FAILED):
            return None
        if "observed_setpoint_c" not in envelope:
            return None
        return {"setpoint_c": envelope["observed_setpoint_c"]}
