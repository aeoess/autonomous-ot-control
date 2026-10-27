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

## What it still does not establish

A measured calibration needs time series: outdoor and indoor temperature,
heating power, AHU operation, airflow, setpoints, occupancy, solar and
metered energy (the sheet's `MÅLEBEHOV`), with a held-out validation period
and MAE/RMSE on temperature and energy. A pilot building's meters are the
next source, not more certificates. Until then every savings figure is a
property of a building built from priors.
