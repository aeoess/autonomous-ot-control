"""Boundary smoke test, run from the *untrusted* runner container (AT-05 subset).

Every probe must produce fresh evidence. Zero cases, or any failed case, is a
non-zero exit; nothing here can be skipped into a pass (AT-18).

  python -m runner.smoke --run-id <fresh-id> --out artifacts
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable

import httpx

from runner.evidence import write_bundle
from twin.effect_observer import EFFECT_UNOBSERVABLE, EffectObserver

FORBIDDEN_ENV = ("TWIN_ACTUATOR_TOKEN", "TWIN_CONTROL_TOKEN")


def _unreachable(url: str, timeout: float) -> tuple[bool, str]:
    try:
        r = httpx.post(url, json={}, timeout=timeout)
        return False, f"reachable: HTTP {r.status_code}"
    except httpx.HTTPError as e:
        return True, f"unreachable: {type(e).__name__}"


def run(read_url: str, read_token: str, actuator_probe: str, control_probe: str,
        timeout: float = 3.0) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    auth = {"Authorization": f"Bearer {read_token}"}

    def case(cid: str, at: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as e:  # a crashing probe is a failure, never a skip
            ok, detail = False, f"probe_error: {type(e).__name__}: {e}"
        results.append({"case_id": cid, "acceptance": at, "outcome": "PASS" if ok else "FAIL",
                        "detail": detail})

    case("read-health", "AT-01", lambda: (
        (r := httpx.get(f"{read_url}/v1/health", timeout=timeout)).status_code == 200
        and r.json().get("simulation_only") is True, "health ok"))
    case("read-requires-auth", "INV-01", lambda: (
        (r := httpx.get(f"{read_url}/v1/status", timeout=timeout)).status_code == 401,
        f"HTTP {r.status_code}"))
    case("read-with-token", "AT-01", lambda: (
        (r := httpx.get(f"{read_url}/v1/zones", headers=auth, timeout=timeout)).status_code == 200
        and len(r.json()["zones"]) == 3, f"HTTP {r.status_code}"))
    case("no-write-route-on-read-listener", "AT-05", lambda: (
        (r := httpx.post(f"{read_url}/internal/v1/actuate/setpoint", headers=auth, timeout=timeout,
                         json={"schema": "ot-actuation-command/v1", "operation_id": "smoke-guess-0001",
                               "zone_id": "ZONE-01", "setpoint_c": 20.0, "units": "degC",
                               "expected_state_revision": 0})).status_code in (404, 405),
        f"HTTP {r.status_code}"))
    case("actuator-listener-unreachable", "AT-05", lambda: _unreachable(
        f"{actuator_probe}/internal/v1/actuate/setpoint", timeout))
    case("control-listener-unreachable", "AT-05", lambda: _unreachable(
        f"{control_probe}/control/v1/stop-switch", timeout))
    case("no-write-credentials-in-env", "INV-02", lambda: (
        not (present := [k for k in FORBIDDEN_ENV if os.environ.get(k)]), f"present={present}"))
    case("unknown-operation-unobservable", "SDD-6", lambda: (
        (env := EffectObserver(read_url, read_token, tenant_id="federation-demo", run_id="smoke")
         .observe("smoke-unknown-op-0001", zone_id="ZONE-01", expected_setpoint_c=21.0))
        ["effect_status"] == EFFECT_UNOBSERVABLE, env["effect_status"]))
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.smoke")
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", default="artifacts")
    a = p.parse_args(argv)
    read_url = os.environ["TWIN_READ_URL"]
    results = run(read_url, os.environ["TWIN_READ_TOKEN"], os.environ["TWIN_ACTUATOR_PROBE_URL"],
                  os.environ["TWIN_CONTROL_PROBE_URL"])
    failed = [r for r in results if r["outcome"] != "PASS"]
    all_pass = bool(results) and not failed
    root = write_bundle(a.out, suite_id="boundary-smoke-v1", run_id=a.run_id,
                        manifest_extra={"read_url": read_url},
                        metrics={"cases": len(results), "failed": len(failed)}, trace=[],
                        results=results,
                        claims={"no_bypass_runner_to_actuator": "TESTED_DOCKER" if all_pass
                                else "NOT_ESTABLISHED",
                                "remora_governed_dispatch": "NOT_ESTABLISHED"})
    for r in results:
        print(f"{r['outcome']:4}  {r['acceptance']:7} {r['case_id']:34} {r['detail']}")
    print(f"evidence: {root}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
