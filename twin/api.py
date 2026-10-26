"""HTTP surfaces of the twin, one FastAPI app per trust domain.

* ``read_app``     — measurements, energy, status and the operation log.
                     Read credential only. Has **no** write routes mounted.
* ``actuator_app`` — ``POST /internal/v1/actuate/setpoint``. Actuator
                     credential, held only by the protected REMORA dispatcher.
* ``control_app``  — simulation clock and operator stop switch. Bound to
                     loopback by default; never exposed to agent networks.

Each app runs on its own listener (see ``twin.server``). Network placement and
authentication enforce INV-01 independently: a request reaching the wrong
listener finds no route, and a wrong token is refused on every route.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, Strict

from twin.model import OperationConflict, Twin, TwinError

TOKEN_ENV = {
    "read": "TWIN_READ_TOKEN",
    "actuator": "TWIN_ACTUATOR_TOKEN",
    "control": "TWIN_CONTROL_TOKEN",
}
_MIN_TOKEN_LEN = 24
_OP_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{7,127}$"


class TokenConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tokens:
    read: str
    actuator: str
    control: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Tokens":
        env = dict(os.environ) if env is None else env
        vals = {}
        for role, var in TOKEN_ENV.items():
            v = env.get(var, "").strip()
            if len(v) < _MIN_TOKEN_LEN:
                raise TokenConfigError(f"{var} missing or shorter than {_MIN_TOKEN_LEN} chars")
            if v.lower().startswith(("changeme", "change-me", "placeholder")):
                raise TokenConfigError(f"{var} is a placeholder value")
            vals[role] = v
        if len(set(vals.values())) != len(vals):
            raise TokenConfigError("read, actuator and control tokens must all differ")
        return cls(**vals)


def _bearer(expected: str) -> Callable[[Request], None]:
    def dep(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        scheme, _, presented = auth.partition(" ")
        if scheme.lower() != "bearer" or not presented or \
                not hmac.compare_digest(presented.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="unauthorized",
                                headers={"WWW-Authenticate": "Bearer"})
    return dep


class SetpointCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=True)

    schema_: str = Field(alias="schema", pattern=r"^ot-actuation-command/v1$")
    operation_id: str = Field(pattern=_OP_ID_PATTERN)
    zone_id: str = Field(min_length=1, max_length=64)
    setpoint_c: Annotated[float, Strict()]
    units: str = Field(min_length=1, max_length=16)
    expected_state_revision: Annotated[int, Strict()] = Field(ge=0)


class AdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: int = Field(gt=0)


class StopSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engaged: bool
    actor: str = Field(min_length=1, max_length=64)


def _health(twin: Twin) -> dict[str, Any]:
    return {"status": "ok", "simulation_only": True, "sim_time_s": twin.clock.now_s}


def build_read_app(twin: Twin, tokens: Tokens) -> FastAPI:
    app = FastAPI(title="twin-read", docs_url=None, redoc_url=None, openapi_url=None)
    auth = Depends(_bearer(tokens.read))

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return _health(twin)

    @app.get("/v1/status", dependencies=[auth])
    def status() -> dict[str, Any]:
        return twin.status()

    @app.get("/v1/zones", dependencies=[auth])
    def zones() -> dict[str, Any]:
        return {"zones": [twin.read_zone(z).to_dict() for z in sorted(twin.zones)]}

    @app.get("/v1/zones/{zone_id}", dependencies=[auth])
    def zone(zone_id: str) -> dict[str, Any]:
        try:
            return twin.read_zone(zone_id).to_dict()
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown_zone") from None

    @app.get("/v1/energy", dependencies=[auth])
    def energy() -> dict[str, Any]:
        return twin.read_energy()

    @app.get("/v1/operations/{operation_id}", dependencies=[auth])
    def operation(operation_id: str) -> dict[str, Any]:
        rec = twin.operation(operation_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown_operation")
        return rec.to_dict()

    return app


def build_actuator_app(twin: Twin, tokens: Tokens) -> FastAPI:
    app = FastAPI(title="twin-actuator", docs_url=None, redoc_url=None, openapi_url=None)
    auth = Depends(_bearer(tokens.actuator))

    @app.get("/internal/v1/health")
    def health() -> dict[str, Any]:
        return _health(twin)

    @app.post("/internal/v1/actuate/setpoint", dependencies=[auth])
    def actuate(cmd: SetpointCommand) -> JSONResponse:
        body = cmd.model_dump(by_alias=True)
        try:
            rec, replayed = twin.apply_setpoint(body)
        except OperationConflict:
            return JSONResponse(status_code=409, content={
                "status": "REJECTED", "reason": "operation_id_reused", "replayed": False,
                "operation_id": cmd.operation_id})
        except TwinError as e:
            return JSONResponse(status_code=409, content={"status": "REJECTED", "reason": str(e)})
        out = {**rec.to_dict(), "replayed": replayed}
        return JSONResponse(status_code=200 if rec.status == "APPLIED" else 409, content=out)

    return app


def build_control_app(twin: Twin, tokens: Tokens) -> FastAPI:
    app = FastAPI(title="twin-control", docs_url=None, redoc_url=None, openapi_url=None)
    auth = Depends(_bearer(tokens.control))

    @app.post("/control/v1/clock/advance", dependencies=[auth])
    def advance(req: AdvanceRequest) -> dict[str, Any]:
        try:
            twin.advance(req.seconds)
        except TwinError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return twin.status()

    @app.post("/control/v1/stop-switch", dependencies=[auth])
    def stop_switch(req: StopSwitchRequest) -> dict[str, Any]:
        return twin.set_stop_switch(req.engaged, req.actor)

    @app.get("/control/v1/snapshot", dependencies=[auth])
    def snapshot() -> dict[str, Any]:
        return twin.snapshot()

    return app
