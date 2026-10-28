"""Stand-in delegation verifier for ot-test-delegation/v1 (NOT APS).

    python adapter.py run --fixtures <set dir> --out <result.json>

For each case it checks only the delegation side of the authority-fixture
contract, in this order, and answers ``pass`` or ``refuse`` with the first
reason found:

1. ``unknown_format``: not an ot-test-delegation/v1 object;
2. ``signature_invalid`` / ``untrusted_root`` / ``broken_chain``: every link
   is signed by its delegator over JCS, the first by a trusted root key, and
   each link's delegator is the previous link's delegate;
3. ``widened``: a link grants more than the link before it (building, or any
   asset, parameter or operation outside its parent's lists);
4. ``not_yet_valid`` / ``expired``: every link is valid at the call's
   ``decision_time`` (``valid_from`` inclusive, ``valid_until`` exclusive);
5. ``out_of_scope``: the call's building, asset, parameter and operation are
   inside the last link's scope, by exact string equality.

It never looks at the value or the work order: the value range is the
testbed's check. APS's verifier replaces this file.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the testbed's RFC 8785 JCS (runner.jcs)

from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402

from runner import jcs  # noqa: E402

FORMAT = "ot-test-delegation/v1"
LISTS = (("asset", "assets"), ("parameter", "parameters"), ("operation", "operations"))


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _signed(link: dict, keys: dict[str, str]) -> bool:
    pub = keys.get(link.get("delegator"))
    if pub is None or not isinstance(link.get("signature"), str):
        return False
    body = {k: v for k, v in link.items() if k != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(_b64(pub)).verify(_b64(link["signature"]), jcs.canonicalize(body))
    except (InvalidSignature, ValueError):
        return False
    return True


def _within(child: dict, parent: dict) -> bool:
    return child.get("building_id") == parent.get("building_id") and all(
        set(child.get(k) or []) <= set(parent.get(k) or []) for _, k in LISTS)


def decide(case: dict, roots: dict[str, str], keys: dict[str, str]) -> tuple[str, str]:
    d, call = case.get("delegation"), case["call"]
    if not isinstance(d, dict) or d.get("schema") != FORMAT or not d.get("chain"):
        return "refuse", "unknown_format"
    chain = d["chain"]
    for i, link in enumerate(chain):
        if not _signed(link, keys):
            return "refuse", "signature_invalid"
        if i == 0 and link["delegator"] not in roots:
            return "refuse", "untrusted_root"
        if i > 0 and link["delegator"] != chain[i - 1]["delegate"]:
            return "refuse", "broken_chain"
        if i > 0 and not _within(link["scope"], chain[i - 1]["scope"]):
            return "refuse", "widened"
    t = call["decision_time"]  # UTC "YYYY-MM-DDTHH:MM:SSZ": string order is time order
    for link in chain:
        if t < link["valid_from"]:
            return "refuse", "not_yet_valid"
        if t >= link["valid_until"]:
            return "refuse", "expired"
    leaf = chain[-1]["scope"]
    if call.get("building_id") != leaf.get("building_id") or any(call.get(f) not in (leaf.get(k) or []) for f, k in LISTS):
        return "refuse", "out_of_scope"
    return "pass", "valid chain; the call is inside the granted scope"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="authority-template-verifier")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--fixtures", required=True)
    r.add_argument("--out", required=True)
    a = p.parse_args(argv)
    fx = Path(a.fixtures)
    m = json.loads((fx / "manifest.json").read_text("utf-8"))
    roots, keys = m.get("delegation_root_keys") or {}, m.get("delegation_signer_keys") or {}
    verdicts = []
    for path in sorted((fx / "cases").glob("*.json")):
        case = json.loads(path.read_text("utf-8"))
        v, reason = decide(case, roots, keys)
        verdicts.append({"case_id": case["case_id"], "verdict": v, "reason": reason})
    Path(a.out).write_text(json.dumps({"edge_id": "authority-template-verifier", "verdicts": verdicts}, indent=2) + "\n",
                           "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
