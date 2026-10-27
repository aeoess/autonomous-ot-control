"""Building services plant for the ``three-zone-hvac-v1`` twin model.

Pure, deterministic physics with no I/O and no clock: :func:`step` advances
the plant by ``dt`` seconds from its state, the zone air temperatures, the
current setpoints and the exogenous inputs. It returns the heat each zone
receives, the new plant state and every metered quantity.
``twin/model.py`` integrates the zone temperatures. Every data point in this
module is catalogued in ``twin/fixtures/datapoints-v1.json``, and
``docs/datapoints.md`` is generated from that catalogue.

Two layers, as in a real building:

* **Local loops** (what a PLC does, never written by an agent): radiator
  valves track the zone heating setpoint, cooling tracks the zone cooling
  setpoint, the AHU heating and cooling coils track the supply-air setpoint,
  the heat pump charges the buffer tank with on/off hysteresis and minimum
  run and stop times, DCV dampers track the CO2 setpoint, and lighting dims
  to the lux setpoint net of daylight.
* **Supervisory setpoints** (:class:`Setpoints`): the only values a strategy
  may change, and only through REMORA's governed tools.

Simplifications are deliberate and stated where they are made. This model is
synthetic and uncalibrated: it supports comparisons between strategies on the
same inputs, never a real-building savings claim.

Sources for the forms used (constants are illustrative defaults in the config):

* radiator output ∝ ΔT_lm^1.3 (EN 442 characteristic, exponent n = 1.3);
* heat pump COP as a fraction of the Carnot COP between source and sink;
* fan power = SFP × airflow (NS 3031 / TEK17 use SFP in kW/(m³/s));
* CO2 well-mixed mass balance, about 18 l/h of CO2 per seated adult;
* daylight ≈ daylight factor × outdoor illuminance, with outdoor
  illuminance ≈ 110 lm/W × global horizontal irradiance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

CP_AIR_J_PER_KG_K = 1005.0
RHO_AIR_KG_PER_M3 = 1.2
CP_WATER_J_PER_KG_K = 4186.0
KELVIN = 273.15
CO2_OUTDOOR_PPM = 420.0
CO2_PER_PERSON_M3_S = 0.005 / 1000.0      # 0.005 l/s = 18 l/h
SENSIBLE_W_PER_PERSON = 75.0
LUMEN_PER_WATT_DAYLIGHT = 110.0
RADIATOR_EXPONENT = 1.3


class PlantError(ValueError):
    pass


# -- configuration -------------------------------------------------------------

@dataclass(frozen=True)
class ZonePlant:
    zone_id: str
    area_m2: float
    volume_m3: float
    window_area_m2: float
    g_value: float                        # solar heat gain coefficient of glazing
    radiator_nominal_w: float             # at 70/55 degC water, 20 degC room
    cooling_max_w: float                  # chilled beam / fan coil
    equipment_w_per_m2: float             # when occupied
    equipment_base_w_per_m2: float        # always (servers, standby)
    lighting_w_per_m2: float              # full output
    daylight_factor: float                # 0.0..0.1
    min_airflow_l_s_m2: float             # building materials, always
    airflow_per_person_l_s: float
    max_airflow_l_s: float


@dataclass(frozen=True)
class PlantConfig:
    heat_source: str                      # "heat_pump" | "district_heating" | "electric_boiler"
    zones: tuple[ZonePlant, ...]
    # buffer tank between the heat source and the radiators/coil
    tank_volume_l: float
    tank_loss_w_per_k: float
    plant_room_c: float
    # air-to-water heat pump
    hp_nominal_heat_w: float              # at A7/W35
    hp_carnot_fraction: float
    hp_min_outdoor_c: float               # below this the compressor stops
    hp_max_supply_c: float
    hp_min_run_s: int
    hp_min_stop_s: int
    hp_hysteresis_k: float
    hp_defrost_band_c: tuple[float, float]
    hp_defrost_rh_pct: float
    hp_defrost_penalty: float             # fraction of capacity and COP lost while frosting
    backup_heater_w: float                # electric immersion heater in the tank
    # district heating substation
    dh_max_w: float
    dh_substation_efficiency: float
    # electric boiler
    boiler_max_w: float
    # radiator circuit
    radiator_dt_k: float                  # design supply-return difference
    # AHU (one unit serving all zones)
    sfp_kw_per_m3_s: float
    hrv_efficiency: float                 # temperature efficiency of the rotary wheel
    hrv_frost_limit_c: float              # below this the wheel's efficiency is reduced
    ahu_coil_max_w: float
    chiller_carnot_fraction: float
    chiller_condenser_offset_k: float
    chiller_evaporator_c: float
    # heating cables (ramps, gutters)
    cables: tuple[tuple[str, float], ...] = field(default_factory=tuple)  # (circuit, rated W)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PlantConfig":
        try:
            zones = tuple(ZonePlant(**z) for z in d["zones"])
            cfg = PlantConfig(
                heat_source=d["heat_source"], zones=zones,
                tank_volume_l=float(d["tank_volume_l"]), tank_loss_w_per_k=float(d["tank_loss_w_per_k"]),
                plant_room_c=float(d["plant_room_c"]),
                hp_nominal_heat_w=float(d["hp_nominal_heat_w"]),
                hp_carnot_fraction=float(d["hp_carnot_fraction"]),
                hp_min_outdoor_c=float(d["hp_min_outdoor_c"]), hp_max_supply_c=float(d["hp_max_supply_c"]),
                hp_min_run_s=int(d["hp_min_run_s"]), hp_min_stop_s=int(d["hp_min_stop_s"]),
                hp_hysteresis_k=float(d["hp_hysteresis_k"]),
                hp_defrost_band_c=(float(d["hp_defrost_band_c"][0]), float(d["hp_defrost_band_c"][1])),
                hp_defrost_rh_pct=float(d["hp_defrost_rh_pct"]),
                hp_defrost_penalty=float(d["hp_defrost_penalty"]),
                backup_heater_w=float(d["backup_heater_w"]),
                dh_max_w=float(d["dh_max_w"]), dh_substation_efficiency=float(d["dh_substation_efficiency"]),
                boiler_max_w=float(d["boiler_max_w"]),
                radiator_dt_k=float(d["radiator_dt_k"]),
                sfp_kw_per_m3_s=float(d["sfp_kw_per_m3_s"]), hrv_efficiency=float(d["hrv_efficiency"]),
                hrv_frost_limit_c=float(d["hrv_frost_limit_c"]), ahu_coil_max_w=float(d["ahu_coil_max_w"]),
                chiller_carnot_fraction=float(d["chiller_carnot_fraction"]),
                chiller_condenser_offset_k=float(d["chiller_condenser_offset_k"]),
                chiller_evaporator_c=float(d["chiller_evaporator_c"]),
                cables=tuple((c["circuit"], float(c["rated_w"])) for c in d.get("cables", [])),
            )
        except (KeyError, TypeError) as e:
            raise PlantError(f"plant config: {e}") from None
        if cfg.heat_source not in ("heat_pump", "district_heating", "electric_boiler"):
            raise PlantError(f"unknown heat_source {cfg.heat_source!r}")
        for name in ("hp_carnot_fraction", "hrv_efficiency", "dh_substation_efficiency",
                     "chiller_carnot_fraction", "hp_defrost_penalty"):
            if not 0.0 < getattr(cfg, name) <= 1.0:
                raise PlantError(f"{name} must be in (0, 1]")
        return cfg


# -- supervisory setpoints (the only writable layer) --------------------------

@dataclass(frozen=True)
class ZoneSetpoints:
    heating_c: float
    cooling_c: float
    co2_ppm: float
    lux: float                            # 0 = lights off
    ventilation_enabled: bool = True


@dataclass(frozen=True)
class Setpoints:
    zones: dict[str, ZoneSetpoints]
    supply_air_c: float
    tank_c: float                         # buffer tank target (heat source charges it)
    hp_max_fraction: float = 1.0          # demand-response cap on compressor output
    ahu_enabled: bool = True
    cable_modes: dict[str, str] = field(default_factory=dict)  # circuit -> auto|on|off


# -- exogenous inputs per step -------------------------------------------------

@dataclass(frozen=True)
class Exogenous:
    outdoor_c: float
    solar_w_m2: float                     # global horizontal irradiance
    outdoor_rh_pct: float
    occupants: dict[str, int]             # persons per zone
    icing_risk: bool = False
    #: Space heating enabled (summer stop off). When False the radiator circuit
    #: and the AHU heating coil are off and the heat source does not charge the
    #: tank for space heating; domestic hot water is not modelled.
    heating_season: bool = True
    equipment_factor: dict[str, float] = field(default_factory=dict)  # hourly load multiplier


# -- plant state ----------------------------------------------------------------

@dataclass(frozen=True)
class PlantState:
    tank_c: float
    co2_ppm: dict[str, float]
    hp_on: bool = False
    hp_state_since_s: float = 1e9         # seconds in the current on/off state
    hp_starts: int = 0


@dataclass(frozen=True)
class StepResult:
    state: PlantState
    zone_heat_w: dict[str, float]         # net heat into each zone's air node
    points: dict[str, float]              # every metered data point, by catalogue id
    electric_w: float
    district_heat_w: float


# -- component models -----------------------------------------------------------

def heat_pump_cop(source_c: float, sink_c: float, carnot_fraction: float) -> float:
    """COP of an air-to-water heat pump, clamped to [1, 6].

    Sink is the supply water plus 3 K condenser approach; source is outdoor air
    minus 6 K evaporator approach.
    """
    t_sink = sink_c + 3.0 + KELVIN
    t_source = source_c - 6.0 + KELVIN
    lift = max(t_sink - t_source, 5.0)
    return max(1.0, min(6.0, carnot_fraction * t_sink / lift))


def heat_pump_capacity_w(nominal_w: float, outdoor_c: float) -> float:
    """Heating capacity falls about 2.5 % per K below A7 (typical air-to-water)."""
    return max(0.0, nominal_w * (1.0 + 0.025 * (outdoor_c - 7.0)))


def chiller_eer(evaporator_c: float, condenser_c: float, carnot_fraction: float) -> float:
    t_evap, t_cond = evaporator_c + KELVIN, condenser_c + KELVIN
    return max(2.0, min(7.0, carnot_fraction * t_evap / max(t_cond - t_evap, 5.0)))


def weather_compensated_supply_c(outdoor_c: float) -> float:
    """Radiator supply curve: 60 degC at -20, 30 degC at +15, linear between."""
    return max(30.0, min(60.0, 30.0 + (15.0 - outdoor_c) * (30.0 / 35.0)))


def radiator_output_w(nominal_w: float, supply_c: float, return_c: float, room_c: float) -> float:
    """EN 442 characteristic relative to 70/55/20 (log-mean ΔT 42.1 K)."""
    if supply_c <= room_c + 0.5:
        return 0.0
    ret = max(min(return_c, supply_c - 0.1), room_c + 0.1)
    dt_lm = (supply_c - ret) / math.log((supply_c - room_c) / (ret - room_c))
    dt_nom = (70.0 - 55.0) / math.log((70.0 - 20.0) / (55.0 - 20.0))
    return nominal_w * (dt_lm / dt_nom) ** RADIATOR_EXPONENT


def outdoor_lux(solar_w_m2: float) -> float:
    return max(0.0, solar_w_m2) * LUMEN_PER_WATT_DAYLIGHT


# -- the step -----------------------------------------------------------------

def step(cfg: PlantConfig, state: PlantState, zone_temp_c: dict[str, float],
         sp: Setpoints, exo: Exogenous, dt: float) -> StepResult:
    if set(sp.zones) != {z.zone_id for z in cfg.zones}:
        raise PlantError("setpoints must name exactly the plant's zones")
    pts: dict[str, float] = {}
    zone_heat: dict[str, float] = {}
    elec_w = 0.0
    # The mixing valve follows the weather curve, but cannot deliver water
    # hotter than the tank actually holds.
    supply_water_c = min(weather_compensated_supply_c(exo.outdoor_c), state.tank_c)

    # ---- ventilation: airflow per zone (DCV), AHU heat recovery and coils ----
    flows: dict[str, float] = {}
    for z in cfg.zones:
        zs = sp.zones[z.zone_id]
        base = z.min_airflow_l_s_m2 * z.area_m2
        if not (sp.ahu_enabled and zs.ventilation_enabled):
            flows[z.zone_id] = 0.0
            continue
        need = base + z.airflow_per_person_l_s * exo.occupants.get(z.zone_id, 0)
        co2 = state.co2_ppm[z.zone_id]
        # DCV: proportional boost above the CO2 setpoint, 0..100 % over 200 ppm.
        boost = max(0.0, min(1.0, (co2 - zs.co2_ppm) / 200.0))
        flows[z.zone_id] = min(z.max_airflow_l_s, need + boost * (z.max_airflow_l_s - need))
    total_l_s = sum(flows.values())
    total_m3_s = total_l_s / 1000.0
    extract_c = (sum(flows[k] * zone_temp_c[k] for k in flows) / total_l_s) if total_l_s else 0.0
    eta = cfg.hrv_efficiency
    if exo.outdoor_c < cfg.hrv_frost_limit_c:
        eta *= 0.75  # frost protection of the wheel: reduced speed
    after_hrv_c = exo.outdoor_c + eta * (extract_c - exo.outdoor_c) if total_l_s else exo.outdoor_c
    # Free cooling: bypass the wheel when the outdoor air alone is closer to target.
    if total_l_s and exo.outdoor_c < sp.supply_air_c < after_hrv_c:
        after_hrv_c = max(exo.outdoor_c, sp.supply_air_c)
        pts["AHU-01.HRV_Bypass"] = 1.0
    else:
        pts["AHU-01.HRV_Bypass"] = 0.0
    m_air = RHO_AIR_KG_PER_M3 * total_m3_s
    coil_need_w = m_air * CP_AIR_J_PER_KG_K * (sp.supply_air_c - after_hrv_c)
    coil_heat_w = min(max(coil_need_w, 0.0), cfg.ahu_coil_max_w) if exo.heating_season else 0.0
    coil_cool_w = min(max(-coil_need_w, 0.0), cfg.ahu_coil_max_w)
    supply_air_c = after_hrv_c + ((coil_heat_w - coil_cool_w) / (m_air * CP_AIR_J_PER_KG_K)
                                  if m_air else 0.0)
    fan_w = cfg.sfp_kw_per_m3_s * 1000.0 * total_m3_s
    elec_w += fan_w
    pts.update({"AHU-01.Airflow": total_l_s, "AHU-01.HRV_AfterTemp": after_hrv_c,
                "AHU-01.SupplyAirTemp": supply_air_c, "AHU-01.ExtractAirTemp": extract_c,
                "AHU-01.HeatingCoilPower": coil_heat_w, "AHU-01.CoolingCoilPower": coil_cool_w,
                "AHU-01.FanPower": fan_w, "AHU-01.HRV_Efficiency": eta if total_l_s else 0.0})

    # ---- zones: radiators, cooling, ventilation, solar, people, equipment, light ----
    radiator_total_w = 0.0
    cooling_total_w = coil_cool_w
    new_co2: dict[str, float] = {}
    lux_out = outdoor_lux(exo.solar_w_m2)
    for z in cfg.zones:
        zid, zs, t = z.zone_id, sp.zones[z.zone_id], zone_temp_c[z.zone_id]
        n = exo.occupants.get(zid, 0)
        occupied = n > 0
        # Lighting: daylight first, electric light tops up to the setpoint.
        daylight = z.daylight_factor * lux_out
        target_lux = zs.lux if occupied else 0.0
        dim = max(0.0, min(1.0, (target_lux - daylight) / 500.0)) if target_lux > 0 else 0.0
        light_w = (0.1 + 0.9 * dim) * z.lighting_w_per_m2 * z.area_m2 if dim > 0 else 0.0
        # Radiator valve: proportional over 1 K below the heating setpoint.
        valve = max(0.0, min(1.0, (zs.heating_c - t) / 1.0 + 0.5)) if t < zs.heating_c + 0.5 else 0.0
        if not exo.heating_season:
            valve = 0.0  # summer stop: the radiator circuit pump is off
        rad_cap = radiator_output_w(z.radiator_nominal_w, supply_water_c,
                                    supply_water_c - cfg.radiator_dt_k, t)
        q_rad = valve * rad_cap
        # Cooling: proportional over 1 K above the cooling setpoint.
        cool_frac = max(0.0, min(1.0, t - zs.cooling_c)) if t > zs.cooling_c else 0.0
        q_cool = cool_frac * z.cooling_max_w
        q_vent = RHO_AIR_KG_PER_M3 * flows[zid] / 1000.0 * CP_AIR_J_PER_KG_K * (supply_air_c - t)
        q_solar = z.window_area_m2 * z.g_value * max(0.0, exo.solar_w_m2) * 0.5  # vertical vs horizontal
        q_people = n * SENSIBLE_W_PER_PERSON
        q_equip = ((z.equipment_w_per_m2 if occupied else 0.0) * z.area_m2
                   + z.equipment_base_w_per_m2 * z.area_m2) * exo.equipment_factor.get(zid, 1.0)
        equip_w = q_equip
        zone_heat[zid] = q_rad - q_cool + q_vent + q_solar + q_people + q_equip + light_w
        radiator_total_w += q_rad
        cooling_total_w += q_cool
        elec_w += light_w + equip_w
        # CO2 well-mixed balance.
        c = state.co2_ppm[zid]
        q_m3_s = flows[zid] / 1000.0
        dc = (n * CO2_PER_PERSON_M3_S * 1e6 + q_m3_s * (CO2_OUTDOOR_PPM - c)) / z.volume_m3
        new_co2[zid] = max(CO2_OUTDOOR_PPM, c + dt * dc)
        pts.update({f"{zid}.RadiatorPower": q_rad, f"{zid}.RadiatorValve": valve,
                    f"{zid}.CoolingPower": q_cool, f"{zid}.Airflow": flows[zid],
                    f"{zid}.CO2": new_co2[zid], f"{zid}.Daylight": daylight,
                    f"{zid}.Illuminance": daylight + (dim * 500.0 if dim > 0 else 0.0),
                    f"{zid}.LightingPower": light_w, f"{zid}.EquipmentPower": equip_w,
                    f"{zid}.SolarGain": q_solar, f"{zid}.Occupants": float(n)})

    # ---- cooling production (chiller, condenser to outdoor air) ----
    cond_c = max(exo.outdoor_c, 15.0) + cfg.chiller_condenser_offset_k
    eer = chiller_eer(cfg.chiller_evaporator_c, cond_c, cfg.chiller_carnot_fraction)
    chiller_w = cooling_total_w / eer if cooling_total_w > 0 else 0.0
    elec_w += chiller_w
    pts.update({"CH-01.CoolingLoad": cooling_total_w, "CH-01.EER": eer if cooling_total_w else 0.0,
                "CH-01.ElectricPower": chiller_w})

    # ---- heat source charging the buffer tank ----
    tank_c_j = cfg.tank_volume_l * CP_WATER_J_PER_KG_K   # J/K (1 l ~ 1 kg)
    demand_w = radiator_total_w + coil_heat_w
    loss_w = cfg.tank_loss_w_per_k * (state.tank_c - cfg.plant_room_c)
    source_heat_w = backup_w = dh_w = boiler_w = 0.0
    hp_on, since, starts = state.hp_on, state.hp_state_since_s + dt, state.hp_starts
    cop = 0.0
    if cfg.heat_source == "heat_pump":
        pts["HP-01.Defrost"] = 0.0
        want_on = exo.heating_season and (state.tank_c < sp.tank_c - cfg.hp_hysteresis_k or (
            hp_on and state.tank_c < sp.tank_c + cfg.hp_hysteresis_k))
        # High-pressure cut-out: the compressor stops whatever the setpoint says
        # once the tank passes the maximum supply temperature plus 5 K.
        cutout = state.tank_c >= cfg.hp_max_supply_c + 5.0
        can_run = (exo.outdoor_c >= cfg.hp_min_outdoor_c and sp.hp_max_fraction > 0
                   and not cutout)
        pts["HP-01.HighPressureCutout"] = 1.0 if cutout else 0.0
        if hp_on and (not want_on or not can_run) and (state.hp_state_since_s >= cfg.hp_min_run_s
                                                        or not can_run):
            hp_on, since = False, dt
        elif not hp_on and want_on and can_run and state.hp_state_since_s >= cfg.hp_min_stop_s:
            hp_on, since, starts = True, dt, starts + 1
        if hp_on:
            cap = heat_pump_capacity_w(cfg.hp_nominal_heat_w, exo.outdoor_c) * sp.hp_max_fraction
            cop = heat_pump_cop(exo.outdoor_c, min(sp.tank_c, cfg.hp_max_supply_c),
                                cfg.hp_carnot_fraction)
            lo, hi = cfg.hp_defrost_band_c
            if lo <= exo.outdoor_c <= hi and exo.outdoor_rh_pct >= cfg.hp_defrost_rh_pct:
                cap *= 1.0 - cfg.hp_defrost_penalty
                cop *= 1.0 - cfg.hp_defrost_penalty
                pts["HP-01.Defrost"] = 1.0
            source_heat_w = cap
            elec_w += cap / cop
        # Backup heater only when the tank falls 5 K below target (it cannot keep up).
        if exo.heating_season and state.tank_c < sp.tank_c - 5.0:
            backup_w = cfg.backup_heater_w
            elec_w += backup_w
        pts.update({"HP-01.Running": 1.0 if hp_on else 0.0, "HP-01.COP": cop,
                    "HP-01.HeatOutput": source_heat_w,
                    "HP-01.ElectricPower": source_heat_w / cop if cop else 0.0,
                    "HP-01.Starts": float(starts), "HP-01.BackupHeaterPower": backup_w})
    elif cfg.heat_source == "district_heating":
        need = (demand_w + loss_w + tank_c_j * (sp.tank_c - state.tank_c) / dt
                if exo.heating_season else 0.0)
        dh_w = min(max(need, 0.0), cfg.dh_max_w)
        source_heat_w = dh_w * cfg.dh_substation_efficiency
        pts.update({"DH-01.HeatPower": dh_w, "DH-01.SupplyTemp": max(sp.tank_c + 5.0, 70.0)})
    else:  # electric_boiler
        need = (demand_w + loss_w + tank_c_j * (sp.tank_c - state.tank_c) / dt
                if exo.heating_season else 0.0)
        boiler_w = min(max(need, 0.0), cfg.boiler_max_w)
        source_heat_w = boiler_w
        elec_w += boiler_w
        pts["EB-01.ElectricPower"] = boiler_w
    tank_c = state.tank_c + dt * (source_heat_w + backup_w - demand_w - loss_w) / tank_c_j
    pts["HYD-01.HeatingSeason"] = 1.0 if exo.heating_season else 0.0
    for zone in cfg.zones:
        pts[f"{zone.zone_id}.EquipmentFactor"] = exo.equipment_factor.get(zone.zone_id, 1.0)
    pts.update({"TANK-01.Temp": tank_c, "TANK-01.Loss": loss_w,
                "HYD-01.SupplyTemp": supply_water_c, "HYD-01.HeatDemand": demand_w})

    # ---- heating cables ----
    for circuit, rated in cfg.cables:
        mode = sp.cable_modes.get(circuit, "auto")
        on = mode == "on" or (mode == "auto" and exo.icing_risk)
        w = rated if on else 0.0
        elec_w += w
        pts[f"{circuit}.Power"] = w
        pts[f"{circuit}.IcingRiskUncovered"] = 1.0 if (exo.icing_risk and not on) else 0.0

    pts["EM-01.ElectricPower"] = elec_w
    pts["EM-01.DistrictHeatPower"] = dh_w
    new_state = replace(state, tank_c=tank_c, co2_ppm=new_co2, hp_on=hp_on,
                        hp_state_since_s=since, hp_starts=starts)
    return StepResult(state=new_state, zone_heat_w=zone_heat, points=pts,
                      electric_w=elec_w, district_heat_w=dh_w)
