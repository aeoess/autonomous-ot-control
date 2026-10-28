"""Operator CLI for the loopback control listener (run inside the twin container).

  python -m twin.ctl stop --actor operator-1
  python -m twin.ctl release --actor operator-1
  python -m twin.ctl advance 900
  python -m twin.ctl fault sensor_freeze --zone ZONE-01 --actor acceptance
  python -m twin.ctl fault reading_unit --zone ZONE-01 --value degF --actor acceptance
  python -m twin.ctl fault ack_delay --value 8 --actor acceptance
  python -m twin.ctl clear-faults --actor acceptance
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="twin.ctl")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("stop", "release"):
        sp = sub.add_parser(name)
        sp.add_argument("--actor", required=True)
    adv = sub.add_parser("advance")
    adv.add_argument("seconds", type=int)
    flt = sub.add_parser("fault")
    flt.add_argument("kind", choices=("sensor_freeze", "reading_unit", "reading_tfm", "ack_delay"))
    flt.add_argument("--zone")
    flt.add_argument("--value")
    flt.add_argument("--actor", required=True)
    clr = sub.add_parser("clear-faults")
    clr.add_argument("--actor", required=True)
    a = p.parse_args(argv)
    base = f"http://127.0.0.1:{os.environ.get('TWIN_CONTROL_PORT', '8300')}"
    headers = {"Authorization": f"Bearer {os.environ['TWIN_CONTROL_TOKEN']}"}
    if a.cmd == "advance":
        r = httpx.post(f"{base}/control/v1/clock/advance", json={"seconds": a.seconds}, headers=headers)
    elif a.cmd == "fault":
        value: str | float | None = float(a.value) if a.kind == "ack_delay" and a.value else a.value
        r = httpx.post(f"{base}/control/v1/faults", headers=headers,
                       json={"kind": a.kind, "zone_id": a.zone, "value": value, "actor": a.actor})
    elif a.cmd == "clear-faults":
        r = httpx.post(f"{base}/control/v1/faults/clear", json={"engaged": False, "actor": a.actor}, headers=headers)
    else:
        r = httpx.post(f"{base}/control/v1/stop-switch",
                       json={"engaged": a.cmd == "stop", "actor": a.actor}, headers=headers)
    print(json.dumps(r.json(), indent=2))
    return 0 if r.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
