"""runner.jcs against RFC 8785.

- Appendix B: IEEE-754 doubles (given as hex bit patterns) and their JCS form;
  NaN and Infinity are rejected;
- section 3.2.2: the worked example (numbers, string escapes, literals);
- section 3.2.3: key order by UTF-16 code units, which differs from code-point
  order for characters outside the BMP;
- what is rejected rather than guessed: unsafe integers, lone surrogates,
  non-string keys, non-JSON types; and the difference from Python's
  json.dumps that makes this module necessary (20.0 -> 20).
"""
from __future__ import annotations

import hashlib
import json
import struct

import pytest

from runner import jcs

# RFC 8785 Appendix B
VECTORS = [
    ("0000000000000000", "0"), ("8000000000000000", "0"),
    ("0000000000000001", "5e-324"), ("8000000000000001", "-5e-324"),
    ("7fefffffffffffff", "1.7976931348623157e+308"), ("ffefffffffffffff", "-1.7976931348623157e+308"),
    ("4340000000000000", "9007199254740992"), ("c340000000000000", "-9007199254740992"),
    ("4430000000000000", "295147905179352830000"),
    ("44b52d02c7e14af5", "9.999999999999997e+22"), ("44b52d02c7e14af6", "1e+23"),
    ("44b52d02c7e14af7", "1.0000000000000001e+23"),
    ("444b1ae4d6e2ef4e", "999999999999999700000"), ("444b1ae4d6e2ef4f", "999999999999999900000"),
    ("444b1ae4d6e2ef50", "1e+21"),
    ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"), ("3eb0c6f7a0b5ed8d", "0.000001"),
    ("41b3de4355555553", "333333333.3333332"), ("41b3de4355555554", "333333333.33333325"),
    ("41b3de4355555555", "333333333.3333333"), ("41b3de4355555556", "333333333.3333334"),
    ("41b3de4355555557", "333333333.33333343"),
    ("becbf647612f3696", "-0.0000033333333333333333"),
    ("43143ff3c1cb0959", "1424953923781206.2"),
]


def _double(hexbits: str) -> float:
    return struct.unpack(">d", bytes.fromhex(hexbits))[0]


@pytest.mark.parametrize("hexbits, expected", VECTORS)
def test_appendix_b_numbers(hexbits, expected):
    assert jcs.canonicalize(_double(hexbits)).decode() == expected


@pytest.mark.parametrize("hexbits", ["7fffffffffffffff", "7ff0000000000000", "fff0000000000000"])
def test_nan_and_infinity_are_rejected(hexbits):
    with pytest.raises(jcs.JCSError):
        jcs.canonicalize(_double(hexbits))


def test_section_3_2_2_example():
    # The string is built from code points so no escaping in this file can blur it:
    # EURO SIGN, "$", U+000F, LF, "A", "'", "B", QUOTE, BACKSLASH, BACKSLASH, QUOTE, "/".
    s = "".join(map(chr, [0x20AC, 0x24, 0x0F, 0x0A, 0x41, 0x27, 0x42, 0x22, 0x5C, 0x5C, 0x22, 0x2F]))
    value = {"numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
             "string": s, "literals": [None, True, False]}
    bs, q = chr(0x5C), chr(0x22)
    want_string = q + chr(0x20AC) + "$" + bs + "u000f" + bs + "n" + "A'B" + bs + q + bs + bs + bs + bs + bs + q + "/" + q
    want = ('{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],"string":'
            + want_string + "}")
    assert jcs.canonicalize(value).decode("utf-8") == want


def test_section_3_2_3_key_order_is_utf16():
    obj = {"€": "Euro Sign", "\r": "Carriage Return", "דּ": "Hebrew Letter Dalet With Dagesh",
           "1": "One", "\U0001f600": "Emoji: Grinning Face", "\u0080": "Control", "ö": "Latin Small Letter O With Diaeresis"}
    keys = list(json.loads(jcs.canonicalize(obj)))
    assert keys == ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]
    assert sorted(obj) != keys  # code-point order puts the emoji last


def test_integers_and_the_difference_from_json_dumps():
    assert jcs.canonicalize({"v": 20.0, "w": 20}) == b'{"v":20,"w":20}'
    assert json.dumps(20.0) == "20.0"  # why the contract needs this module
    assert jcs.canonicalize(2 ** 53) == b"9007199254740992"
    assert jcs.sha256_hex({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()


@pytest.mark.parametrize("bad", [2 ** 53 + 1, -(2 ** 53) - 1, "\ud800", {1: "x"}, {"s": {1, 2}}, b"x"])
def test_values_without_a_jcs_form_are_rejected(bad):
    with pytest.raises(jcs.JCSError):
        jcs.canonicalize(bad)
