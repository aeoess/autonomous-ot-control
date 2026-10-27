"""The data-point catalogue of the ``three-zone-hvac-v1`` twin: the single source.

Every value the simulated building exposes or accepts is declared here:

* measured and computed points (what the plant publishes each step);
* exogenous inputs (weather, price, occupancy);
* supervisory setpoints, the only writable layer.

For each point the catalogue also declares what it affects (the dependency
graph), the cross-point interlocks that protect the plant, the governed tools
that write it, and the multi-step procedures some changes require.

``python -m twin.datapoints --write`` renders ``docs/datapoints.md`` and
``twin/fixtures/datapoints-v1.json`` from this module.
``tests/unit/test_datapoints.py`` fails when:

* the plant emits a point the catalogue lacks, or the reverse;
* a dependency names an unknown point;
* a writable point has no tool or no interlock;
* the rendered files are stale.

Every interlock, tool and procedure carries a ``status``:

* ``IMPLEMENTED`` means the twin enforces or offers it today.
* ``SPECIFIED`` means it is designed here and not built yet.

The documentation never claims more than the status says.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ZONES = ("ZONE-01", "ZONE-02", "ZONE-03")
ZONE_NAMES = {"ZONE-01": "open-plan office", "ZONE-02": "meeting room",
              "ZONE-03": "laboratory (constant equipment load)"}
CIRCUITS = ("CABLE-RAMP", "CABLE-GUTTER")

# kind: AI analog measured, BI binary measured, AV computed analog, BV computed binary,
#       CV counter, EX exogenous input, SP writable analog setpoint, SB writable binary/enum
KINDS = {"AI": "measured analog", "BI": "measured binary", "AV": "computed analog",
         "BV": "computed binary", "CV": "counter", "EX": "exogenous input",
         "SP": "writable setpoint", "SB": "writable command"}


@dataclass(frozen=True)
class Point:
    id: str
    system: str
    kind: str
    unit: str
    range: tuple[float, float]
    description: str
    source: str                           # where its value comes from (function or input)
    affects: tuple[str, ...] = ()         # points whose value this one changes (direct edges)
    variants: tuple[str, ...] = ()        # heat sources it exists for; empty = all
    writable_via: str | None = None       # tool id
    interlocks: tuple[str, ...] = ()
    resolution: float | None = None


@dataclass(frozen=True)
class Interlock:
    id: str
    rule: str
    protects: str
    points: tuple[str, ...]
    status: str                           # IMPLEMENTED | SPECIFIED
    enforced_by: str                      # "twin interlock" | "plant local loop" | "REMORA ToolSpec" | ...


@dataclass(frozen=True)
class Tool:
    id: str
    writes: tuple[str, ...]
    arguments: dict[str, Any]
    risk_tier: str
    rate: str
    cascade: str
    status: str
    requires_procedure: str | None = None


@dataclass(frozen=True)
class Procedure:
    id: str
    name: str
    why: str
    steps: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class Cascade:
    id: str
    trigger: str
    chain: tuple[str, ...]
    consequence: str


@dataclass
class Catalogue:
    points: list[Point] = field(default_factory=list)
    interlocks: list[Interlock] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    procedures: list[Procedure] = field(default_factory=list)
    cascades: list[Cascade] = field(default_factory=list)

    def by_id(self) -> dict[str, Point]:
        return {p.id: p for p in self.points}


def build() -> Catalogue:
    c = Catalogue()
    P = c.points.append

    # -- exogenous inputs -----------------------------------------------------------
    P(Point("OUT-01.Temp", "outdoor", "EX", "degC", (-40, 35), "Outdoor air temperature (2 m).",
            "scenario outdoor series (ERA5 snapshot or declared curve)",
            affects=("AHU-01.HRV_AfterTemp", "HP-01.COP", "HP-01.HeatOutput", "HYD-01.SupplyTemp",
                     "CH-01.EER", *[f"{z}.AirTemp" for z in ZONES])))
    P(Point("OUT-01.Solar", "outdoor", "EX", "W/m2", (0, 1000), "Global horizontal irradiance.",
            "scenario solar series", affects=tuple(f"{z}.{k}" for z in ZONES
                                                  for k in ("SolarGain", "Daylight"))))
    P(Point("OUT-01.RH", "outdoor", "EX", "%", (0, 100), "Outdoor relative humidity.",
            "scenario humidity series", affects=("HP-01.Defrost",)))
    P(Point("OUT-01.Precipitation", "outdoor", "EX", "mm/h", (0, 50), "Precipitation, preceding hour.",
            "scenario precipitation series", affects=("OUT-01.IcingRisk",)))
    P(Point("OUT-01.IcingRisk", "outdoor", "BV", "bool", (0, 1),
            "Outdoor -6..+2 degC with precipitation within 3 h either side.",
            "Scenario.icing_risk", affects=tuple(f"{c_}.Power" for c_ in CIRCUITS)))
    P(Point("OUT-01.SurfaceWet", "outdoor", "AI", "bool", (0, 1),
            "Ground moisture sensor at the ramp: the surface is wet, snowy or iced (twin surface model: wet "
            "after precipitation; above +2 degC dries in 2 h; at or below +2 degC stays until the snow-melt "
            "cable has run 1 h without new precipitation).",
            "Twin._surface_wet", affects=tuple(f"{c_}.Power" for c_ in CIRCUITS)
            + tuple(f"{c_}.IcingHazardUncovered" for c_ in CIRCUITS)))
    P(Point("OUT-01.Temp24hMean", "outdoor", "AV", "degC", (-40, 35),
            "Mean outdoor temperature over the preceding 24 h; drives the summer stop.",
            "Scenario.mean_outdoor_24h", affects=("HYD-01.HeatingSeason",)))
    P(Point("GRID-01.SpotPrice", "market", "EX", "NOK/kWh", (-1, 20),
            "Day-ahead spot price for the site's area (NO3), spot only.",
            "scenario price series (hvakosterstrommen.no snapshot)",
            affects=("EM-01.Cost",)))
    P(Point("EM-01.Cost", "energy", "AV", "NOK", (0, 1e7),
            "Accumulated simulated energy cost: electricity at spot, district heat at the declared tariff.",
            "Twin._step"))

    # -- zones ---------------------------------------------------------------------
    for z in ZONES:
        nm = ZONE_NAMES[z]
        P(Point(f"{z}.AirTemp", z, "AI", "degC", (-10, 40), f"Room air temperature, {nm}.",
                "zone RC node (Twin._step)",
                affects=(f"{z}.RadiatorValve", f"{z}.CoolingPower", "AHU-01.ExtractAirTemp")))
        P(Point(f"{z}.Occupants", z, "EX", "persons", (0, 50), f"People present, {nm}.",
                "calendar x design_occupants",
                affects=(f"{z}.CO2", f"{z}.Airflow", f"{z}.LightingPower", f"{z}.EquipmentPower",
                         f"{z}.AirTemp")))
        P(Point(f"{z}.LightingMode", z, "SB", "presence|schedule", (0, 1),
                f"Lighting control, {nm}: lit while people are present, or through the scheduled hours "
                f"whoever is there (as found).", "supervisory (governed)",
                affects=(f"{z}.LightingPower",), writable_via="set_zone_lighting_mode",
                interlocks=("IL-08",)))
        P(Point(f"{z}.DaylightHarvesting", z, "SB", "bool", (0, 1),
                f"Daylight dimming, {nm}: electric light only tops up to the lux setpoint.",
                "supervisory (governed)", affects=(f"{z}.LightingPower",),
                writable_via="set_zone_lighting_mode", interlocks=("IL-08",)))
        P(Point(f"{z}.EquipmentFactor", z, "EX", "x", (0.5, 1.6),
                f"Hourly multiplier on the equipment load, {nm} (seeded lognormal; 1.0 without variation).",
                "Scenario.equipment_factor", affects=(f"{z}.EquipmentPower",)))
        P(Point(f"{z}.HeatingSetpoint", z, "SP", "degC", (8, 23),
                f"Heating setpoint, {nm}: the radiator valve's target.",
                "supervisory (governed)", affects=(f"{z}.RadiatorValve",),
                writable_via="set_zone_setpoint",
                interlocks=("IL-01", "IL-02", "IL-03", "IL-11"), resolution=0.1))
        P(Point(f"{z}.CoolingSetpoint", z, "SP", "degC", (20, 28),
                f"Cooling setpoint, {nm}: zone cooling starts above it.",
                "supervisory (governed)", affects=(f"{z}.CoolingPower",),
                writable_via="set_zone_cooling_setpoint", interlocks=("IL-01", "IL-11"),
                resolution=0.1))
        P(Point(f"{z}.CO2Setpoint", z, "SP", "ppm", (600, 1000),
                f"DCV target, {nm}: airflow rises over 200 ppm above it.",
                "supervisory (governed)", affects=(f"{z}.Airflow",),
                writable_via="set_zone_co2_setpoint", interlocks=("IL-07",), resolution=10))
        P(Point(f"{z}.LuxSetpoint", z, "SP", "lux", (0, 750),
                f"Maintained illuminance while occupied, {nm}; daylight counts toward it.",
                "supervisory (governed)", affects=(f"{z}.LightingPower",),
                writable_via="set_zone_lighting_setpoint", interlocks=("IL-08",), resolution=10))
        P(Point(f"{z}.RadiatorValve", z, "AV", "0..1", (0, 1),
                "Radiator valve opening (proportional over 1 K around the heating setpoint).",
                "plant local loop", affects=(f"{z}.RadiatorPower",)))
        P(Point(f"{z}.RadiatorPower", z, "AV", "W", (0, 20000),
                "Heat from the radiators (EN 442, capped by supply water temperature).",
                "plant.radiator_output_w", affects=("HYD-01.HeatDemand", f"{z}.AirTemp")))
        P(Point(f"{z}.CoolingPower", z, "AV", "W", (0, 10000),
                "Zone cooling (chilled beam), proportional over 1 K above the cooling setpoint.",
                "plant local loop", affects=("CH-01.CoolingLoad", f"{z}.AirTemp")))
        P(Point(f"{z}.Airflow", z, "AI", "l/s", (0, 1000),
                "Supply airflow to the zone (material minimum + per person + DCV boost).",
                "plant DCV", affects=("AHU-01.Airflow", f"{z}.CO2", f"{z}.AirTemp")))
        P(Point(f"{z}.CO2", z, "AI", "ppm", (400, 5000), "Room CO2 concentration.",
                "well-mixed CO2 balance", affects=(f"{z}.Airflow",)))
        P(Point(f"{z}.Daylight", z, "AV", "lux", (0, 100000),
                "Daylight on the work plane (daylight factor x outdoor illuminance).",
                "plant.outdoor_lux", affects=(f"{z}.LightingPower",)))
        P(Point(f"{z}.Illuminance", z, "AI", "lux", (0, 100000), "Work-plane illuminance, daylight plus electric.",
                "plant lighting loop"))
        P(Point(f"{z}.LightingPower", z, "AI", "W", (0, 10000),
                "Electric lighting power (10 % at minimum dim); all of it becomes heat in the zone.",
                "plant lighting loop", affects=("EM-01.ElectricPower", f"{z}.AirTemp")))
        P(Point(f"{z}.EquipmentPower", z, "AI", "W", (0, 20000), "Plug and process load; becomes heat.",
                "config equipment densities", affects=("EM-01.ElectricPower", f"{z}.AirTemp")))
        P(Point(f"{z}.SolarGain", z, "AV", "W", (0, 20000), "Solar heat through glazing.",
                "window area x g-value x irradiance", affects=(f"{z}.AirTemp",)))

    # -- hydronic heating and heat sources -------------------------------------------
    P(Point("HYD-01.SupplyTemp", "hydronic", "AI", "degC", (20, 75),
            "Radiator supply temperature: weather-compensated curve (60 at -20, 30 at +15), never hotter than the tank.",
            "plant.weather_compensated_supply_c", affects=tuple(f"{z}.RadiatorPower" for z in ZONES)))
    P(Point("HYD-01.HeatingSeason", "hydronic", "BV", "bool", (0, 1),
            "Space heating enabled. Off (summer stop) when the 24 h mean outdoor temperature rises above "
            "plant.heating_limit.off_above_c, on again below on_below_c. Off means radiator circuit and AHU "
            "heating coil off and no tank charging for space heating; domestic hot water is not modelled.",
            "Twin._heating_season", affects=tuple(f"{z}.RadiatorValve" for z in ZONES)
            + ("AHU-01.HeatingCoilPower", "HP-01.Running", "DH-01.HeatPower", "EB-01.ElectricPower")))
    P(Point("HYD-01.HeatDemand", "hydronic", "AV", "W", (0, 60000),
            "Heat drawn from the tank by radiators and the AHU heating coil.",
            "plant", affects=("TANK-01.Temp",)))
    P(Point("TANK-01.Temp", "hydronic", "AI", "degC", (10, 80), "Buffer tank temperature.",
            "tank energy balance", affects=("HYD-01.SupplyTemp", "HP-01.Running", "HP-01.BackupHeaterPower",
                                            "HP-01.HighPressureCutout")))
    P(Point("TANK-01.Setpoint", "hydronic", "SP", "degC", (35, 60),
            "Target the heat source charges the tank to. Higher stores more heat and lowers heat-pump COP.",
            "supervisory (governed)", affects=("HP-01.Running", "HP-01.COP", "DH-01.HeatPower",
                                               "EB-01.ElectricPower"),
            writable_via="set_tank_setpoint", interlocks=("IL-04", "IL-11"), resolution=0.5))
    P(Point("TANK-01.Loss", "hydronic", "AV", "W", (0, 1000), "Standing loss to the plant room.", "plant"))
    hp = ("heat_pump",)
    P(Point("HP-01.Running", "heat pump", "BI", "bool", (0, 1),
            "Compressor running (hysteresis 3 K, min run 10 min, min stop 10 min).",
            "plant local loop", affects=("HP-01.HeatOutput", "HP-01.Starts", "HP-01.ElectricPower"),
            variants=hp))
    P(Point("HP-01.COP", "heat pump", "AV", "-", (0, 6),
            "Coefficient of performance: 45 % of Carnot between outdoor air and tank, clamped 1..6; 0 when off.",
            "plant.heat_pump_cop", affects=("HP-01.ElectricPower",), variants=hp))
    P(Point("HP-01.HeatOutput", "heat pump", "AV", "W", (0, 40000),
            "Heat delivered; falls 2.5 %/K below A7, times the demand-response cap, less defrost.",
            "plant.heat_pump_capacity_w", affects=("TANK-01.Temp",), variants=hp))
    P(Point("HP-01.ElectricPower", "heat pump", "AI", "W", (0, 20000), "Compressor electric power.",
            "HeatOutput / COP", affects=("EM-01.ElectricPower",), variants=hp))
    P(Point("HP-01.Starts", "heat pump", "CV", "count", (0, 1e6),
            "Compressor starts since t=0; frequent cycling wears the compressor.",
            "plant local loop", variants=hp))
    P(Point("HP-01.Defrost", "heat pump", "BV", "bool", (0, 1),
            "Frosting conditions (-7..+5 degC, RH >= 75 %): 10 % of capacity and COP lost.",
            "plant", variants=hp))
    P(Point("HP-01.HighPressureCutout", "heat pump", "BI", "bool", (0, 1),
            "Compressor locked out: tank above max supply + 5 K.", "plant local loop", variants=hp))
    P(Point("HP-01.BackupHeaterPower", "heat pump", "AI", "W", (0, 20000),
            "Electric immersion heater, on when the tank is 5 K below target (COP 1).",
            "plant local loop", affects=("TANK-01.Temp", "EM-01.ElectricPower"), variants=hp))
    P(Point("HP-01.MaxFraction", "heat pump", "SP", "0..1", (0, 1),
            "Demand-response cap on compressor output.", "supervisory (governed)",
            affects=("HP-01.HeatOutput", "TANK-01.Temp", "HP-01.BackupHeaterPower"),
            variants=hp, writable_via="set_heat_pump_limit", interlocks=("IL-09", "IL-11"),
            resolution=0.05))
    dh = ("district_heating",)
    P(Point("DH-01.HeatPower", "district heating", "AI", "W", (0, 60000),
            "Heat drawn from the district-heating network by the substation.",
            "plant", affects=("TANK-01.Temp", "EM-01.DistrictHeatPower"), variants=dh))
    P(Point("DH-01.SupplyTemp", "district heating", "AI", "degC", (60, 120),
            "Primary supply temperature (declared: max(tank setpoint + 5, 70)).", "plant", variants=dh))
    P(Point("EB-01.ElectricPower", "electric boiler", "AI", "W", (0, 40000),
            "Electric boiler power.", "plant", affects=("TANK-01.Temp", "EM-01.ElectricPower"),
            variants=("electric_boiler",)))

    # -- air handling and cooling ----------------------------------------------------
    P(Point("AHU-01.Enabled", "ventilation", "SB", "bool", (0, 1), "AHU running.",
            "supervisory (governed)", affects=tuple(f"{z}.Airflow" for z in ZONES),
            writable_via="set_ahu_enabled", interlocks=("IL-06",)))
    P(Point("AHU-01.SupplyAirSetpoint", "ventilation", "SP", "degC", (15, 21),
            "Supply air temperature target (coils after heat recovery).", "supervisory (governed)",
            affects=("AHU-01.HeatingCoilPower", "AHU-01.CoolingCoilPower", "AHU-01.SupplyAirTemp"),
            writable_via="set_supply_air_setpoint", interlocks=("IL-05", "IL-11"), resolution=0.5))
    P(Point("AHU-01.Airflow", "ventilation", "AI", "l/s", (0, 3000), "Total supply airflow.",
            "sum of zone airflows", affects=("AHU-01.FanPower", "AHU-01.HeatingCoilPower")))
    P(Point("AHU-01.FanPower", "ventilation", "AI", "W", (0, 10000), "Fan power = SFP x airflow (SFP 1.5).",
            "plant", affects=("EM-01.ElectricPower",)))
    P(Point("AHU-01.HRV_Efficiency", "ventilation", "AV", "0..1", (0, 1),
            "Rotary heat recovery temperature efficiency (0.80; x0.75 below -15 degC frost limit).", "plant"))
    P(Point("AHU-01.HRV_Bypass", "ventilation", "BV", "bool", (0, 1),
            "Heat recovery bypassed for free cooling.", "plant"))
    P(Point("AHU-01.HRV_AfterTemp", "ventilation", "AI", "degC", (-40, 40), "Air temperature after heat recovery.",
            "plant", affects=("AHU-01.HeatingCoilPower", "AHU-01.CoolingCoilPower")))
    P(Point("AHU-01.ExtractAirTemp", "ventilation", "AI", "degC", (-10, 40), "Airflow-weighted extract air.",
            "plant", affects=("AHU-01.HRV_AfterTemp",)))
    P(Point("AHU-01.SupplyAirTemp", "ventilation", "AI", "degC", (-40, 40), "Supply air temperature delivered.",
            "plant", affects=tuple(f"{z}.AirTemp" for z in ZONES)))
    P(Point("AHU-01.HeatingCoilPower", "ventilation", "AV", "W", (0, 20000), "Heating coil, drawn from the tank.",
            "plant", affects=("HYD-01.HeatDemand",)))
    P(Point("AHU-01.CoolingCoilPower", "ventilation", "AV", "W", (0, 20000), "Cooling coil, from the chiller.",
            "plant", affects=("CH-01.CoolingLoad",)))
    P(Point("CH-01.CoolingLoad", "cooling", "AV", "W", (0, 40000), "Cooling demand on the chiller.",
            "plant", affects=("CH-01.ElectricPower",)))
    P(Point("CH-01.EER", "cooling", "AV", "-", (0, 7),
            "Chiller efficiency: 45 % of Carnot, condenser 10 K above max(outdoor, 15); 0 when idle.",
            "plant.chiller_eer", affects=("CH-01.ElectricPower",)))
    P(Point("CH-01.ElectricPower", "cooling", "AI", "W", (0, 20000), "Chiller electric power.",
            "CoolingLoad / EER", affects=("EM-01.ElectricPower",)))

    # -- heating cables and meters ------------------------------------------------------
    for cc in CIRCUITS:
        P(Point(f"{cc}.Mode", "heating cables", "SB", "auto|on|off", (0, 2),
                f"Heating cable mode, {cc.split('-')[1].lower()}.", "supervisory (governed)",
                affects=(f"{cc}.Power", f"{cc}.IcingRiskUncovered"),
                writable_via="set_heating_cable_mode", interlocks=("IL-10",)))
        P(Point(f"{cc}.Power", "heating cables", "AI", "W", (0, 10000), "Cable power (rated when on).",
                "plant", affects=("EM-01.ElectricPower",)))
        P(Point(f"{cc}.IcingRiskUncovered", "heating cables", "BV", "bool", (0, 1),
                "Conservative proxy: the 3 h icing-risk window with the circuit off.", "plant"))
        P(Point(f"{cc}.IcingHazardUncovered", "heating cables", "BV", "bool", (0, 1),
                "HAZARD: the ground sensor reports a wet surface at -10..+1 degC and the circuit is off.",
                "plant"))
    P(Point("EM-01.ElectricPower", "energy", "AI", "W", (0, 200000),
            "Building electricity meter: fans, chiller, heat pump, backup, boiler, lights, equipment, cables.",
            "plant", affects=("EM-01.Cost",)))
    P(Point("EM-01.DistrictHeatPower", "energy", "AI", "W", (0, 100000), "District heat meter.", "plant",
            affects=("EM-01.Cost",)))
    P(Point("EM-01.MonthlyPeakKw", "energy", "AV", "kW", (0, 500),
            "The month's highest hourly mean electric power: the basis of the grid tariff's power charge.",
            "Twin._grid_tariff", affects=("EM-01.GridCost",)))
    P(Point("EM-01.GridCost", "energy", "AV", "NOK", (0, 1e7),
            "Declared reference grid tariff (ASSUMPTION): energy part per kWh plus the monthly power part.",
            "Twin.grid_power_cost"))

    # -- interlocks ----------------------------------------------------------------------
    IL = c.interlocks.append
    IL(Interlock("IL-01", "CoolingSetpoint >= HeatingSetpoint + 2.0 K in every zone",
                "no simultaneous heating and cooling", ("*.HeatingSetpoint", "*.CoolingSetpoint"),
                "SPECIFIED", "twin interlock"))
    IL(Interlock("IL-02", "HeatingSetpoint >= 20.0 while the zone is occupied or within its recovery lead",
                "comfort during occupancy (D-15)", ("*.HeatingSetpoint", "*.Occupants"), "SPECIFIED",
                "twin interlock"))
    IL(Interlock("IL-03", "HeatingSetpoint >= 8.0 degC always; today the interlock floor is 19.0 (D-15 pending)",
                "frost protection of the building", ("*.HeatingSetpoint",), "IMPLEMENTED",
                "twin interlock + signed ToolSpec"))
    IL(Interlock("IL-04", "TANK-01.Setpoint within 35..max supply (60); at most +3 K per 15 min",
                "heat-pump high-pressure margin and radiator supply", ("TANK-01.Setpoint",), "SPECIFIED",
                "twin interlock + signed ToolSpec"))
    IL(Interlock("IL-05", "AHU-01.SupplyAirSetpoint within 15..21 degC",
                "draught and coil freezing", ("AHU-01.SupplyAirSetpoint",), "SPECIFIED",
                "twin interlock + signed ToolSpec"))
    IL(Interlock("IL-06", "AHU-01.Enabled = false refused while any zone is occupied",
                "statutory ventilation during occupancy", ("AHU-01.Enabled", "*.Occupants"),
                "SPECIFIED", "twin interlock"))
    IL(Interlock("IL-07", "CO2Setpoint within 600..1000 ppm",
                "indoor air quality (1000 ppm guideline)", ("*.CO2Setpoint",), "SPECIFIED",
                "signed ToolSpec"))
    IL(Interlock("IL-08", "LuxSetpoint >= 300 while occupied; 0 only when unoccupied",
                "workplace lighting (NS-EN 12464-1: 500 lux offices)", ("*.LuxSetpoint", "*.Occupants"),
                "SPECIFIED", "twin interlock"))
    IL(Interlock("IL-09", "HP-01.MaxFraction < 1 refused while TANK-01.Temp < Setpoint - 2 K",
                "a cap that would start the backup heater (COP 1)", ("HP-01.MaxFraction", "TANK-01.Temp"),
                "SPECIFIED", "twin interlock"))
    IL(Interlock("IL-10", "CABLE-*.Mode = off refused while OUT-01.SurfaceWet at -10..+1 degC "
                "(the ground sensor reports a freezing wet surface)",
                "ice on ramps and gutters", ("CABLE-*.Mode", "OUT-01.SurfaceWet"), "SPECIFIED",
                "twin interlock"))
    IL(Interlock("IL-11", "rate: at most one change per point per 15 min; setpoints move <= 1.0 K per change",
                "actuator wear and stability", ("*",), "IMPLEMENTED",
                "twin interlock (zone heating today) + REMORA cumulative limits"))

    # -- governed tools -------------------------------------------------------------------
    T = c.tools.append
    zone = {"type": "string", "enum": list(ZONES)}
    T(Tool("set_zone_setpoint", ("*.HeatingSetpoint",),
           {"zone_id": zone, "setpoint_c": {"type": "number", "minimum": 19.0, "maximum": 23.0}},
           "high", "<=1.0 K, >=15 min, <=2/h per zone",
           "valve -> radiator power -> tank draw -> heat-pump start (or backup heater)", "IMPLEMENTED"))
    T(Tool("set_zone_cooling_setpoint", ("*.CoolingSetpoint",),
           {"zone_id": zone, "cooling_c": {"type": "number", "minimum": 20.0, "maximum": 28.0}},
           "medium", "<=1.0 K, >=15 min", "chilled beam -> chiller electric", "SPECIFIED"))
    T(Tool("set_zone_co2_setpoint", ("*.CO2Setpoint",),
           {"zone_id": zone, "co2_ppm": {"type": "integer", "minimum": 600, "maximum": 1000}},
           "medium", "<=100 ppm per change", "airflow -> fan power and coil heat -> tank draw", "SPECIFIED"))
    T(Tool("set_zone_lighting_setpoint", ("*.LuxSetpoint",),
           {"zone_id": zone, "lux": {"type": "integer", "minimum": 0, "maximum": 750}},
           "low", ">=15 min", "lighting power -> zone heat gain -> less radiator heat", "SPECIFIED"))
    T(Tool("set_zone_lighting_mode", ("*.LightingMode", "*.DaylightHarvesting"),
           {"zone_id": zone, "mode": {"type": "string", "enum": ["presence", "schedule"]},
            "daylight_harvesting": {"type": "boolean"}},
           "low", ">=15 min", "lighting power -> zone heat gain -> radiator heat", "SPECIFIED"))
    T(Tool("set_supply_air_setpoint", ("AHU-01.SupplyAirSetpoint",),
           {"supply_air_c": {"type": "number", "minimum": 15.0, "maximum": 21.0}},
           "high", "<=1.0 K, >=15 min", "coil power -> tank draw; supply air -> every zone's radiator demand",
           "SPECIFIED"))
    T(Tool("set_tank_setpoint", ("TANK-01.Setpoint",),
           {"tank_c": {"type": "number", "minimum": 35.0, "maximum": 60.0}},
           "high", "<=3 K, >=15 min", "heat-pump run time and COP; stored heat for load shifting",
           "SPECIFIED", requires_procedure="PR-03"))
    T(Tool("set_heat_pump_limit", ("HP-01.MaxFraction",),
           {"max_fraction": {"type": "number", "minimum": 0.0, "maximum": 1.0}},
           "high", ">=15 min", "heat output -> tank -> possibly the backup heater (expensive)",
           "SPECIFIED", requires_procedure="PR-04"))
    T(Tool("set_ahu_enabled", ("AHU-01.Enabled",), {"enabled": {"type": "boolean"}},
           "high", ">=30 min", "all airflow, CO2, fan power, coil heat", "SPECIFIED",
           requires_procedure="PR-02"))
    T(Tool("set_heating_cable_mode", ("CABLE-*.Mode",),
           {"circuit": {"type": "string", "enum": list(CIRCUITS)},
            "mode": {"type": "string", "enum": ["auto", "thermostat", "sensor", "on", "off"]}},
           "high", ">=15 min", "cable power; off during icing risk is a hazard", "SPECIFIED"))

    # -- procedures (multi-step changes) ------------------------------------------------------
    R = c.procedures.append
    R(Procedure("PR-01", "Zone setback and recovery",
                "the interlock moves at most 1 K per change and twice per hour, and the deadband (IL-01) "
                "means the cooling setpoint must move first when heating rises toward it",
                ("raising: if HeatingSetpoint + delta > CoolingSetpoint - 2, raise CoolingSetpoint first",
                 "move HeatingSetpoint in <= 1.0 K steps, >= 15 min apart",
                 "lowering: move HeatingSetpoint down in steps; CoolingSetpoint may follow afterwards",
                 "verify AirTemp reaches the band before occupancy (optimal start computes the lead)"),
                "SPECIFIED"))
    R(Procedure("PR-02", "AHU night shutdown and morning start",
                "stopping ventilation while people are present is not allowed (IL-06), and a cold start "
                "blows cold air if the supply setpoint is low",
                ("check every zone's Occupants = 0 and none expected within 30 min",
                 "set_ahu_enabled(false)",
                 "morning: set_supply_air_setpoint(>= 18) before set_ahu_enabled(true)",
                 "set_ahu_enabled(true) >= 30 min before occupancy; verify CO2 < 800 ppm at start"),
                "SPECIFIED"))
    R(Procedure("PR-03", "Load shift by tank pre-charge",
                "stored heat moves consumption to cheap hours, but a hotter tank lowers COP and nears "
                "the high-pressure limit",
                ("raise TANK-01.Setpoint in <= 3 K steps toward <= 55 degC during cheap hours",
                 "never combine with an HP-01.MaxFraction cap below 1.0",
                 "lower TANK-01.Setpoint back to the curve requirement before the expensive block ends",
                 "verify HP-01.HighPressureCutout stayed 0 and HP-01.Starts rose by <= 3 per hour"),
                "SPECIFIED"))
    R(Procedure("PR-04", "Demand-response cap",
                "capping the compressor while the tank is short starts the backup heater, which costs "
                "about COP times more than the heat it replaces",
                ("pre-charge the tank (PR-03)",
                 "set_heat_pump_limit(<1.0) only while TANK-01.Temp >= Setpoint - 2 (IL-09)",
                 "release (1.0) as soon as TANK-01.Temp < Setpoint - 3 or the cap window ends",
                 "verify HP-01.BackupHeaterPower stayed 0 during the cap"),
                "SPECIFIED"))
    R(Procedure("PR-05", "Heating-cable price deferral",
                "a ramp without heat in icing conditions is a safety hazard; only a dry, short deferral "
                "is acceptable",
                ("only CABLE-RAMP, only when no precipitation is forecast during the deferral",
                 "defer at most 1 h into a cheaper hour; never during OUT-01.IcingRisk",
                 "the interlock turns the circuit on when the deferral expires (IL-10)"),
                "SPECIFIED"))

    # -- documented cascades ----------------------------------------------------------------
    K = c.cascades.append
    K(Cascade("CS-01", "raise a zone HeatingSetpoint",
              ("ZONE.RadiatorValve", "ZONE.RadiatorPower", "HYD-01.HeatDemand", "TANK-01.Temp",
               "HP-01.Running", "HP-01.Starts", "HP-01.ElectricPower", "EM-01.ElectricPower"),
              "a zone change draws the shared tank down; several zones at once can push it 5 K "
              "below target and start the backup heater at COP 1"))
    K(Cascade("CS-02", "cap HP-01.MaxFraction for a price peak",
              ("HP-01.HeatOutput", "TANK-01.Temp", "HP-01.BackupHeaterPower", "EM-01.ElectricPower"),
              "the cap saves only if the tank holds; otherwise the backup heater costs more than "
              "the compressor would have (PR-04, IL-09)"))
    K(Cascade("CS-03", "lower AHU-01.SupplyAirSetpoint",
              ("AHU-01.HeatingCoilPower", "AHU-01.SupplyAirTemp", "ZONE.AirTemp", "ZONE.RadiatorPower",
               "HYD-01.HeatDemand"),
              "coil heat moves to the radiators; the total heat barely changes, the comfort can"))
    K(Cascade("CS-04", "raise a CO2Setpoint (less ventilation)",
              ("ZONE.Airflow", "AHU-01.FanPower", "AHU-01.HeatingCoilPower", "ZONE.CO2"),
              "saves fan and coil energy, costs air quality; bounded by IL-07"))
    K(Cascade("CS-05", "dim lighting (LuxSetpoint down)",
              ("ZONE.LightingPower", "ZONE.AirTemp", "ZONE.RadiatorPower"),
              "in winter part of the lighting saving returns as radiator heat (lights are heat)"))
    K(Cascade("CS-06", "raise TANK-01.Setpoint for load shifting",
              ("HP-01.COP", "HP-01.ElectricPower", "TANK-01.Loss", "HP-01.HighPressureCutout"),
              "stores heat at a worse COP and higher standing loss; worth it only across a large "
              "enough price spread"))
    K(Cascade("CS-07", "CABLE mode off",
              ("CABLE.Power", "CABLE.IcingRiskUncovered"),
              "a saving that is a hazard during icing risk; never netted against cost"))
    return c


def as_json(c: Catalogue) -> dict[str, Any]:
    return {"schema": "ot-datapoints/v1", "model": "three-zone-hvac-v1",
            "kinds": KINDS,
            "points": [asdict(p) for p in c.points],
            "interlocks": [asdict(i) for i in c.interlocks],
            "tools": [asdict(t) for t in c.tools],
            "procedures": [asdict(r) for r in c.procedures],
            "cascades": [asdict(k) for k in c.cascades]}


def _fmt_range(r: tuple[float, float]) -> str:
    def f(v: float) -> str:
        return str(int(v)) if float(v).is_integer() else str(v)
    return f"{f(r[0])} .. {f(r[1])}"


def render_markdown(c: Catalogue) -> str:
    out = ["# Data points — `three-zone-hvac-v1`", "",
           "<!-- Generated by `python -m twin.datapoints --write` from twin/datapoints.py. "
           "Do not edit by hand; tests/unit/test_datapoints.py fails when this file is stale. -->", "",
           "Every value the simulated building exposes or accepts. The twin is synthetic and "
           "uncalibrated (ADR-007); ranges are plausibility bounds for validation, not design values. "
           "Writable points change only through the governed tools below, never directly.", "",
           f"{len(c.points)} points, {len(c.interlocks)} interlocks, {len(c.tools)} tools, "
           f"{len(c.procedures)} procedures, {len(c.cascades)} documented cascades.", "",
           "Kinds: " + "; ".join(f"`{k}` {v}" for k, v in KINDS.items()) + ".", ""]
    systems: dict[str, list[Point]] = {}
    for p in c.points:
        systems.setdefault(p.system, []).append(p)
    out += ["## Points", ""]
    for sysname, pts in systems.items():
        out += [f"### {sysname}", "",
                "| Id | Kind | Unit | Range | Description | Source | Affects | Written by |",
                "|---|---|---|---|---|---|---|---|"]
        for p in pts:
            var = f" *(only: {', '.join(p.variants)})*" if p.variants else ""
            w = f"`{p.writable_via}` ({', '.join(p.interlocks)})" if p.writable_via else ""
            out.append(f"| `{p.id}` | {p.kind} | {p.unit} | {_fmt_range(p.range)} | "
                       f"{p.description}{var} | {p.source} | "
                       f"{', '.join(f'`{a}`' for a in p.affects)} | {w} |")
        out.append("")
    out += ["## Interlocks", "", "| Id | Rule | Protects | Status | Enforced by |", "|---|---|---|---|---|"]
    out += [f"| {i.id} | {i.rule} | {i.protects} | `{i.status}` | {i.enforced_by} |" for i in c.interlocks]
    out += ["", "## Governed tools", "",
            "Each becomes a signed REMORA ToolSpec (argument schema, target, closure digest) and a "
            "twin actuator route. `requires` names the procedure a strategy must follow.", "",
            "| Tool | Writes | Arguments | Risk | Rate | Cascade | Requires | Status |",
            "|---|---|---|---|---|---|---|---|"]
    for t in c.tools:
        args = "; ".join(f"`{k}`: {json.dumps(v)}" for k, v in t.arguments.items())
        out.append(f"| `{t.id}` | {', '.join(t.writes)} | {args} | {t.risk_tier} | {t.rate} | "
                   f"{t.cascade} | {t.requires_procedure or ''} | `{t.status}` |")
    out += ["", "## Procedures (multi-step changes)", ""]
    for r in c.procedures:
        out += [f"### {r.id} — {r.name} (`{r.status}`)", "", f"Why: {r.why}.", ""]
        out += [f"{n}. {s}" for n, s in enumerate(r.steps, 1)]
        out.append("")
    out += ["## Cascades", "", "| Id | Trigger | Chain | Consequence |", "|---|---|---|---|"]
    out += [f"| {k.id} | {k.trigger} | {' → '.join(k.chain)} | {k.consequence} |" for k in c.cascades]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="twin.datapoints")
    p.add_argument("--write", action="store_true")
    a = p.parse_args(argv)
    c = build()
    md, js = render_markdown(c), json.dumps(as_json(c), indent=2, ensure_ascii=False) + "\n"
    if a.write:
        (ROOT / "docs" / "datapoints.md").write_text(md, encoding="utf-8", newline="\n")
        (ROOT / "twin" / "fixtures" / "datapoints-v1.json").write_text(js, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
