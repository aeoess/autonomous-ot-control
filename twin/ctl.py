"""Operator CLI for the loopback control listener (run inside the twin container).

  python -m twin.ctl stop --actor operator-1
  python -m twin.ctl release --actor operator-1
  python -m twin.ctl advance 900
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
    a = p.parse_args(argv)
    base = f"http://127.0.0.1:{os.environ.get('TWIN_CONTROL_PORT', '8300')}"
    headers = {"Authorization": f"Bearer {os.environ['TWIN_CONTROL_TOKEN']}"}
    if a.cmd == "advance":
        r = httpx.post(f"{base}/control/v1/clock/advance", json={"seconds": a.seconds}, headers=headers)
    else:
        r = httpx.post(f"{base}/control/v1/stop-switch",
                       json={"engaged": a.cmd == "stop", "actor": a.actor}, headers=headers)
    print(json.dumps(r.json(), indent=2))
    return 0 if r.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
