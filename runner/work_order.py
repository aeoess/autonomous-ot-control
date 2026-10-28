"""Signed synthetic work orders and the normalized call (authority-fixture contract).

The contract is docs/federation/authority-fixture-contract.md. The testbed
derives one **normalized call** from a proposal; the APS delegation check and
the work-order check below both evaluate that same call. The work order is
signed with a **test-only** Ed25519 key and binds an APS delegation by
``delegation_id`` and the SHA-256 of the delegation's RFC 8785 JCS form
(runner.jcs). A valid delegation alone never authorizes a call, and this
check never authorizes one either: it can only pass or refuse, and the
testbed composes it as an added refusal (runner.admission).

Test keys: each issuer's private key is derived from a public string, so
anyone can sign as that issuer. That is deliberate: the fixtures must be
reproducible, and no such key may ever be trusted outside this testbed.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from runner import jcs

SCHEMA = "ot-work-order/v1"
CALL_SCHEMA = "ot-normalized-call/v1"
TEST_ISSUER = "testbed-test-key-1"
TEST_KEY_LABEL = "autonomous-ot-control TEST-ONLY work-order signing key: "
UNITS = {"setpoint_c": "degC"}
# The one write tool the A/B/C episode uses, and the argument that is its asset and its value.
TOOLS = {"set_zone_setpoint": {"asset": "zone_id", "parameter": "setpoint_c"}}
FIELDS = ("building_id", "asset", "parameter", "operation")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issuer_test_key(issuer: str = TEST_ISSUER) -> Ed25519PrivateKey:
    """TEST ONLY: the seed is the SHA-256 of a public label, so the key is known to everyone."""
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256((TEST_KEY_LABEL + issuer).encode("utf-8")).digest())


def public_key_b64url(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return b64url(pub.public_bytes(Encoding.Raw, PublicFormat.Raw))


def issuer_test_keys(*issuers: str) -> dict[str, str]:
    """Issuer id -> raw Ed25519 public key, base64url: what a fixture set ships."""
    return {i: public_key_b64url(issuer_test_key(i)) for i in (issuers or (TEST_ISSUER,))}


def utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> datetime:
    if not isinstance(s, str) or not s.endswith("Z"):
        raise ValueError(f"not a UTC timestamp ending in Z: {s!r}")
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def decision_time(episode_start_utc: str, sim_s: int) -> str:
    """Fixed decision time: the episode's start plus simulated seconds, never the wall clock."""
    return utc(parse_utc(episode_start_utc) + timedelta(seconds=int(sim_s)))


def normalized_call(call: dict[str, Any], *, building_id: str, decision_time_utc: str) -> dict[str, Any]:
    """The one input both checks evaluate, derived from a proposal's tool call."""
    tool = call.get("tool_name")
    spec = TOOLS.get(tool)
    if spec is None:
        raise ValueError(f"no normalized form for tool {tool!r}")
    args = call.get("arguments") or {}
    value = args.get(spec["parameter"])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{spec['parameter']} is not a number")
    parse_utc(decision_time_utc)
    return {"schema": CALL_SCHEMA, "building_id": building_id, "asset": args.get(spec["asset"]),
            "parameter": spec["parameter"], "operation": tool, "value": value,
            "unit": UNITS[spec["parameter"]], "decision_time": decision_time_utc}


def delegation_sha256(delegation: Any) -> str:
    """SHA-256 of the complete signed delegation in its RFC 8785 JCS form."""
    return jcs.sha256_hex(delegation)


def signing_bytes(work_order: dict[str, Any]) -> bytes:
    return jcs.canonicalize({k: v for k, v in work_order.items() if k != "signature"})


def sign(work_order: dict[str, Any], key: Ed25519PrivateKey, issuer: str) -> dict[str, Any]:
    wo = {k: v for k, v in work_order.items() if k != "signature"}
    wo["issuer"] = issuer
    wo["signature"] = b64url(key.sign(signing_bytes(wo)))
    return wo


def verify_signature(work_order: dict[str, Any], issuer_keys: dict[str, str]) -> str | None:
    """None when the signature holds; otherwise the refusal reason."""
    pub = issuer_keys.get(work_order.get("issuer"))
    if pub is None:
        return "unknown_issuer"
    sig = work_order.get("signature")
    if not isinstance(sig, str):
        return "signature_missing"
    try:
        Ed25519PublicKey.from_public_bytes(_unb64url(pub)).verify(_unb64url(sig), signing_bytes(work_order))
    except (InvalidSignature, ValueError, jcs.JCSError):
        return "signature_invalid"
    return None


REQUIRED = ("schema", "work_order_id", "building_id", "asset", "parameter", "operation", "value_range",
            "valid_from", "valid_until", "delegation_id", "delegation_sha256", "issuer", "signature")


def check(work_order: dict[str, Any], call: dict[str, Any], issuer_keys: dict[str, str], *,
          delegation: Any = None) -> dict[str, Any]:
    """The work-order check: ``pass`` or ``refuse`` with every reason found, never an authorization.

    With ``delegation`` given, the work order must also bind exactly that delegation
    (its JCS SHA-256). The delegation's own validity and scope are APS's check, not this one.
    """
    reasons: list[str] = []
    missing = [k for k in REQUIRED if k not in work_order]
    if missing or work_order.get("schema") != SCHEMA:
        return {"verdict": "refuse", "reasons": ["malformed"], "missing": missing}
    if (r := verify_signature(work_order, issuer_keys)) is not None:
        reasons.append(r)
    try:
        t = parse_utc(call["decision_time"])
        if t < parse_utc(work_order["valid_from"]):
            reasons.append("not_yet_valid")
        if t >= parse_utc(work_order["valid_until"]):
            reasons.append("expired")
    except (KeyError, ValueError):
        reasons.append("malformed_time")
    for f in FIELDS:
        if call.get(f) != work_order.get(f):
            reasons.append(f"{f}_mismatch")
    rng = work_order.get("value_range") or {}
    lo, hi, value = rng.get("min"), rng.get("max"), call.get("value")
    if call.get("unit") != rng.get("unit"):
        reasons.append("unit_mismatch")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (lo, hi, value)) or lo > hi:
        reasons.append("malformed_range")
    elif value < lo:
        reasons.append("value_below_range")
    elif value > hi:
        reasons.append("value_above_range")
    if delegation is not None:
        try:
            if delegation_sha256(delegation) != work_order.get("delegation_sha256"):
                reasons.append("delegation_digest_mismatch")
        except jcs.JCSError:
            reasons.append("delegation_not_canonicalizable")
    return {"verdict": "refuse" if reasons else "pass", "reasons": reasons}
