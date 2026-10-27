"""Reference inline edge: a second, independently written admission check before dispatch.

    python federation/edges/_inline_template/server.py --port 8765

Answers ``POST /decide`` with ``{"verdict": "permit" | "refuse", "reason": ...}``
within the harness's 2 s budget. It receives the exact call and a context
the harness assembles from the day plan and the plant's declared step limit,
and it checks, without consulting REMORA, the reviewer or the plant:

1. the call names a zone the work order covers;
2. the value is one the work order permits for that zone;
3. the step from the current setpoint is within the declared maximum.

Anything else is ``refuse`` with the first reason that failed. It holds no
credential, reaches nothing, and can only add refusals: the harness consults
it after REMORA and the reviewer have approved, and a ``permit`` changes
nothing (runner.admission).

Copy this directory to federation/edges/<your-edge-id>/ and replace
``decide`` with your own check, or keep it as a thin wrapper that calls your
own service. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

EDGE_ID = "inline-template-admission"
TOOL = "set_zone_setpoint"


def decide(payload: dict[str, Any]) -> dict[str, Any]:
    call = payload.get("call") or {}
    ctx = payload.get("context") or {}
    args = call.get("arguments") or {}
    if call.get("tool_name") != TOOL:
        return {"verdict": "refuse", "reason": f"tool_not_admitted:{call.get('tool_name')}"}
    plan = (ctx.get("work_order") or {}).get("zones") or {}
    zone = args.get("zone_id")
    if zone not in plan:
        return {"verdict": "refuse", "reason": "zone_not_in_work_order"}
    value = args.get("setpoint_c")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return {"verdict": "refuse", "reason": "value_not_numeric"}
    if not any(abs(float(value) - float(v)) < 1e-9 for v in plan[zone]):
        return {"verdict": "refuse", "reason": "value_not_in_work_order"}
    cur, max_delta = ctx.get("current_setpoint_c"), ctx.get("max_delta_c")
    if isinstance(cur, (int, float)) and isinstance(max_delta, (int, float)) \
            and abs(float(value) - float(cur)) > float(max_delta) + 1e-9:
        return {"verdict": "refuse", "reason": "step_exceeds_declared_max"}
    return {"verdict": "permit", "reason": "within the work order and the declared step"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw_in = self.rfile.read(n) if n else b""
        if self.path != "/decide":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            payload = json.loads(raw_in.decode("utf-8") or "{}")
            out = decide(payload)
        except (ValueError, UnicodeDecodeError):
            out = {"verdict": "refuse", "reason": "malformed_request"}
        raw = json.dumps({**out, "edge_id": EDGE_ID}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        return


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    return srv


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="inline-template-admission")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args(argv)
    srv = serve(a.host, a.port)
    print(f"{EDGE_ID}: POST http://{a.host}:{a.port}/decide", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
