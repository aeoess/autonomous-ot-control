"""SDD-OT-002 SR-11 (ST-11): a verification is never re-dated to follow its dispatch.

Before the fix the attestor rewrote ``verified_at`` to max(dispatch, now) when the observation preceded the
dispatch, so "observed after dispatch" was asserted, not observed. Driven through the real recording path
(the client branch with ``_request``) with a labelled fake SDK and fake REMORA client:
- every reading precedes the dispatch: observed twice more, then recorded with observed=None (unobservable);
  nothing posted carries a re-dated time;
- the first reading precedes the dispatch and a later one does not: recorded verified, at the later reading's
  own time;
- a reading after the dispatch is recorded once, unchanged.
"""
from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from adapter import verifier as vmod
from adapter.verifier import EffectAttestor
from tests.conftest import cmd
from twin.api import build_read_app
from twin.effect_observer import EffectObserver

OP = "remora-proposal-order-1"
DISPATCH = "2026-01-15T07:00:05+00:00"


class FakeView:
    def __init__(self, payload):
        self._p = payload

    def to_dict(self):
        return dict(self._p)


class ClockSDK:
    """Labelled fake: verify_effect stamps each view with the next reading time from ``times``."""

    def __init__(self, times):
        self.times, self.calls = list(times), []

    def build_postcondition(self, **kw):
        return {"spec": kw}

    def verify_effect(self, spec, observed, **kw):
        self.calls.append(observed)
        status = "UNOBSERVABLE" if observed is None else "VERIFIED"
        return FakeView({"status": status, "observed": observed, "verified_at": self.times.pop(0),
                         "tool_call_hash": "h", "grant_jti": "j", **kw})


class FakeRemora:
    def __init__(self):
        self.posts = []
        self._dispatch_binding = object()

    def _request(self, method, path, json=None):
        if method == "GET":
            return {"events": [{"event": "execution_result", "timestamp": DISPATCH}]}
        self.posts.append(json)
        return {}


@pytest.fixture(autouse=True)
def fake_sdk_modules(monkeypatch):
    client_mod = types.ModuleType("remora.sdk.client")
    client_mod.bind_verification = lambda body, binding: body
    client_mod.dispatch_binding = lambda trail: {}
    effects_mod = types.ModuleType("remora.sdk.effects")
    effects_mod.EffectVerificationView = type("V", (), {"from_payload": staticmethod(lambda p: FakeView(p))})
    for name, mod in (("remora", types.ModuleType("remora")), ("remora.sdk", types.ModuleType("remora.sdk")),
                      ("remora.sdk.client", client_mod), ("remora.sdk.effects", effects_mod)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(vmod, "REOBSERVE_WAIT_S", (0.0, 0.0), raising=False)


def _attest(twin, tokens, times):
    twin.apply_setpoint(cmd(twin, OP, zone="ZONE-01", sp=20.5))
    observer = EffectObserver("http://testserver", tokens.read, tenant_id="ot-pilot", run_id="r",
                              client=TestClient(build_read_app(twin, tokens)))
    sdk, remora = ClockSDK(times), FakeRemora()
    out = EffectAttestor(observer, sdk=sdk).attest(proposal_id="P", execution_id="E", toolspec_hash="TS",
                                                    zone_id="ZONE-01", expected_setpoint_c=20.5, operation_id=OP,
                                                    client=remora)
    return out, sdk, remora


def test_st11_readings_before_the_dispatch_are_never_re_dated(twin, tokens):
    early = ["2026-01-15T07:00:01+00:00", "2026-01-15T07:00:02+00:00", "2026-01-15T07:00:03+00:00",
             "2026-01-15T07:00:04+00:00"]
    out, sdk, remora = _attest(twin, tokens, early)
    assert len(out["observed_before_dispatch"]) == 3  # first reading, then two re-observations
    assert sdk.calls[-1] is None  # recorded unobservable: no observation of this effect after its dispatch
    (posted,) = remora.posts
    assert posted["status"] == "UNOBSERVABLE" and "observed" not in posted or posted.get("observed") is None
    assert posted["verified_at"] == early[-1]  # its own time, never moved to follow the dispatch
    assert out["remora_recorded"] is True


def test_st11_a_later_reading_is_recorded_at_its_own_time(twin, tokens):
    times = ["2026-01-15T07:00:01+00:00", "2026-01-15T07:00:06+00:00"]
    out, sdk, remora = _attest(twin, tokens, times)
    (posted,) = remora.posts
    assert posted["status"] == "VERIFIED" and posted["verified_at"] == "2026-01-15T07:00:06+00:00"
    assert posted["observed"] == {"setpoint_c": 20.5} and len(out["observed_before_dispatch"]) == 1


def test_a_reading_after_the_dispatch_is_recorded_once_unchanged(twin, tokens):
    out, sdk, remora = _attest(twin, tokens, ["2026-01-15T07:00:09+00:00"])
    (posted,) = remora.posts
    assert posted["verified_at"] == "2026-01-15T07:00:09+00:00" and "observed_before_dispatch" not in out
