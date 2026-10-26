"""Run the twin's three listeners in one process.

Environment:
  TWIN_CONFIG, TWIN_SCENARIO           paths to config and scenario JSON
  TWIN_SNAPSHOT_PATH                   optional durable snapshot (resume after restart)
  TWIN_READ_BIND / TWIN_READ_PORT      read listener   (default 0.0.0.0:8100)
  TWIN_ACTUATOR_BIND / _PORT           actuator listener (default 127.0.0.1:8200)
  TWIN_CONTROL_BIND / _PORT            control listener  (default 127.0.0.1:8300)
  TWIN_AUTOPLAY_SPEEDUP                optional: advance sim time this many x real time
  TWIN_READ_TOKEN, TWIN_ACTUATOR_TOKEN, TWIN_CONTROL_TOKEN   distinct, >= 24 chars

The actuator listener must be bound to an address reachable only from the
protected dispatcher network (compose uses a fixed plant-private address).
Startup fails closed on missing/duplicate tokens or a wildcard actuator bind
unless TWIN_ALLOW_WILDCARD_ACTUATOR=1 is set explicitly for tests.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import uvicorn

from twin.api import Tokens, build_actuator_app, build_control_app, build_read_app
from twin.model import Twin, TwinError, load_config
from twin.scenarios import load_scenario

log = logging.getLogger("twin")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


async def _autoplay(twin: Twin, speedup: float) -> None:
    interval = twin.step_s / speedup
    while not twin.ended:
        await asyncio.sleep(interval)
        try:
            twin.advance(twin.step_s)
        except TwinError as e:
            log.info("autoplay stopped: %s", e)
            return


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    tokens = Tokens.from_env()
    act_bind = _env("TWIN_ACTUATOR_BIND", "127.0.0.1")
    if act_bind in ("0.0.0.0", "::") and os.environ.get("TWIN_ALLOW_WILDCARD_ACTUATOR") != "1":
        print("refusing: actuator listener must not bind a wildcard address", file=sys.stderr)
        return 2
    twin = Twin(load_config(_env("TWIN_CONFIG", "twin/fixtures/three-zone-v1.json")),
                load_scenario(_env("TWIN_SCENARIO", "scenarios/baseline-day-v1.json")),
                snapshot_path=os.environ.get("TWIN_SNAPSHOT_PATH") or None)
    servers = [
        uvicorn.Server(uvicorn.Config(build_read_app(twin, tokens), host=_env("TWIN_READ_BIND", "0.0.0.0"),
                                      port=int(_env("TWIN_READ_PORT", "8100")), log_level="info")),
        uvicorn.Server(uvicorn.Config(build_actuator_app(twin, tokens), host=act_bind,
                                      port=int(_env("TWIN_ACTUATOR_PORT", "8200")), log_level="info")),
        uvicorn.Server(uvicorn.Config(build_control_app(twin, tokens),
                                      host=_env("TWIN_CONTROL_BIND", "127.0.0.1"),
                                      port=int(_env("TWIN_CONTROL_PORT", "8300")), log_level="info")),
    ]
    tasks = [s.serve() for s in servers]
    speed = os.environ.get("TWIN_AUTOPLAY_SPEEDUP", "").strip()
    if speed:
        tasks.append(_autoplay(twin, float(speed)))
    await asyncio.gather(*tasks)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
