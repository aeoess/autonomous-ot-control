"""Signed work orders (SDD-OT-002 SR-05, F-05).

A work order is an entry of the intent table REMORA grounds a write against (``intent_ref`` -> entry). Before
SR-05 the table was unsigned JSON that the proposing process rewrote, and the bundle labelled every entry
``signed_work_order`` although nothing was signed. Now an entry counts as a work order only when it carries an
Ed25519 signature by a trusted work-order authority key, over the entry and the reference it is filed under:

    entry["signature"] = {"alg": "Ed25519", "key_id": "wo-<16 hex>", "sig": "<base64url>"}
    signed bytes      = json({"intent_ref": ref, "entry": entry without "signature"}), sorted keys, no spaces

The authority key is not the proposer's: on this testbed it lives in its own container (``runner.work_order
_authority``), which issues only the day plans and lab orders it builds itself from fixed templates. The
proposer can still write the table file, but what it writes without the key is not a work order.

Standard library plus ``cryptography``; importable offline and inside the REMORA image (the bundle uses it).
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ALG = "Ed25519"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_raw(key: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    return "wo-" + hashlib.sha256(public_raw(key)).hexdigest()[:16]


def signing_bytes(intent_ref: str, entry: dict[str, Any]) -> bytes:
    body = {k: v for k, v in entry.items() if k != "signature"}
    return json.dumps({"intent_ref": intent_ref, "entry": body}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign_entry(intent_ref: str, entry: dict[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    body = {k: v for k, v in entry.items() if k != "signature"}
    return {**body, "signature": {"alg": ALG, "key_id": key_id(key),
                                  "sig": _b64(key.sign(signing_bytes(intent_ref, body)))}}


def verify_entry(intent_ref: str, entry: Any, trusted: dict[str, str]) -> tuple[str | None, str]:
    """(key_id, "ok") when the entry is signed by a trusted key for this reference, else (None, why)."""
    if not isinstance(entry, dict):
        return None, "not_an_entry"
    sig = entry.get("signature")
    if not isinstance(sig, dict):
        return None, "unsigned"
    kid = sig.get("key_id")
    if sig.get("alg") != ALG or not isinstance(kid, str) or not isinstance(sig.get("sig"), str):
        return None, "malformed_signature"
    pub = trusted.get(kid)
    if pub is None:
        return None, "untrusted_key"
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(pub)).verify(_unb64(sig["sig"]), signing_bytes(intent_ref, entry))
    except (InvalidSignature, ValueError):
        return None, "bad_signature"
    return kid, "ok"


def entry_digest(intent_ref: str, entry: dict[str, Any]) -> str:
    return hashlib.sha256(signing_bytes(intent_ref, entry)).hexdigest()


# -- keys -------------------------------------------------------------------------------------------------

def key_from_seed(seed_hex: str) -> Ed25519PrivateKey:
    """The authority key from its 32-byte hex seed (deploy/.keys/work_order.seed, made by the bootstrap)."""
    raw = bytes.fromhex(seed_hex.strip())
    if len(raw) != 32:
        raise ValueError("a work-order seed is 32 bytes of hex")
    return Ed25519PrivateKey.from_private_bytes(raw)


def trusted_document(*keys: Ed25519PrivateKey | Ed25519PublicKey) -> dict[str, Any]:
    return {"schema": "ot-work-order-keys/v1", "keys": {key_id(k): _b64(public_raw(k)) for k in keys}}


def trusted_env_value(*keys: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    """The trust anchor as one environment value, ``<key id>:<base64url public key>[,...]``.

    It is set in the gitignored ``.env`` by the bootstrap, beside REMORA's other trusted keys, and never read
    from a file: the directory the intent table lives in is writable by the authority and by the proposer, so a
    keys file there could be swapped for the proposer's own key."""
    return ",".join(f"{key_id(k)}:{_b64(public_raw(k))}" for k in keys)


def parse_trusted_env(value: str) -> dict[str, str]:
    """``<key id>:<public key>[,...]`` back to {key id: public key}; a key id must be the hash of its key."""
    out: dict[str, str] = {}
    for part in (p.strip() for p in value.split(",") if p.strip()):
        kid, sep, pub = part.partition(":")
        if not sep:
            raise ValueError("a trusted work-order key is <key id>:<public key>")
        if key_id(Ed25519PublicKey.from_public_bytes(_unb64(pub))) != kid:
            raise ValueError(f"key id {kid} is not the id of its public key")
        out[kid] = pub
    return out
