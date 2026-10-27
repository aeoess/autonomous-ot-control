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

from twin.effect_observer import (EFFECT_MISMATCH, EFFECT_UNOBSERVABLE, EFFECT_VERIFIER_FAILED,
                                  EffectObserver)
from twin.state import canonical_digest

TOOL_ID = "set_zone_setpoint"
DEFAULT_VERIFIER_IDENTITY = "ot-effect-observer/v1"

#: Observer refusals that say "this record is not evidence for this claim".
#: For these the plant's current value is not an observation of the named
#: operation's effect, so it is never handed to the SDK, or to REMORA, for
#: comparison: a coincidentally matching value would come back VERIFIED and
#: the refusal would be lost. A plant value that genuinely differs
#: (plant_setpoint_differs, superseded_by_later_operation, plant_state_differs)
#: is compared as before, so the SDK and the server compute that MISMATCH
#: themselves.
BINDING_REFUSALS = frozenset({
    "operation_tool_mismatch", "operation_target_mismatch", "recorded_value_differs",
    "zone_mismatch", "recorded_setpoint_differs",
})


def binding_refusal(envelope: dict[str, Any]) -> str | None:
    """The observer's binding refusal in this envelope, or None."""
    if envelope.get("effect_status") != EFFECT_MISMATCH:
        return None
    detail = str(envelope.get("detail") or "")
    if detail in BINDING_REFUSALS or detail.startswith("operation_rejected:"):
        return detail
    return None


class _RefusedView:
    """A MISMATCH view built from the observer's refusal, when the SDK's own view class is unavailable."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._p = dict(payload)
        self.raw = dict(payload)
        self.status = payload["status"]
        self.reason_code = payload["reason_code"]

    def to_dict(self) -> dict[str, Any]:
        return dict(self._p)


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
        refusal = binding_refusal(envelope)
        if refusal is not None:
            observed = None  # not an observation of this operation's effect; never compared, never attached
            view = self._refused_view(envelope, refusal, tool_id=TOOL_ID,
                                      expected_fields={"setpoint_c": expected_setpoint_c},
                                      proposal_id=proposal_id, execution_id=execution_id,
                                      toolspec_hash=toolspec_hash)
        else:
            observed = self._observed_fields(envelope)
            view = self._sdk.verify_effect(
                spec, observed,
                proposal_id=proposal_id, execution_id=execution_id,
                toolspec_hash=toolspec_hash, verifier_identity=self._verifier_identity,
            )
        result["verification"] = view.to_dict() if hasattr(view, "to_dict") else view
        if client is not None:
            try:
                recorded = self._record_with_observation(
                    client, proposal_id, view,
                    observed=observed,
                    arguments={"zone_id": zone_id, "setpoint_c": expected_setpoint_c},
                )
                result["remora_recorded"] = True
                result["recorded"] = (
                    {**recorded.to_dict(), "verdict_source": getattr(recorded, "verdict_source", "")}
                    if hasattr(recorded, "to_dict") else recorded
                )
            except Exception as e:  # recording failed; do not claim it succeeded
                result["remora_record_error"] = f"{type(e).__name__}: {e}"
        return result

    def attest_tool(self, *, proposal_id: str, execution_id: str, toolspec_hash: str,
                    tool_id: str, arguments: dict[str, Any], operation_id: str,
                    client: Any | None = None) -> dict[str, Any]:
        """Observe and record the effect of any governed WRITE tool."""
        if tool_id == TOOL_ID:
            return self.attest(proposal_id=proposal_id, execution_id=execution_id,
                               toolspec_hash=toolspec_hash, zone_id=str(arguments["zone_id"]),
                               expected_setpoint_c=float(arguments["setpoint_c"]),
                               operation_id=operation_id, client=client)
        envelope = self._observer.observe_tool(operation_id, tool_name=tool_id, arguments=arguments)
        result: dict[str, Any] = {"observation": envelope, "remora_recorded": False}
        if self._sdk is None:
            result["verification"] = None
            return result
        selector, expected = self._selector_and_expected(tool_id, arguments)
        spec = self._sdk.build_postcondition(
            tool_id=tool_id,
            target_selector=selector,
            expected_fields=expected,
            reader=self._verifier_identity,
        )
        status = envelope.get("effect_status")
        refusal = binding_refusal(envelope)
        if refusal is not None:
            observed = None
            view = self._refused_view(envelope, refusal, tool_id=tool_id, expected_fields=expected,
                                      proposal_id=proposal_id, execution_id=execution_id,
                                      toolspec_hash=toolspec_hash)
        else:
            observed = (None if status in (EFFECT_UNOBSERVABLE, EFFECT_VERIFIER_FAILED)
                        else envelope.get("observed_fields"))
            view = self._sdk.verify_effect(
                spec, observed,
                proposal_id=proposal_id, execution_id=execution_id,
                toolspec_hash=toolspec_hash, verifier_identity=self._verifier_identity,
            )
        result["verification"] = view.to_dict() if hasattr(view, "to_dict") else view
        if client is not None:
            try:
                recorded = self._record_with_observation(
                    client, proposal_id, view, observed=observed, arguments=arguments)
                result["remora_recorded"] = True
                result["recorded"] = (
                    {**recorded.to_dict(), "verdict_source": getattr(recorded, "verdict_source", "")}
                    if hasattr(recorded, "to_dict") else recorded
                )
            except Exception as e:
                result["remora_record_error"] = f"{type(e).__name__}: {e}"
        return result

    def _refused_view(self, envelope: dict[str, Any], refusal: str, *, tool_id: str,
                      expected_fields: dict[str, Any], proposal_id: str, execution_id: str,
                      toolspec_hash: str) -> Any:
        """A MISMATCH view carrying the observer's binding refusal as its reason.

        Built without comparing values: the observer has said the named
        operation is not the source of whatever the plant shows, so there is
        nothing to compare. The payload carries no ``observed`` fields, so the
        recording boundary cannot attach any and a server cannot recompute
        VERIFIED from them. The observation envelope is referenced by its digest.
        """
        payload = {
            "proposal_id": proposal_id, "execution_id": execution_id, "tool_id": tool_id,
            "toolspec_hash": toolspec_hash, "status": EFFECT_MISMATCH, "reason_code": refusal,
            "verifier_identity": self._verifier_identity,
            "expected_sha256": canonical_digest(expected_fields),
            "observed_sha256": str(envelope.get("observation_digest", "")),
            "verified_at": str(envelope.get("observed_at", "")),
            "detail": f"observer refused to bind operation {envelope.get('operation_id')} to this claim: {refusal}",
            "vantage": "same_deployment", "scope": "declared_delta_only",
        }
        view_cls = getattr(self._sdk, "EffectVerificationView", None)
        if view_cls is None:
            try:
                from remora.sdk.effects import EffectVerificationView as view_cls  # noqa: N813
            except Exception:
                return _RefusedView(payload)
        try:
            return view_cls.from_payload(payload)
        except Exception:
            return _RefusedView(payload)

    @staticmethod
    def _selector_and_expected(tool_id: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if "zone_id" in arguments:
            sel = {"zone_id": arguments["zone_id"]}
            exp = {k: v for k, v in arguments.items() if k != "zone_id"}
            return sel, exp
        if "circuit" in arguments:
            return {"circuit": arguments["circuit"]}, {"mode": arguments["mode"]}
        sys_map = {
            "set_supply_air_setpoint": "AHU-01",
            "set_ahu_enabled": "AHU-01",
            "set_tank_setpoint": "TANK-01",
            "set_heat_pump_limit": "HP-01",
        }
        return {"system": sys_map.get(tool_id, tool_id)}, dict(arguments)

    @staticmethod
    def _record_with_observation(client: Any, proposal_id: str, view: Any, *,
                                 observed: dict[str, Any] | None,
                                 arguments: dict[str, Any]) -> Any:
        """Record the effect in REMORA, attaching observed & arguments when supported (ToolSpec v5)."""
        if hasattr(client, "_request") and hasattr(client, "_dispatch_binding"):
            from datetime import UTC, datetime
            from remora.sdk.client import bind_verification
            from remora.sdk.effects import EffectVerificationView

            body = view.to_dict() if hasattr(view, "to_dict") else dict(view)
            trail = client._request("GET", f"/v1/execution/proposals/{proposal_id}/lifecycle")
            if not body.get("tool_call_hash") or not body.get("grant_jti"):
                from remora.sdk.client import dispatch_binding
                body = bind_verification(body, dispatch_binding(trail))
            # Ensure verified_at is never before the server's dispatch timestamp
            # when two containers' VM clocks jitter by a few milliseconds.
            dispatched_at = ""
            for ev in trail.get("events") or []:
                if ev.get("event") == "execution_result" and ev.get("timestamp"):
                    dispatched_at = str(ev["timestamp"])
            if dispatched_at and body.get("verified_at"):
                try:
                    t_disp = datetime.fromisoformat(dispatched_at.replace("Z", "+00:00"))
                    t_obs = datetime.fromisoformat(str(body["verified_at"]).replace("Z", "+00:00"))
                    if t_obs < t_disp:
                        body["verified_at"] = max(t_disp, datetime.now(UTC)).isoformat()
                except ValueError:
                    pass
            if observed is not None:
                body["observed"] = observed
                body["arguments"] = arguments
            payload = client._request("POST", f"/v1/execution/proposals/{proposal_id}/effect", json=body)
            return EffectVerificationView.from_payload({**body, **payload})
        return client.record_effect(proposal_id, view)

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
