"""Authority-fixture sets: the five cases of issue #27, built and sealed.

    python -m runner.authority_fixtures build --out federation/authority/fixtures/t1

Each case carries the normalized call, the testbed's signed work order and a
delegation, and the manifest fixes the expected outcome of both checks before
any verifier runs (docs/federation/authority-fixture-contract.md):

| # | Case | APS | Work order | Admitted |
|---|---|---|---|---|
| 1 | valid chain, in scope, value in range | pass | pass | yes |
| 2 | a child widens its parent | refuse | pass | no |
| 3 | expired at the decision time | refuse | pass | no |
| 4 | asset outside the grant | refuse | pass | no |
| 5 | valid, in scope, value outside the range | pass | refuse | no |

The delegation format here, ``ot-test-delegation/v1``, is the testbed's
**stand-in**, not APS's: a chain of links, each signed by its delegator with
a test-only Ed25519 key over JCS. It exists so the harness runs end to end
before APS's fixtures arrive; APS's own delegations and verifier replace it
(the work orders bind whatever delegation object they are given by its JCS
SHA-256). The ground truth of every case comes from how it was built.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from runner import jcs
from runner import work_order as wo

SET_SCHEMA = "ot-authority-fixtures/v1"
CASE_SCHEMA = "ot-authority-case/v1"
DELEGATION_SCHEMA = "ot-test-delegation/v1"
BUILDING = "demo-building-001"
ROOT_KEY = "delegation-root-test-key"
OPERATOR_KEY = "operator-test-key"
DAY = "2026-01-15"
DECISION = f"{DAY}T07:00:00Z"
FULL_SCOPE = {"building_id": BUILDING, "assets": ["ZONE-01", "ZONE-02", "ZONE-03"],
              "parameters": ["setpoint_c"], "operations": ["set_zone_setpoint"]}
CASES = (
    ("af-1-valid", "valid chain, in scope, value in range", None, "pass", "pass"),
    ("af-2-widened", "the operator holds ZONE-01 and ZONE-02 but grants the agent ZONE-01 and ZONE-03",
     "widened", "refuse", "pass"),
    ("af-3-expired", "the agent's link expired at 06:00, before the decision time 07:00", "expired", "refuse", "pass"),
    ("af-4-out-of-scope", "a valid, narrowed chain grants the agent ZONE-02 only; the call is on ZONE-01",
     "out_of_scope", "refuse", "pass"),
    ("af-5-value-out-of-range", "valid chain and scope; the value 22.5 is outside the work order's 19.0..21.0",
     None, "pass", "refuse"),
)


def _link(delegator: str, delegate: str, scope: dict[str, Any], valid_from: str, valid_until: str) -> dict[str, Any]:
    link = {"delegator": delegator, "delegate": delegate, "scope": scope,
            "valid_from": valid_from, "valid_until": valid_until}
    link["signature"] = wo.b64url(wo.issuer_test_key(delegator).sign(jcs.canonicalize(link)))
    return link


def delegation(case_id: str, fault: str | None) -> dict[str, Any]:
    """A two-link chain: root -> operator (ZONE-01, ZONE-02), operator -> agent (ZONE-01 unless the fault says)."""
    op_scope = {**FULL_SCOPE, "assets": ["ZONE-01", "ZONE-02"]}
    agent_scope = {**op_scope, "assets": ["ZONE-01"]}
    agent_until = f"{DAY}T18:00:00Z"
    if fault == "widened":
        agent_scope["assets"] = ["ZONE-01", "ZONE-03"]
    elif fault == "expired":
        agent_until = f"{DAY}T06:00:00Z"
    elif fault == "out_of_scope":
        agent_scope["assets"] = ["ZONE-02"]
    return {"schema": DELEGATION_SCHEMA, "delegation_id": f"DLG-{case_id}",
            "chain": [_link(ROOT_KEY, OPERATOR_KEY, op_scope, f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z"),
                      _link(OPERATOR_KEY, "agent", agent_scope, f"{DAY}T00:00:00Z", agent_until)]}


def build_case(n: int) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id, construction, fault, aps, wo_expected = CASES[n]
    value = 22.5 if case_id.startswith("af-5") else 20.5
    call = wo.normalized_call({"tool_name": "set_zone_setpoint", "arguments": {"zone_id": "ZONE-01", "setpoint_c": value}},
                              building_id=BUILDING, decision_time_utc=DECISION)
    dlg = delegation(case_id, fault)
    order = wo.sign({"schema": wo.SCHEMA, "work_order_id": f"WO-{case_id}", "building_id": BUILDING,
                     "asset": "ZONE-01", "parameter": "setpoint_c", "operation": "set_zone_setpoint",
                     "value_range": {"min": 19.0, "max": 21.0, "unit": "degC"},
                     "valid_from": f"{DAY}T00:00:00Z", "valid_until": f"{DAY}T23:59:59Z",
                     "delegation_id": dlg["delegation_id"], "delegation_sha256": wo.delegation_sha256(dlg)},
                    wo.issuer_test_key(), wo.TEST_ISSUER)
    case = {"schema": CASE_SCHEMA, "case_id": case_id, "construction": construction,
            "call": call, "work_order": order, "delegation": dlg}
    entry = {"case_id": case_id, "construction": construction, "delegation_fault": fault,
             "expected": {"aps": aps, "work_order": wo_expected, "admitted": aps == "pass" and wo_expected == "pass"}}
    return case, entry


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_digest(files: list[dict[str, str]]) -> str:
    text = "".join(f"{f['path']} {f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(out: Path, set_id: str = "t1") -> dict[str, Any]:
    if out.exists():
        shutil.rmtree(out)
    (out / "cases").mkdir(parents=True)
    entries = []
    for n in range(len(CASES)):
        case, entry = build_case(n)
        (out / "cases" / f"{case['case_id']}.json").write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", "utf-8")
        entries.append(entry)
    files = [{"path": f"cases/{p.name}", "sha256": _sha(p)} for p in sorted((out / "cases").glob("*.json"))]
    manifest = {
        "schema": SET_SCHEMA, "fixture_set": set_id,
        "contract": "docs/federation/authority-fixture-contract.md",
        "scope": "The five cases of issue #27 on the simulated building. Synthetic; test-only keys; no physical control.",
        "delegation_format": f"{DELEGATION_SCHEMA}: the testbed's stand-in, not APS's format. APS's delegations "
                             "and verifier replace it; the work orders bind any delegation by its JCS SHA-256.",
        "work_order_issuer_keys": wo.issuer_test_keys(),
        "delegation_root_keys": wo.issuer_test_keys(ROOT_KEY),
        "delegation_signer_keys": wo.issuer_test_keys(ROOT_KEY, OPERATOR_KEY),
        "test_keys_note": "Every private key here is derived from a public label (runner.work_order.issuer_test_key): "
                          "anyone can sign with it. Never trust these keys anywhere else.",
        "cases": entries, "files": files, "package_digest": package_digest(files),
        "package_digest_rule": "sha256 over one line per files entry, '<path> <sha256>\\n', sorted by path",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")
    return manifest


def check(set_dir: Path) -> list[str]:
    """Problems with a sealed set: missing or changed files, a changed package digest, an unknown case."""
    m = json.loads((set_dir / "manifest.json").read_text("utf-8"))
    problems = []
    if m.get("schema") != SET_SCHEMA:
        problems.append(f"manifest schema is {m.get('schema')!r}")
    for f in m.get("files", []):
        p = set_dir / f["path"]
        if not p.is_file():
            problems.append(f"missing {f['path']}")
        elif _sha(p) != f["sha256"]:
            problems.append(f"changed {f['path']}")
    if package_digest(m.get("files", [])) != m.get("package_digest"):
        problems.append("package digest does not match the files list")
    listed = {f"cases/{c['case_id']}.json" for c in m.get("cases", [])}
    present = {f"cases/{p.name}" for p in (set_dir / "cases").glob("*.json")}
    problems += [f"unlisted {p}" for p in sorted(present - listed)]
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.authority_fixtures")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    b.add_argument("--set-id", default="t1")
    c = sub.add_parser("check")
    c.add_argument("set_dir")
    a = p.parse_args(argv)
    if a.cmd == "build":
        m = build(Path(a.out), a.set_id)
        print(f"{a.out}: {len(m['cases'])} cases, {m['package_digest']}")
        return 0
    problems = check(Path(a.set_dir))
    print("\n".join(problems) or f"{a.set_dir}: sealed, unchanged")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
