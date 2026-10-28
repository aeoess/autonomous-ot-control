"""RFC 8785 JSON Canonicalization Scheme (JCS).

    jcs.canonicalize(value) -> bytes        # UTF-8
    jcs.sha256_hex(value) -> str            # SHA-256 over those bytes

The authority-fixture contract (docs/federation/authority-fixture-contract.md)
binds a work order to an APS delegation by the SHA-256 of the delegation's
JCS form, so both sides must produce the same bytes. Python's
``json.dumps(sort_keys=True, separators=(",", ":"))`` is close but is not JCS:

- numbers: JCS serialises every number as an IEEE-754 double the way
  ECMAScript's Number.prototype.toString does, so ``20.0`` is ``20``,
  ``1e30`` is ``1e+30`` and ``0.000001`` is ``0.000001`` (Python: ``1e-06``);
- object keys are sorted by their UTF-16 code units, not by code points;
- strings escape only ``"``, ``\\`` and U+0000..U+001F (with the short forms
  ``\\b \\f \\n \\r \\t``, otherwise lowercase ``\\u00xx``); everything else,
  ``/`` and non-ASCII included, is written as UTF-8.

Rejected rather than guessed: NaN and the infinities, integers that a double
cannot hold exactly (|n| > 2**53), lone surrogates, non-string keys and any
type JSON does not have. The other canonical form in this repository
(``runner.federation_fixtures.canonical_sha256``) is unchanged; it serves the
sealed fixture sets and stays as their digest rule.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

_MAX_SAFE_INT = 2 ** 53
_SHORT = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r", 0x22: '\\"', 0x5C: "\\\\"}


class JCSError(ValueError):
    """The value has no RFC 8785 form."""


def _number(v: float) -> str:
    if math.isnan(v) or math.isinf(v):
        raise JCSError("NaN and Infinity have no JSON form")
    if v == 0:
        return "0"  # also -0
    sign = "-" if v < 0 else ""
    # repr gives the shortest digits that round-trip, as ECMAScript's algorithm requires.
    r = repr(abs(v))
    mant, _, exp = r.partition("e")
    digits = mant.replace(".", "")
    point = mant.index(".") if "." in mant else len(mant)
    n = point + (int(exp) if exp else 0)  # value = 0.<digits> * 10**n after stripping zeros below
    stripped = digits.lstrip("0")
    n -= len(digits) - len(stripped)
    digits = stripped.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        out = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        c = ord(ch)
        if 0xD800 <= c <= 0xDFFF:
            raise JCSError("lone surrogate in string")
        if c in _SHORT:
            out.append(_SHORT[c])
        elif c < 0x20:
            out.append(f"\\u{c:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _utf16_key(s: str) -> list[int]:
    return list(s.encode("utf-16-be"))


def _serialize(v: Any, out: list[str]) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, int):
        if abs(v) > _MAX_SAFE_INT:
            raise JCSError(f"integer {v} is outside the range a double holds exactly")
        out.append(_number(float(v)))
    elif isinstance(v, float):
        out.append(_number(v))
    elif isinstance(v, str):
        out.append(_string(v))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _serialize(x, out)
        out.append("]")
    elif isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise JCSError("object keys must be strings")
        out.append("{")
        for i, k in enumerate(sorted(v, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(_string(k))
            out.append(":")
            _serialize(v[k], out)
        out.append("}")
    else:
        raise JCSError(f"{type(v).__name__} has no JSON form")


def canonicalize(value: Any) -> bytes:
    out: list[str] = []
    _serialize(value, out)
    return "".join(out).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()
