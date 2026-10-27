# Building priors from Norwegian energy certificates

The demo building (`twin/fixtures/three-zone-hvac-seasonal-v2.json`) was
written by hand: 270 m², an air-to-water heat pump, 9 kW of heating cables on
an air thermostat. Its savings figures rested on those choices. This page says
what the testbed now uses instead, where it comes from, how it is mapped, and
what it still does not establish.

## Source

- `docs/kalibreringsgrunnlag_ot_anonymisert.json`: 536 anonymised 2015
  energy certificates (369 offices, 134 retail, 33 hotels) with BRA, U-values
  and areas, heat recovery, SFP, airflow, system efficiency, installed heating
  W/m², setpoints, heat capacity, lighting and equipment W/m², heat source,
  and the calculated delivered energy.
- `docs/Enova_OT_kalibreringsark_2026.xlsx`: the same data with yearly
  benchmarks (2015, 2016, 2025) by category and energy grade, correlations,
  the parameter mapping (`OT_mapping`), start profiles and data-quality notes.

Both are **priors, not a calibration**. Every annual figure is calculated,
not measured (the data-quality sheet: 0 of 536 with measured energy). UA
excludes ventilation, infiltration and thermal bridges. The heat capacity is
a normalised value, not a verified RC time constant. Nothing is live
telemetry, and none of it may be used for live OT or control policy.

## What changed because of it

1. **The as-found heat source.** Most offices in the certificates are heated
   directly by electricity: 66 of 88 small, 149 of 185 medium, 68 of 95
   large. The demo building's heat pump is a measure (`tiltak_varmepumpe`),
   not the as-found case. Calibrated buildings take each cohort's dominant
   heat source; direct electric heating is modelled as the plant's electric
   boiler (heat at about 1 per kWh, no COP).
2. **No heating cables by default.** Certificates carry none. The demo
   building's cable lever, 80–90 % of its headline saving, does not exist in
   a calibrated building unless a rating is given (`--cables-kw`), stated.
3. **Ventilation without a double count.** The certificate's airflow is the
   operating-time total. The plant adds 7 l/s per person to a per-m² base, so
   the base is the total minus that at one person per 12 m², never below
   0.35 l/s·m².
4. **District heating efficiency.** The certificate's annual system
   efficiency is carried by the substation term, the only one the plant has.
5. **Consistent zone areas.** The demo fixture has different zone areas on
   the thermal side (120/90/60) and the plant side (120/60/80). Calibrated
   buildings use one set for both. The demo fixture is corrected separately
   and its 2025 results rerun.

## Mapping (from `OT_mapping`)

| Twin parameter | From the certificate (cohort median or one building) |
|---|---|
| zone `area_m2` (thermal and plant) | BRA × zone share 4/9, 3/9, 2/9 |
| zone `ua_w_per_k` | (UA transmission / BRA + thermal bridge) × zone area |
| zone `capacity_j_per_k` | normalised heat capacity Wh/(m²K) × 3600 × zone area |
| zone `heater_max_w`, plant `radiator_nominal_w` | installed heating W/m² × zone area |
| plant `lighting_w_per_m2`, `equipment_w_per_m2`, `cooling_max_w` | the certificate's W/m² |
| plant `min_airflow_l_s_m2`, `airflow_per_person_l_s` | operating airflow / 3.6 minus 7 l/s per person at 1 per 12 m²; 7 l/s |
| plant `sfp_kw_per_m3_s`, `hrv_efficiency` | SFP in operation; annual heat recovery / 100 |
| plant `heat_source`, `dh_substation_efficiency` | dominant cohort source; system efficiency for district heating |
| default heating and cooling setpoints | the certificate's, cooling at least 2 K above heating |
| `inter_zone_conductance_w_per_k` | not in any certificate: the demo's 40 W/K × BRA/270, stated |
| design occupants | one per 12 m², stated |

Every built fixture carries a `calibration` block with the priors file's
SHA-256, the source (cohort and size, or certificate id), the parameters and
the assumptions above.

## The calibrated buildings

`twin/fixtures/calibrated/`, built and rebuilt byte for byte by
`runner.calibration` (a test pins it):

| Fixture | Source | BRA | Heat source | P0 year, NO3 2025 | Certificate band P10 / median / P90 |
|---|---|---:|---|---:|---|
| `kontor-small-v1` | 88 offices < 500 m² | 296 m² | direct electric | 199 kWh/m² | 134 / 250 / 434 |
| `kontor-medium-v1` | 185 offices 500–2499 m² | 1136 m² | direct electric | 187 kWh/m² | 151 / 211 / 369 |
| `kontor-large-v1` | 95 offices ≥ 2500 m² | 4153 m² | direct electric | 196 kWh/m² | 144 / 183 / 276 |
| `kontor-1920-fjernvarme-v1` | one certificate, built 1920 | 1500 m² | district heating | 266 kWh/m² | ± 30 % of its 221 |

All four land inside their band. That is a plausibility check, not a fit:
the certificate's figure is a normal-year calculation of all delivered
energy, the twin's is one simulated year on verified 2025 weather. The small
office shows high comfort degree-hours, worth a look before it is used for
savings.

```bash
python -m runner.calibration profiles                     # all cohort profiles
python -m runner.calibration build --category Kontorbygg --size medium --out twin/fixtures/calibrated/<id>.json
python -m runner.calibration check twin/fixtures/calibrated/kontor-medium-v1.json
```

## Calibrating against a measured building

`runner.site_calibration` takes an anonymised hourly meter export (schema
hourly meter v2: `manifest.json`, `meters/<id>.json`,
`data_quality_audit.json`), identified only by its `site_id`. The export
itself stays outside the repository (`.gitignore`); no address, owner or
meter-point identifier is read or written.

1. **Quality gate** from the export's own audit: flagged rows, the
   simultaneous-doubling window, catch-up spikes (over 20 × the meter's P99)
   and the partial last hour are excluded. An absent hour stays absent, never
   zero. Joins to weather are by UTC hour.
2. **Targets**, each with its coverage: the electricity load shape of the
   top-level meters (hourly median kW, weekday and weekend, night base,
   daytime level, ratios); the heating and cooling energy signatures
   (kWh/day = base + a·max(0, Tb − T)); heating-cable energy against hours in
   the icing band.
3. **Identifiability is reported.** A signature's balance temperature and
   weather-independent base count as targets only if the kinked fit beats a
   straight line clearly and has days on both sides; otherwise only the slope
   does.
4. **The same targets on the twin** (`twin_targets`), for a candidate fixture
   over a year of the same area, so the two can be compared line by line.

```bash
python -m runner.exogenous fetch --site twin/fixtures/site-demo-building-no1.json \
    --from <first day> --to <last day> --out scenarios/data/demo-building-no1 --kinds outdoor solar price
python -m runner.site_calibration --data <extracted export> \
    --weather scenarios/data/demo-building-no1/outdoor-temperature-<period>.json --out artifacts/<site>-calibration
```

What one measured site can fit, and what it cannot: building scale from the
heating slope, base load from the night, lighting and equipment from the
weekday daytime increment, and the weekday/weekend pattern. Not the balance
temperature or domestic hot water when the heating meters do not cover warm
days, not the meter hierarchy, not COP, and not a causal saving.

## SITE_001: one measured office, NO1, 2026

Aggregates in [`scenarios/calibration/site-001.json`](../scenarios/calibration/site-001.json),
published with the owner's consent; the hourly export is not in the
repository and no address or identifier is. 21 meters, 2026-02-16 to
2026-10-27. The fit runs on a scenario of exactly that period
(`scenarios/no1-2026-metered-period-hvac-v1.json`: verified NO1 prices and
ERA5 weather for those days, Norwegian holidays, occupancy from the measured
profile).

**What it changed in the simulator**

- **Occupancy hours.** The measured weekday load is at least halfway from
  night base to peak from 08 to 16 local time. All 2025 scenarios now use
  `weekday_hours_local: [8, 16]` with summer time; before, they used 07–17
  UTC, which is 09–19 in summer.
- **Base load.** The building uses 42 % of its daytime load at night; the
  demo building used 10 %. The calibrated building carries 7.8 W/m² of
  permanent load.
- **Lighting and equipment.** The daytime increment is about 30 % of the
  certificate cohort's lighting and equipment density (2.3 and 3.1 W/m²).
- **Heating cables.** The ramp cable used 78 kWh over four weeks in
  March–April 2026; the demo building assumed about 19 600 kWh a year.
  Calibrated buildings have none.

**The fitted building** `twin/fixtures/calibrated/site-001-v1.json` (heat pump,
as at the site) and its district-heated twin `site-001-dh-v1.json` (used only
to read the heating signature cleanly): 1545 m², from the medium-office
certificate profile, with three parameters fitted.

| Target | Measured | Twin | |
|---|---:|---:|---:|
| Heating signature slope | 1549 W/K | 1624 W/K | +5 % |
| Night base load | 12.5 kW | 12.5 kW | 0 % |
| Weekday daytime | 29.9 kW | 29.9 kW | 0 % |
| Weekend / weekday | 0.71 | 0.67 | −5 % |
| Electricity over 5963 hours, NMBE | | | 1.0 % |
| Hourly CV(RMSE) (Guideline 14: ≤ 30 %) | | | 39.7 % |
| Monthly CV(RMSE) (Guideline 14: ≤ 15 %) | | | 19.8 % |

**Not calibrated yet.** The total is right but the seasons are not: the
twin uses 18–20 % too little electricity in February–March and
September–October and 15–43 % too much in June–August. The likely causes are
the heat pump's winter efficiency and the cooling plant's summer
electricity, and they cannot be fitted honestly until the meter topology is
known (whether the heat pump and the chiller are on the two top-level
meters). The heating signature's balance temperature and base are not
identified (2 of 51 complete heating days above 11 °C). Savings computed on
this building are the size of the levers on a building like it, not a
verified saving for it.

## What it still does not establish

A measured calibration needs time series: outdoor and indoor temperature,
heating power, AHU operation, airflow, setpoints, occupancy, solar and
metered energy (the sheet's `MÅLEBEHOV`), with a held-out validation period
and MAE/RMSE on temperature and energy. A pilot building's meters are the
next source, not more certificates. Until then every savings figure is a
property of a building built from priors.
