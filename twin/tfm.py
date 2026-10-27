"""TFM (Tverrfaglig merkesystem, Statsbygg PA 0802): parse, validate and explain identifiers.

    python -m twin.tfm explain "+DEMO01=360.001-JV401"
    python -m twin.tfm point ZONE-01.AirTemp
    python -m twin.tfm list
    python -m twin.tfm markdown --write     # regenerates docs/tfm.md

An identifier has up to three main elements, each usable alone:

    +AAAAAA   location: the building, given by the client (Statsbygg: 6 digits)
    =NNN.nnn  system: a code from Vedlegg 9.1 (NS 3451:2022) and a running number
    -BBnnn    component occurrence: a code from Vedlegg 9.2 and a running number
    -BBnnnT   component type (parallel identification)

The code lists come from `twin/tfm_register.json`. It is built from Statsbygg's
published PDFs by `scripts/build_tfm_register.py` and records each source's
revision and sha256. Validation is strict:

- the system code must be marked as a TFM system code in Vedlegg 9.1, not a
  group and not "skal ikke benyttes";
- the component code must be assigned in Vedlegg 9.2;
- in HVAC systems (3xx) the running number tells where a component sits:
  401..499 supply or flow, 501..599 return or extract, 601..899 the room,
  901..999 outdoor air.

The map from the twin's data points to TFM objects (`POINT_TFM`) belongs to the
fictitious building only. A real site's identifiers come from its approved
asset inventory (SDD-OT-001 D-08), never from this table.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTER = Path(__file__).with_name("tfm_register.json")
BUILDING = "+DEMO01"

_ID = re.compile(r"^(?:\+(?P<loc>[A-Z0-9]{1,12}))?"
                 r"(?:=(?P<sys>\d{3})(?:\.(?P<run>\d{3}))?)?"
                 r"(?:-(?P<comp>[A-ZÆØÅ]{2}|X_[A-Z])(?P<num>\d{3})(?P<type>T)?)?$")
HVAC_POSITION = ((401, 499, "supply or flow"), (501, 599, "return or extract"),
                 (601, 899, "represents the room"), (901, 999, "represents outdoor air"))


class TfmError(ValueError):
    pass


@dataclass(frozen=True)
class TfmId:
    location: str | None
    system: str | None
    system_run: str | None
    component: str | None
    number: str | None
    is_type: bool

    def __str__(self) -> str:
        s = f"+{self.location}" if self.location else ""
        if self.system:
            s += f"={self.system}" + (f".{self.system_run}" if self.system_run else "")
        if self.component:
            s += f"-{self.component}{self.number}" + ("T" if self.is_type else "")
        return s


@lru_cache(maxsize=1)
def register() -> dict[str, Any]:
    return json.loads(REGISTER.read_text("utf-8"))


@lru_cache(maxsize=1)
def _systems() -> dict[str, dict[str, Any]]:
    return {s["code"]: s for s in register()["systems"]}


@lru_cache(maxsize=1)
def _components() -> dict[str, dict[str, Any]]:
    return {c["code"]: c for c in register()["components"]}


def parse(text: str) -> TfmId:
    """Parse an identifier. The en dash some documents use is read as the hyphen."""
    t = text.strip().replace("–", "-").replace("—", "-").replace(" ", "")
    m = _ID.match(t)
    if not t or not m or not any(m.group(k) for k in ("loc", "sys", "comp")):
        raise TfmError(f"not a TFM identifier: {text!r}")
    return TfmId(location=m["loc"], system=m["sys"], system_run=m["run"], component=m["comp"],
                 number=m["num"], is_type=bool(m["type"]))


def validate(text: str) -> list[str]:
    """Every way the identifier breaks PA 0802 or the code lists; empty when valid."""
    try:
        i = parse(text)
    except TfmError as e:
        return [str(e)]
    errs = []
    if i.system:
        s = _systems().get(i.system)
        if s is None:
            errs.append(f"system code {i.system} is not in Vedlegg 9.1")
        elif s["status"] != "system_code":
            errs.append(f"system code {i.system} ({s['name']}) is {s['status']} in Vedlegg 9.1, not a TFM system code")
        if i.system_run is None:
            errs.append(f"system {i.system} has no running number (=NNN.nnn)")
        elif i.system_run == "000" and not i.is_type:
            errs.append("running number 000 is reserved for component types")
    if i.component:
        c = _components().get(i.component)
        if c is None or c["level"] != 2:
            errs.append(f"component code {i.component} is not in Vedlegg 9.2")
        elif c["status"] != "component_code":
            errs.append(f"component code {i.component} is unassigned in Vedlegg 9.2")
        if i.number == "000":
            errs.append("component running number 000 is not used")
    return errs


def position(i: TfmId) -> str | None:
    """Where an HVAC component sits, from its running number (PA 0802 3.1.3)."""
    if not (i.system and i.system.startswith("3") and i.number) or i.is_type:
        return None
    n = int(i.number)
    return next((lbl for lo, hi, lbl in HVAC_POSITION if lo <= n <= hi), "general")


def explain(text: str) -> dict[str, Any]:
    i = parse(text)
    out: dict[str, Any] = {"id": str(i), "valid": not validate(text), "errors": validate(text)}
    if i.location:
        out["location"] = {"code": i.location, "meaning": "building (client-assigned)"}
    if i.system:
        s = _systems().get(i.system, {})
        grp = _systems().get(i.system[:2], {})
        out["system"] = {"code": i.system, "running_number": i.system_run, "name": s.get("name"),
                         "group": f"{i.system[:2]} {grp.get('name')}", "status": s.get("status")}
    if i.component:
        c = _components().get(i.component, {})
        cls = _components().get(i.component[0], {})
        out["component"] = {"code": i.component, "name": c.get("name"), "examples": c.get("examples"),
                            "class": f"{i.component[0]} {cls.get('name')}", "running_number": i.number,
                            "kind": "type (parallel identification)" if i.is_type else "occurrence",
                            "position": position(i)}
    return out


# -- the fictitious building's TFM objects -----------------------------------------------
#: Each object of the twin and its TFM identifier (occurrence). Built from
#: PA 0802 and the Vedlegg 9.3 examples: room sensors in the bus system
#: (=564.001, running numbers 6xx), the common outdoor sensor on the heating
#: plant (=320.001-RT901), valves on the heating circuit with their actuators in
#: the bus system, and the AHU's components by position (4xx supply, 5xx extract).
_Z = {"ZONE-01": "1", "ZONE-02": "2", "ZONE-03": "3"}
OBJECTS: dict[str, tuple[str, str]] = {
    # outdoor and market
    "OUT-01.temp_sensor": ("=320.001-RT901", "common outdoor temperature sensor (heating plant)"),
    "OUT-01.solar_sensor": ("=564.001-RJ901", "solar sensor"),
    "OUT-01.rh_sensor": ("=564.001-RH901", "outdoor humidity sensor"),
    "OUT-01.rain_sensor": ("=745.001-QH901", "precipitation sensor (regnvakt) at the ramp"),
    "OUT-01.ground_sensor": ("=745.001-QH902", "ground moisture and snow sensor (snøvakt) at the ramp"),
    "BMS.calc": ("=562.001-OF001", "central operations: calculated values (virtual object)"),
    # zones
    **{f"{z}.room_temp": (f"=564.001-RT60{n}", f"room temperature sensor, {z}") for z, n in _Z.items()},
    **{f"{z}.co2": (f"=564.001-RY60{n}", f"CO2 sensor, {z}") for z, n in _Z.items()},
    **{f"{z}.presence": (f"=564.001-RB60{n}", f"presence detector, {z}") for z, n in _Z.items()},
    **{f"{z}.lux": (f"=564.001-RJ60{n}", f"light sensor, {z}") for z, n in _Z.items()},
    **{f"{z}.controller": (f"=564.001-OU60{n}", f"room controller, {z}") for z, n in _Z.items()},
    **{f"{z}.luminaires": (f"=442.001-UP00{n}", f"luminaires, {z}") for z, n in _Z.items()},
    **{f"{z}.dimmer": (f"=442.001-SI00{n}", f"lighting dimmer, {z}") for z, n in _Z.items()},
    **{f"{z}.radiator": (f"=320.002-LH00{n}", f"radiators, {z}") for z, n in _Z.items()},
    **{f"{z}.radiator_valve": (f"=320.002-SB40{n}", f"radiator valve, {z}") for z, n in _Z.items()},
    **{f"{z}.valve_actuator": (f"=564.001-KA10{n}", f"radiator valve actuator, {z}") for z, n in _Z.items()},
    **{f"{z}.cooling_unit": (f"=370.001-LC00{n}", f"fan-coil (cooling), {z}") for z, n in _Z.items()},
    **{f"{z}.vav": (f"=360.001-SQ40{n}", f"supply VAV damper, {z}") for z, n in _Z.items()},
    **{f"{z}.sockets": (f"=433.001-UE10{n}", f"socket circuit (equipment), {z}") for z, n in _Z.items()},
    # heating plant
    "HYD-01.supply_sensor": ("=320.002-RT401", "radiator circuit supply temperature"),
    "HYD-01.controller": ("=320.001-OU001", "heating plant controller (weather curve, summer stop)"),
    "TANK-01.tank": ("=320.001-NT001", "buffer tank"),
    "TANK-01.sensor": ("=320.001-RT402", "buffer tank temperature"),
    "HP-01.unit": ("=356.001-IK001", "heat pump (air to water)"),
    "HP-01.meter": ("=356.001-RE001", "heat pump electric meter"),
    "HP-01.hp_switch": ("=356.001-QP001", "high-pressure switch (pressostat)"),
    "HP-01.backup": ("=320.001-LI401", "backup electric heater in the tank"),
    "DH-01.exchanger": ("=320.001-LV401", "district heating exchanger"),
    "DH-01.meter": ("=320.001-OE001", "district heating energy meter (labelled by the supplier)"),
    "DH-01.supply_sensor": ("=320.001-RT403", "district heating secondary supply temperature"),
    "EB-01.boiler": ("=320.001-IE001", "electric boiler"),
    # air handling
    "AHU-01.controller": ("=360.001-OU001", "AHU controller"),
    "AHU-01.supply_fan": ("=360.001-JV401", "supply fan"),
    "AHU-01.extract_fan": ("=360.001-JV501", "extract fan"),
    "AHU-01.heat_recovery": ("=360.001-LX401", "rotary heat recovery wheel"),
    "AHU-01.bypass_damper": ("=360.001-SS401", "heat-recovery bypass damper"),
    "AHU-01.heating_coil": ("=360.001-LV402", "heating coil"),
    "AHU-01.cooling_coil": ("=360.001-LK401", "cooling coil"),
    "AHU-01.airflow_sensor": ("=360.001-RF401", "supply airflow sensor"),
    "AHU-01.after_hrv_sensor": ("=360.001-RT402", "temperature after heat recovery"),
    "AHU-01.supply_sensor": ("=360.001-RT401", "supply air temperature"),
    "AHU-01.extract_sensor": ("=360.001-RT501", "extract air temperature"),
    # cooling
    "CH-01.unit": ("=370.001-IK001", "chiller"),
    "CH-01.meter": ("=370.001-RE001", "chiller electric meter"),
    # heating cables
    "CABLE-RAMP.cable": ("=745.001-LZ001", "snow-melt cable, ramp (outdoor electric heating)"),
    "CABLE-RAMP.controller": ("=745.001-OU001", "snow-melt controller, ramp"),
    "CABLE-GUTTER.cable": ("=450.001-LZ001", "gutter heating cable (on the building)"),
    "CABLE-GUTTER.controller": ("=450.001-OU001", "gutter heating controller"),
    # energy
    "EM-01.main_meter": ("=432.001-RE001", "main electricity meter (main distribution)"),
}

#: Each data point -> (object, role). Roles: measured (a sensor's value),
#: setpoint (written to a controller), state (an equipment status or output),
#: calculated (computed in central operations), input (a simulation input with
#: no building object).
_POINT_OBJECT: dict[str, tuple[str | None, str]] = {
    "OUT-01.Temp": ("OUT-01.temp_sensor", "measured"),
    "OUT-01.Solar": ("OUT-01.solar_sensor", "measured"),
    "OUT-01.RH": ("OUT-01.rh_sensor", "measured"),
    "OUT-01.Precipitation": ("OUT-01.rain_sensor", "measured"),
    "OUT-01.SurfaceWet": ("OUT-01.ground_sensor", "measured"),
    "OUT-01.IcingRisk": ("BMS.calc", "calculated"),
    "OUT-01.Temp24hMean": ("BMS.calc", "calculated"),
    "GRID-01.SpotPrice": (None, "input"),
    "EM-01.Cost": ("BMS.calc", "calculated"),
    "EM-01.MonthlyPeakKw": ("BMS.calc", "calculated"),
    "EM-01.GridCost": ("BMS.calc", "calculated"),
    "EM-01.ElectricPower": ("EM-01.main_meter", "measured"),
    "EM-01.DistrictHeatPower": ("DH-01.meter", "measured"),
    "HYD-01.SupplyTemp": ("HYD-01.supply_sensor", "measured"),
    "HYD-01.HeatingSeason": ("HYD-01.controller", "state"),
    "HYD-01.HeatDemand": ("HYD-01.controller", "state"),
    "TANK-01.Temp": ("TANK-01.sensor", "measured"),
    "TANK-01.Setpoint": ("HYD-01.controller", "setpoint"),
    "TANK-01.Loss": ("TANK-01.tank", "state"),
    "HP-01.Running": ("HP-01.unit", "state"),
    "HP-01.COP": ("BMS.calc", "calculated"),
    "HP-01.HeatOutput": ("HP-01.unit", "state"),
    "HP-01.ElectricPower": ("HP-01.meter", "measured"),
    "HP-01.Starts": ("HP-01.unit", "state"),
    "HP-01.Defrost": ("HP-01.unit", "state"),
    "HP-01.HighPressureCutout": ("HP-01.hp_switch", "state"),
    "HP-01.BackupHeaterPower": ("HP-01.backup", "state"),
    "HP-01.MaxFraction": ("HP-01.unit", "setpoint"),
    "DH-01.HeatPower": ("DH-01.meter", "measured"),
    "DH-01.SupplyTemp": ("DH-01.supply_sensor", "measured"),
    "EB-01.ElectricPower": ("EB-01.boiler", "state"),
    "AHU-01.Enabled": ("AHU-01.controller", "setpoint"),
    "AHU-01.SupplyAirSetpoint": ("AHU-01.controller", "setpoint"),
    "AHU-01.Airflow": ("AHU-01.airflow_sensor", "measured"),
    "AHU-01.FanPower": ("AHU-01.supply_fan", "state"),
    "AHU-01.HRV_Efficiency": ("AHU-01.heat_recovery", "state"),
    "AHU-01.HRV_Bypass": ("AHU-01.bypass_damper", "state"),
    "AHU-01.HRV_AfterTemp": ("AHU-01.after_hrv_sensor", "measured"),
    "AHU-01.ExtractAirTemp": ("AHU-01.extract_sensor", "measured"),
    "AHU-01.SupplyAirTemp": ("AHU-01.supply_sensor", "measured"),
    "AHU-01.HeatingCoilPower": ("AHU-01.heating_coil", "state"),
    "AHU-01.CoolingCoilPower": ("AHU-01.cooling_coil", "state"),
    "CH-01.CoolingLoad": ("CH-01.unit", "state"),
    "CH-01.EER": ("BMS.calc", "calculated"),
    "CH-01.ElectricPower": ("CH-01.meter", "measured"),
}
for _c, _p in (("CABLE-RAMP", "CABLE-RAMP"), ("CABLE-GUTTER", "CABLE-GUTTER")):
    _POINT_OBJECT.update({f"{_c}.Mode": (f"{_p}.controller", "setpoint"), f"{_c}.Power": (f"{_p}.cable", "state"),
                          f"{_c}.IcingRiskUncovered": ("BMS.calc", "calculated"),
                          f"{_c}.IcingHazardUncovered": ("BMS.calc", "calculated")})
for _z in _Z:
    _POINT_OBJECT.update({
        f"{_z}.AirTemp": (f"{_z}.room_temp", "measured"),
        f"{_z}.Occupants": (f"{_z}.presence", "measured"),
        f"{_z}.LightingMode": (f"{_z}.controller", "setpoint"),
        f"{_z}.DaylightHarvesting": (f"{_z}.controller", "setpoint"),
        f"{_z}.EquipmentFactor": (None, "input"),
        f"{_z}.HeatingSetpoint": (f"{_z}.controller", "setpoint"),
        f"{_z}.CoolingSetpoint": (f"{_z}.controller", "setpoint"),
        f"{_z}.CO2Setpoint": (f"{_z}.controller", "setpoint"),
        f"{_z}.LuxSetpoint": (f"{_z}.controller", "setpoint"),
        f"{_z}.RadiatorValve": (f"{_z}.valve_actuator", "state"),
        f"{_z}.RadiatorPower": (f"{_z}.radiator", "state"),
        f"{_z}.CoolingPower": (f"{_z}.cooling_unit", "state"),
        f"{_z}.Airflow": (f"{_z}.vav", "state"),
        f"{_z}.CO2": (f"{_z}.co2", "measured"),
        f"{_z}.Daylight": (f"{_z}.lux", "measured"),
        f"{_z}.Illuminance": (f"{_z}.lux", "measured"),
        f"{_z}.LightingPower": (f"{_z}.dimmer", "state"),
        f"{_z}.EquipmentPower": (f"{_z}.sockets", "measured"),
        f"{_z}.SolarGain": ("BMS.calc", "calculated"),
    })


def object_id(obj: str, building: str = BUILDING) -> str:
    return building + OBJECTS[obj][0]


def point_tfm(point_id: str, building: str = BUILDING) -> dict[str, Any]:
    """The TFM object behind one of the twin's data points, with its role."""
    if point_id not in _POINT_OBJECT:
        raise KeyError(point_id)
    obj, role = _POINT_OBJECT[point_id]
    if obj is None:
        return {"point": point_id, "tfm": None, "role": role, "object": None,
                "description": "simulation input; no building object"}
    return {"point": point_id, "tfm": object_id(obj, building), "role": role, "object": obj,
            "description": OBJECTS[obj][1]}


def zone_sensor_tfm(zone_id: str, building: str = BUILDING) -> str:
    """The TFM of a zone's temperature sensor: what a zone reading carries."""
    return object_id(f"{zone_id}.room_temp", building)



def render_markdown() -> str:
    """docs/tfm.md: the syntax, every code in both lists, and the building's TFM objects."""
    reg = register()
    src = reg["sources"]
    esc = lambda v: (v or "").replace("|", "/")  # noqa: E731
    L = ["# TFM: Tverrfaglig merkesystem (Statsbygg PA 0802)", "",
         "<!-- Generated by `python -m twin.tfm markdown --write` from twin/tfm_register.json and twin/tfm.py. "
         "Do not edit by hand; tests/unit/test_tfm.py fails when this file is stale. -->", "",
         "The testbed reads, validates and explains TFM identifiers (`twin/tfm.py`), and every data point "
         "of the twin names its TFM object. The code lists are Statsbygg's, indexed unaltered in "
         "`twin/tfm_register.json`; the PDFs remain the authority.", "",
         "| Source | Revision | Approved | sha256 |", "|---|---|---|---|"]
    for k in ("guide", "systems", "components"):
        if k in src:
            v = src[k]
            L.append(f"| [{v['title']}]({v['url']}) | {v['revision'] or 'n/a'} | {v['approved'] or 'n/a'} | `{v['sha256'][:16]}` |")
    syn = reg["id_syntax"]
    L += ["", "## Syntax", "", f"`{syn['full']}`", ""] + [f"- {syn[k]}" for k in ("location", "system", "component", "component_type")]
    L += ["", "Running numbers in HVAC systems (PA 0802 3.1.3):", ""]
    L += [f"- `{k}`: {v}" for k, v in reg["running_numbers_hvac"].items()]
    L += [f"- Electrical: {reg['running_numbers_electrical']}", "",
          "Validation (`twin.tfm.validate`) rejects a system code that Vedlegg 9.1 marks as a group or "
          "\"skal ikke benyttes\", an unassigned component code, a missing running number, and malformed "
          "identifiers. `python -m twin.tfm explain \"+123456=360.001-JV401\"` explains one.", "",
          "## The fictitious building `+DEMO01`", "",
          "These identifiers are valid PA 0802 codes for the testbed's fictitious building, laid out after "
          "the Vedlegg 9.3 examples. A real site's identifiers come from its approved asset inventory "
          "(SDD-OT-001 D-08), never from this table.", "",
          "| Object | TFM | System | Component | Position | Description | Data points |", "|---|---|---|---|---|---|---|"]
    pts: dict[str, list[str]] = {}
    for pid, (obj, role) in _POINT_OBJECT.items():
        if obj:
            pts.setdefault(obj, []).append(f"`{pid}` ({role})")
    for obj, (tid, desc) in OBJECTS.items():
        e = explain(BUILDING + tid)
        L.append(f"| `{obj}` | `{BUILDING}{tid}` | {e['system']['code']} {esc(e['system']['name'])} | "
                 f"{e['component']['code']} {esc(e['component']['name'])} | {e['component']['position'] or ''} | "
                 f"{esc(desc)} | {', '.join(pts.get(obj, []))} |")
    inputs = [pid for pid, (obj, _) in _POINT_OBJECT.items() if obj is None]
    L += ["", f"Simulation inputs with no building object: {', '.join(f'`{i}`' for i in inputs)}.", ""]
    sysl = reg["systems"]
    L += ["## System codes (Vedlegg 9.1)", "",
          f"{len(sysl)} entries: {sum(x['status'] == 'system_code' for x in sysl)} TFM system codes, "
          f"{sum(x['status'] == 'group' for x in sysl)} groups, {sum(x['status'] == 'not_used' for x in sysl)} "
          "not to be used in TFM. Codes marked *TFM* are defined by TFM, not by NS 3451.", "",
          "| Code | Name | Status |", "|---|---|---|"]
    for x in sysl:
        name = esc(x["name"]) or "(no name)"
        if x["level"] == 2:
            name = f"**{name}**"
        L.append(f"| {x['code']} | {name}{' *TFM*' if x.get('tfm_defined') else ''} | {x['status']} |")
    comp = reg["components"]
    L += ["", "## Component codes (Vedlegg 9.2)", "",
          f"{sum(x['status'] == 'component_code' for x in comp)} assigned component codes in "
          f"{sum(x['level'] == 1 for x in comp)} function classes; the "
          f"{sum(x['status'] == 'unassigned' for x in comp)} unassigned two-letter codes are omitted here and kept "
          "in the register.", "", "| Code | Name | Examples, synonyms | Remarks |", "|---|---|---|---|"]
    for x in comp:
        if x["level"] == 1:
            L.append(f"| **{x['code']}** | **{esc(x['name'])}** | {esc(x.get('description'))} | {x['status'] if x['status'] == 'not_used' else ''} |")
        elif x["status"] == "component_code":
            L.append(f"| {x['code']} | {esc(x['name'])} | {esc(x.get('examples'))} | {esc(x.get('remarks'))} |")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="twin.tfm")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("explain").add_argument("id")
    sub.add_parser("point").add_argument("point")
    sub.add_parser("list")
    sub.add_parser("markdown").add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "markdown":
        text = render_markdown()
        if a.write:
            (REGISTER.parents[1] / "docs" / "tfm.md").write_text(text, encoding="utf-8", newline="\n")
        else:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            print(text, end="")
        return 0
    if a.cmd == "explain":
        out: Any = explain(a.id)
    elif a.cmd == "point":
        out = point_tfm(a.point)
        if out["tfm"]:
            out["explained"] = explain(out["tfm"])
    else:
        out = {k: object_id(k) for k in OBJECTS}
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
