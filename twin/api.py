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
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, Strict, StrictBool, StrictInt, StrictStr, model_validator

from twin.model import OperationConflict, StateStoreFailed, Twin, TwinError

TOKEN_ENV = {
    "read": "TWIN_READ_TOKEN",
    "actuator": "TWIN_ACTUATOR_TOKEN",
    "control": "TWIN_CONTROL_TOKEN",
}
_MIN_TOKEN_LEN = 24
_OP_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{7,127}$"
#: SR-15 (F-17): the largest request body any listener reads. A command is a few hundred bytes.
MAX_BODY_BYTES = 16 * 1024


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


class _Gate:
    """SR-15 (F-17): authenticate before the body is read, and never read more than ``MAX_BODY_BYTES``.

    FastAPI parses the body before it runs a route's dependencies, so a malformed or oversized body from
    an unauthenticated caller would be read and answered 422. The gate runs first: on this app's own
    path prefix a request without the right bearer token gets 401 with its body unread; a body over
    the cap gets 413. Paths outside the prefix and the open paths fall through, so a request on the
    wrong listener still finds no route. The route dependency ``_bearer`` stays as a second check.
    """

    def __init__(self, app: Any, token: str, prefix: str, open_paths: tuple[str, ...] = ()) -> None:
        self.app, self.token, self.prefix, self.open_paths = app, token.encode(), prefix, open_paths

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        if path.startswith(self.prefix) and path not in self.open_paths:
            scheme, _, presented = headers.get(b"authorization", b"").partition(b" ")
            if scheme.lower() != b"bearer" or not presented or not hmac.compare_digest(presented, self.token):
                return await _reply(send, 401, "unauthorized", [(b"www-authenticate", b"Bearer")])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > MAX_BODY_BYTES
            except ValueError:
                return await _reply(send, 400, "invalid_content_length")
            if too_big:
                return await _reply(send, 413, "body_too_large")
        body, more = b"", True
        while more:  # chunked bodies are counted too
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
            if len(body) > MAX_BODY_BYTES:
                return await _reply(send, 413, "body_too_large")
        sent = False

        async def replay() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await self.app(scope, replay, send)


async def _reply(send: Any, status: int, detail: str, extra: list[tuple[bytes, bytes]] | None = None) -> None:
    data = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode()), *(extra or [])]})
    await send({"type": "http.response.body", "body": data})


async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
    """SR-15 (F-17): a 422 names the fields, never echoes what was sent: an echoed NaN cannot be encoded
    as JSON (a 500 instead of a 422), and an echoed body would hand its size back to the caller."""
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": [str(p) for p in e.get("loc", ())][:8], "type": str(e.get("type", ""))[:64]}
        for e in exc.errors()[:16]]})


def _app(title: str, token: str, prefix: str, open_paths: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_exception_handler(RequestValidationError, _invalid)
    app.add_middleware(_Gate, token=token, prefix=prefix, open_paths=open_paths)
    return app


_Name = Annotated[StrictStr, Field(min_length=1, max_length=64)]
_Number = Annotated[float, Strict()]  # an int or a float, never a string; NaN and the infinities refused


class _Args(BaseModel):
    """SR-15: each plant tool's arguments, typed. Shape only: bounds, steps and interlocks stay in
    ``twin.guard``, which answers them with a REJECTED record."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _ZoneCooling(_Args):
    zone_id: _Name
    cooling_c: _Number


class _ZoneCo2(_Args):
    zone_id: _Name
    co2_ppm: StrictInt


class _ZoneLux(_Args):
    zone_id: _Name
    lux: StrictInt


class _ZoneLightingMode(_Args):
    zone_id: _Name
    mode: _Name
    daylight_harvesting: StrictBool


class _SupplyAir(_Args):
    supply_air_c: _Number


class _Tank(_Args):
    tank_c: _Number


class _HeatPumpLimit(_Args):
    max_fraction: _Number


class _AhuEnabled(_Args):
    enabled: StrictBool


class _CableMode(_Args):
    circuit: _Name
    mode: _Name


PLANT_ARGUMENT_MODELS: dict[str, type[_Args]] = {
    "set_zone_cooling_setpoint": _ZoneCooling,
    "set_zone_co2_setpoint": _ZoneCo2,
    "set_zone_lighting_setpoint": _ZoneLux,
    "set_zone_lighting_mode": _ZoneLightingMode,
    "set_supply_air_setpoint": _SupplyAir,
    "set_tank_setpoint": _Tank,
    "set_heat_pump_limit": _HeatPumpLimit,
    "set_ahu_enabled": _AhuEnabled,
    "set_heating_cable_mode": _CableMode,
}


class SetpointCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_: str = Field(alias="schema", pattern=r"^ot-actuation-command/v1$")
    operation_id: str = Field(pattern=_OP_ID_PATTERN)
    zone_id: str = Field(min_length=1, max_length=64)
    setpoint_c: Annotated[float, Strict()]
    units: str = Field(min_length=1, max_length=16)
    expected_state_revision: Annotated[int, Strict()] = Field(ge=0)


class PlantCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_: str = Field(alias="schema", pattern=r"^ot-plant-command/v1$")
    operation_id: str = Field(pattern=_OP_ID_PATTERN)
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]
    expected_state_revision: Annotated[int, Strict()] = Field(ge=0)

    @model_validator(mode="after")
    def _typed_arguments(self) -> "PlantCommand":
        # Validated against the tool's own model; the arguments are passed on as sent, so the request
        # digest is the one the adapter computed.
        model = PLANT_ARGUMENT_MODELS.get(self.tool_name)
        if model is None:
            raise ValueError("unknown_tool")
        model.model_validate(self.arguments)
        return self


class AdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: int = Field(gt=0)


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: str = Field(pattern=r"^(sensor_freeze|reading_unit|reading_tfm|ack_delay)$")
    zone_id: str | None = Field(default=None, min_length=1, max_length=64)
    value: str | float | None = None
    actor: str = Field(min_length=1, max_length=64)


class StopSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engaged: bool
    actor: str = Field(min_length=1, max_length=64)


def _health(twin: Twin) -> dict[str, Any]:
    return {"status": "ok", "simulation_only": True, "sim_time_s": twin.clock.now_s}


def build_read_app(twin: Twin, tokens: Tokens) -> FastAPI:
    app = _app("twin-read", tokens.read, "/v1/", ("/v1/health",))
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

    @app.get("/v1/plant", dependencies=[auth])
    def plant() -> dict[str, Any]:
        return twin.read_plant()

    @app.get("/v1/operations/{operation_id}", dependencies=[auth])
    def operation(operation_id: str) -> dict[str, Any]:
        rec = twin.operation(operation_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown_operation")
        return rec.to_dict()

    return app


def build_actuator_app(twin: Twin, tokens: Tokens) -> FastAPI:
    app = _app("twin-actuator", tokens.actuator, "/internal/v1/", ("/internal/v1/health",))
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
        except StateStoreFailed as e:  # nothing applied, nothing recorded; the twin is fail-closed (SR-14)
            return JSONResponse(status_code=503, content={"status": "STORE_FAILED", "reason": str(e)})
        except TwinError as e:
            return JSONResponse(status_code=409, content={"status": "REJECTED", "reason": str(e)})
        out = {**rec.to_dict(), "replayed": replayed}
        delay = twin.take_ack_delay()
        if delay:  # AT-12 fault: the write has run; its acknowledgement comes late
            time.sleep(delay)
        return JSONResponse(status_code=200 if rec.status == "APPLIED" else 409, content=out)

    @app.post("/internal/v1/actuate/plant", dependencies=[auth])
    def actuate_plant(cmd: PlantCommand) -> JSONResponse:
        body = cmd.model_dump(by_alias=True)
        try:
            rec, replayed = twin.apply_plant_command(body)
        except OperationConflict:
            return JSONResponse(status_code=409, content={
                "status": "REJECTED", "reason": "operation_id_reused", "replayed": False,
                "operation_id": cmd.operation_id})
        except StateStoreFailed as e:  # nothing applied, nothing recorded; the twin is fail-closed (SR-14)
            return JSONResponse(status_code=503, content={"status": "STORE_FAILED", "reason": str(e)})
        except TwinError as e:
            return JSONResponse(status_code=409, content={"status": "REJECTED", "reason": str(e)})
        out = {**rec.to_dict(), "replayed": replayed}
        delay = twin.take_ack_delay()
        if delay:  # AT-12 fault: the write has run; its acknowledgement comes late
            time.sleep(delay)
        return JSONResponse(status_code=200 if rec.status == "APPLIED" else 409, content=out)

    return app


def build_control_app(twin: Twin, tokens: Tokens) -> FastAPI:
    app = _app("twin-control", tokens.control, "/control/v1/")
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

    @app.post("/control/v1/faults", dependencies=[auth])
    def inject_fault(req: FaultRequest) -> dict[str, Any]:
        try:
            return {**twin.inject_fault(req.kind, req.zone_id, req.value, req.actor), "faults": twin.fault_state()}
        except (TwinError, TypeError, ValueError) as e:
            raise HTTPException(status_code=409, detail=str(e)) from None

    @app.post("/control/v1/faults/clear", dependencies=[auth])
    def clear_faults(req: StopSwitchRequest) -> dict[str, Any]:
        return {**twin.clear_faults(req.actor), "faults": twin.fault_state()}

    @app.get("/control/v1/faults", dependencies=[auth])
    def faults() -> dict[str, Any]:
        return twin.fault_state()

    @app.get("/control/v1/snapshot", dependencies=[auth])
    def snapshot() -> dict[str, Any]:
        return twin.snapshot()

    return app
