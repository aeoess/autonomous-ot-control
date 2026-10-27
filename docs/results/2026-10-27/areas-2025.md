# Five Price Areas, 2025

Seeds 177, 178, 179. Strategies: P0, P1, P2, P3. Scope: Synthetic, uncalibrated building-services twin (three-zone-hvac-seasonal-v2) at fictitious sites in each Norwegian price area. Verified 2025 day-ahead prices per area and local ERA5 weather. Cost = spot + a declared reference grid tariff (ASSUMPTION; taxes excluded). Strategies applied directly (offline); not a real-building saving.

## How to read this report

One synthetic three-zone office building with its own heat pump, hot-water tank, air-handling unit, lighting and heated entrance ramp is placed at a fictitious site in each Norwegian price area. The same building runs the full year 2025 once per strategy and seed, on that area's verified day-ahead prices and ERA5 weather. Every strategy is applied directly to the twin, offline: no REMORA, no governance in the loop. The governed runs are reported separately.

### The strategies

| Strategy | What it is |
|---|---|
| **P0** | As found. Fixed 21 °C occupied (1 h preheat) and 20 °C otherwise, the air-handling unit on a fixed weekday schedule, lighting on schedule, heating cables on an air thermostat, the tank at 50 °C with no cap. The baseline every saving is measured against. |
| **P1** | Classic night setback. P0 plus setback to 19 °C outside occupancy and a self-tuning optimal start that learns how long the building takes to reheat. |
| **P2** | Rule-based caretaker. P1 plus a weekend and holiday setback chosen by the outdoor mean, occupancy-aware ventilation, presence lighting with daylight dimming, ground-sensor control of the heating cables, and the tank following the radiator curve with a cheap-hour boost and a peak limiter. |
| **P3** | The digital caretaker (ADR-009). Safety overlays (frost, air quality, icing, degraded telemetry), one active comfort profile, one named algorithm per setpoint, an independent validator against the site policy, a hash-pinned sensor-free fallback, and fault detection on the zone sensors. Every change is a governed tool call with its decision record. |

### The numbers

| Column | Meaning |
|---|---|
| NOK | cost for the year: Nord Pool day-ahead spot for the area plus a declared reference grid tariff (energy part and a monthly power charge on the highest hourly kW). Taxes excluded. |
| saves NOK (min..max) | the saving against P0, mean over the seeds, with the smallest and largest seed in brackets. The seeds vary occupancy and the sensor faults, not the weather or the prices. |
| % | the saving as a share of P0's cost in the same area. |
| kWh % | the share of P0's energy saved; it differs from the NOK share because prices vary by hour. |
| spot NOK/kWh | the energy-weighted average spot price P0 paid in the area. |
| comfort dh | degree-hours outside the occupied comfort band, summed over the three zones. One zone one degree too warm for one hour is one degree-hour. Lower is better. |
| icing h | hours in which the ground sensor reported a wet surface at −10..+1 °C while the heating cable was off: an icing hazard on the ramp. Lower is better. |
| CO2>1000 ppm·h | ppm-hours above 1000 ppm while occupied, summed over the zones: how far and how long the air quality limit was exceeded. Lower is better. |
| tank>60 °C h | hours the hot-water tank stood above 60 °C, beyond the heat pump's supply limit: wasted heat and a safety limit in the site policy. Zero is the target. |
| submitted / held / fallback / escalated | how the caretaker routed each control step: proposed a change, held the current value, switched to the sensor-free fallback, or escalated to a person. |
| optimizer faults injected / caught | deliberately corrupted proposals fed to the caretaker, and how many the validator refused. |
| FDD alarms / true / fault episodes detected of | fault-detection alarms raised, how many pointed at a real seeded sensor fault, and how many of the seeded fault episodes were detected at all. |

**What this does and does not show.** The savings are properties of the simulated building: real levers, synthetic plant. They show which levers matter and by how much relative to each other, and what the validator is worth when sensors lie. They are not a real-building saving, and a real site must be measured before any figure here means anything there.

## What drives the number

The headline saving of P3 is dominated by one lever: the heating cables. As found (P0) they run on an air thermostat and heat the ramp and gutter through most of the winter; with the ground moisture sensor they run only while the surface is wet and freezing. That share rests on three stated assumptions of the twin, none of them measured: the cable rating (9 kW installed for a 260 m² building), the as-found control (air thermostat −8..+3 °C, the practice in older installations), and the surface moisture model. **Treat the figure without the cable lever as the headline until a real site has been measured.**

| Area | P3 saves (NOK %) | of which the cable lever (kWh, share of saved kWh) | HVAC and lighting levers alone (kWh %) | cost saving without the cable lever (estimate, %) |
|---|---:|---:|---:|---:|
| NO1 Oslo | 41.9 | 18 605 kWh (85 %) | **12.3** | 15.6 |
| NO2 Kristiansand | 39.9 | 13 619 kWh (79 %) | **13.9** | 17.7 |
| NO3 Trondheim | 33.8 | 18 865 kWh (85 %) | **12.0** | 15.4 |
| NO4 Tromsø | 25.5 | 24 446 kWh (90 %) | **8.7** | 13.9 |
| NO5 Bergen | 39.8 | 15 771 kWh (83 %) | **12.1** | 17.7 |

The cost estimate without the cable lever subtracts the cable kWh at the area's P0-weighted spot price; the grid power part depends on monthly peaks and is not attributable per consumer, so this column is an estimate, the kWh column is exact.

**Reasonableness check, P0 energy intensity (260 m² floor area).** Whole-building figures for a small Norwegian office typically fall in the low hundreds of kWh/m²·year; what matters here is the composition, which is an assumption of the twin, not a measurement.

| Area | P0 kWh/m² total | heating cables | equipment (uncontrollable base load) | HVAC, lighting, rest |
|---|---:|---:|---:|---:|
| NO1 | 182 | 76 | 54 | 52 |
| NO2 | 155 | 55 | 54 | 46 |
| NO3 | 188 | 83 | 54 | 51 |
| NO4 | 250 | 132 | 54 | 65 |
| NO5 | 168 | 65 | 54 | 49 |

A cable share of roughly 40 % of a building's electricity is high; it follows from the installed rating and the thermostat baseline, both assumptions. The equipment base load is constant in every strategy and dilutes every percentage.

## Year

| Area | P0 NOK | P0 kWh | Spot NOK/kWh (P0) | P1 saves NOK (min..max) | P1 % | P1 kWh % | P2 saves NOK (min..max) | P2 % | P2 kWh % | P3 saves NOK (min..max) | P3 % | P3 kWh % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 Oslo | 56 127 | 47 331 | 0.79 | 406 (297..595) | 0.7 | 0.1 | 23 162 (23 008..23 282) | 41.3 | 46.5 | 23 517 (23 412..23 589) | 41.9 | 46.5 |
| NO2 Kristiansand | 53 103 | 40 410 | 0.87 | 420 (248..711) | 0.8 | 0.1 | 20 706 (20 573..20 775) | 39.0 | 42.6 | 21 208 (21 050..21 387) | 39.9 | 42.7 |
| NO3 Trondheim | 37 092 | 48 840 | 0.36 | 242 (196..303) | 0.7 | 0.1 | 12 565 (12 398..12 763) | 33.9 | 45.3 | 12 539 (12 435..12 738) | 33.8 | 45.3 |
| NO4 Tromsø | 29 997 | 65 101 | 0.14 | 109 (-123..280) | 0.4 | 0.1 | 7 663 (7 447..7 908) | 25.5 | 41.7 | 7 637 (7 324..8 044) | 25.5 | 41.7 |
| NO5 Bergen | 48 704 | 43 629 | 0.68 | 404 (276..496) | 0.8 | 0.1 | 18 803 (18 585..19 138) | 38.6 | 43.5 | 19 384 (18 945..19 886) | 39.8 | 43.6 |

Largest saving in NOK with P3: **NO1 Oslo**. Largest saving in kWh: **NO4 Tromsø**. The two differ because the spot price differs by area: a saved kWh in the north is worth a fraction of one in the south.

## Where the P3 saving comes from

kWh saved against P0, by consumer, and the cost saved by tariff part (NOK).

| Area | heating cables | heat pump | backup heater | lighting | fans | cooling | equipment | spot | grid energy | grid power | kWh % without the cable lever |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 | 18 605 | 392 | -9 | 2 646 | 39 | 339 | 0 | 18 644 | 1 321 | 3 552 | 12.3 |
| NO2 | 13 619 | 521 | -10 | 2 736 | 48 | 331 | 0 | 16 361 | 1 035 | 3 812 | 13.9 |
| NO3 | 18 865 | 651 | -1 | 2 282 | 75 | 257 | 0 | 8 872 | 1 328 | 2 339 | 12.0 |
| NO4 | 24 446 | 375 | -0 | 2 013 | 91 | 193 | 0 | 4 265 | 1 627 | 1 745 | 8.7 |
| NO5 | 15 771 | 625 | -9 | 2 285 | 82 | 266 | 0 | 14 399 | 1 141 | 3 845 | 12.1 |

The heating cables dominate. As found they run on an air thermostat (-8..+3 degC), so they heat a dry ramp for most of the winter; on the ground sensor they run only while the surface is wet and freezing. That share rests on the twin's assumed cable ratings and the surface model, and a real site must be measured before the figure means anything. The last column shows the rest of the levers alone.

## Comfort and safety

| Area | P0 comfort dh | P0 icing h | P0 CO2>1000 ppm·h | P0 tank>60 °C h | P1 comfort dh | P1 icing h | P1 CO2>1000 ppm·h | P1 tank>60 °C h | P2 comfort dh | P2 icing h | P2 CO2>1000 ppm·h | P2 tank>60 °C h | P3 comfort dh | P3 icing h | P3 CO2>1000 ppm·h | P3 tank>60 °C h |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 | 2170 | 8 | 0 | 0 | 2173 | 8 | 0 | 0 | 1211 | 0 | 0 | 0 | 970 | 0 | 0 | 0 |
| NO2 | 2124 | 0 | 0 | 0 | 2132 | 0 | 0 | 0 | 1141 | 0 | 0 | 0 | 925 | 0 | 0 | 0 |
| NO3 | 1340 | 0 | 0 | 0 | 1342 | 0 | 0 | 0 | 731 | 0 | 0 | 0 | 623 | 0 | 0 | 0 |
| NO4 | 828 | 55 | 0 | 0 | 828 | 55 | 0 | 0 | 391 | 0 | 0 | 0 | 319 | 0 | 0 | 0 |
| NO5 | 1281 | 0 | 0 | 0 | 1286 | 0 | 0 | 0 | 693 | 0 | 0 | 0 | 613 | 0 | 0 | 0 |

Comfort is degree-hours outside the occupied band, summed over three zones. The icing hazard is hours with the ground sensor reporting a wet surface at -10..+1 degC while a cable was off.

Almost all the comfort deviation is summer overheating (May to September), not cold rooms. The strategies that light only occupied rooms and dim by daylight put less heat into the zones, so they overheat less. Winter comfort stays within a few degree-hours in every strategy.

## The caretaker's own record (P3 and P3raw)

Controller steps by route, optimizer faults injected and caught by the validator, and FDD alarms scored against the injected sensor faults. Means over the seeds.

| Area | Arm | submitted | held | fallback | escalated | optimizer faults injected | caught | FDD alarms | true | fault episodes detected | of | purge steps |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 | P3 | 7540 | 27500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2482 |
| NO2 | P3 | 7368 | 27672 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2219 |
| NO3 | P3 | 6853 | 28187 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1419 |
| NO4 | P3 | 7478 | 27562 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 945 |
| NO5 | P3 | 6799 | 28241 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1214 |

## By half

| Area | Period | P0 NOK | P1 saves NOK | P1 % | P2 saves NOK | P2 % | P3 saves NOK | P3 % | P0 peak kW |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 | 2025-H1 | 31 086 | 328 | 1.1 | 13 164 | 42.3 | 13 376 | 43.0 | 0.0 |
| NO1 | 2025-H2 | 25 041 | 78 | 0.3 | 9 997 | 39.9 | 10 141 | 40.5 | 0.0 |
| NO2 | 2025-H1 | 30 204 | 223 | 0.7 | 12 283 | 40.7 | 12 814 | 42.4 | 0.0 |
| NO2 | 2025-H2 | 22 899 | 197 | 0.9 | 8 423 | 36.8 | 8 394 | 36.7 | 0.0 |
| NO3 | 2025-H1 | 18 424 | 37 | 0.2 | 6 359 | 34.5 | 6 389 | 34.7 | 0.0 |
| NO3 | 2025-H2 | 18 669 | 204 | 1.1 | 6 206 | 33.2 | 6 149 | 32.9 | 0.0 |
| NO4 | 2025-H1 | 14 050 | 83 | 0.6 | 2 824 | 20.1 | 2 989 | 21.3 | 0.0 |
| NO4 | 2025-H2 | 15 946 | 26 | 0.2 | 4 839 | 30.3 | 4 647 | 29.1 | 0.0 |
| NO5 | 2025-H1 | 25 992 | 221 | 0.9 | 10 065 | 38.7 | 10 504 | 40.4 | 0.0 |
| NO5 | 2025-H2 | 22 712 | 182 | 0.8 | 8 738 | 38.5 | 8 880 | 39.1 | 0.0 |

## By quarter

| Area | Period | P0 NOK | P1 saves NOK | P1 % | P2 saves NOK | P2 % | P3 saves NOK | P3 % | P0 peak kW |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 | 2025-Q1 | 24 895 | 304 | 1.2 | 11 436 | 45.9 | 11 631 | 46.7 | 0.0 |
| NO1 | 2025-Q2 | 6 191 | 24 | 0.4 | 1 728 | 27.9 | 1 745 | 28.2 | 0.0 |
| NO1 | 2025-Q3 | 5 665 | 2 | 0.0 | 946 | 16.7 | 909 | 16.0 | 0.0 |
| NO1 | 2025-Q4 | 19 376 | 76 | 0.4 | 9 051 | 46.7 | 9 232 | 47.6 | 0.0 |
| NO2 | 2025-Q1 | 23 911 | 175 | 0.7 | 10 645 | 44.5 | 11 090 | 46.4 | 0.0 |
| NO2 | 2025-Q2 | 6 292 | 48 | 0.8 | 1 638 | 26.0 | 1 724 | 27.4 | 0.0 |
| NO2 | 2025-Q3 | 6 442 | 1 | 0.0 | 1 102 | 17.1 | 1 042 | 16.2 | 0.0 |
| NO2 | 2025-Q4 | 16 457 | 196 | 1.2 | 7 322 | 44.5 | 7 353 | 44.7 | 0.0 |
| NO3 | 2025-Q1 | 14 775 | 65 | 0.4 | 5 257 | 35.6 | 5 276 | 35.7 | 0.0 |
| NO3 | 2025-Q2 | 3 648 | -28 | -0.8 | 1 102 | 30.2 | 1 113 | 30.5 | 0.0 |
| NO3 | 2025-Q3 | 3 105 | 15 | 0.5 | 523 | 16.9 | 483 | 15.6 | 0.0 |
| NO3 | 2025-Q4 | 15 563 | 189 | 1.2 | 5 683 | 36.5 | 5 666 | 36.4 | 0.0 |
| NO4 | 2025-Q1 | 10 312 | 62 | 0.6 | 1 857 | 18.0 | 1 952 | 18.9 | 0.0 |
| NO4 | 2025-Q2 | 3 738 | 21 | 0.6 | 967 | 25.9 | 1 037 | 27.7 | 0.0 |
| NO4 | 2025-Q3 | 2 525 | -41 | -1.6 | 432 | 17.1 | 369 | 14.6 | 0.0 |
| NO4 | 2025-Q4 | 13 422 | 67 | 0.5 | 4 407 | 32.8 | 4 278 | 31.9 | 0.0 |
| NO5 | 2025-Q1 | 20 333 | 232 | 1.1 | 8 604 | 42.3 | 8 866 | 43.6 | 0.0 |
| NO5 | 2025-Q2 | 5 659 | -10 | -0.2 | 1 461 | 25.8 | 1 639 | 29.0 | 0.0 |
| NO5 | 2025-Q3 | 4 577 | -6 | -0.1 | 783 | 17.1 | 817 | 17.9 | 0.0 |
| NO5 | 2025-Q4 | 18 134 | 188 | 1.0 | 7 955 | 43.9 | 8 063 | 44.5 | 0.0 |

## By month

| Area | Period | P0 NOK | P1 saves NOK | P1 % | P2 saves NOK | P2 % | P3 saves NOK | P3 % | P0 peak kW |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NO1 | 2025-01 | 9 572 | 130 | 1.4 | 4 162 | 43.5 | 4 191 | 43.8 | 21.4 |
| NO1 | 2025-02 | 9 611 | 40 | 0.4 | 4 559 | 47.4 | 4 689 | 48.8 | 21.0 |
| NO1 | 2025-03 | 5 712 | 134 | 2.3 | 2 715 | 47.5 | 2 751 | 48.2 | 21.0 |
| NO1 | 2025-04 | 2 532 | -8 | -0.3 | 975 | 38.5 | 1 015 | 40.1 | 16.2 |
| NO1 | 2025-05 | 2 175 | 25 | 1.1 | 531 | 24.4 | 552 | 25.4 | 15.0 |
| NO1 | 2025-06 | 1 484 | 7 | 0.5 | 223 | 15.0 | 178 | 12.0 | 8.6 |
| NO1 | 2025-07 | 1 656 | 0 | 0.0 | 342 | 20.7 | 336 | 20.3 | 8.9 |
| NO1 | 2025-08 | 1 836 | 0 | 0.0 | 344 | 18.7 | 323 | 17.6 | 8.3 |
| NO1 | 2025-09 | 2 174 | 2 | 0.1 | 260 | 12.0 | 250 | 11.5 | 18.6 |
| NO1 | 2025-10 | 3 105 | 1 | 0.0 | 1 178 | 37.9 | 1 189 | 38.3 | 19.8 |
| NO1 | 2025-11 | 8 564 | 41 | 0.5 | 4 138 | 48.3 | 4 174 | 48.7 | 20.8 |
| NO1 | 2025-12 | 7 707 | 34 | 0.4 | 3 735 | 48.5 | 3 869 | 50.2 | 20.4 |
| NO2 | 2025-01 | 8 684 | 93 | 1.1 | 3 628 | 41.8 | 3 688 | 42.5 | 21.4 |
| NO2 | 2025-02 | 9 871 | 55 | 0.6 | 5 136 | 52.0 | 5 346 | 54.2 | 20.9 |
| NO2 | 2025-03 | 5 356 | 27 | 0.5 | 1 880 | 35.1 | 2 056 | 38.4 | 20.2 |
| NO2 | 2025-04 | 2 423 | 15 | 0.6 | 845 | 34.9 | 851 | 35.1 | 16.1 |
| NO2 | 2025-05 | 2 220 | 3 | 0.1 | 560 | 25.2 | 577 | 26.0 | 14.8 |
| NO2 | 2025-06 | 1 649 | 31 | 1.9 | 233 | 14.1 | 295 | 17.9 | 9.8 |
| NO2 | 2025-07 | 2 226 | 0 | 0.0 | 478 | 21.5 | 462 | 20.8 | 8.5 |
| NO2 | 2025-08 | 1 923 | 0 | 0.0 | 346 | 18.0 | 316 | 16.4 | 8.1 |
| NO2 | 2025-09 | 2 293 | 1 | 0.0 | 278 | 12.1 | 263 | 11.5 | 18.5 |
| NO2 | 2025-10 | 2 836 | 119 | 4.2 | 815 | 28.7 | 839 | 29.6 | 20.3 |
| NO2 | 2025-11 | 7 570 | 23 | 0.3 | 3 533 | 46.7 | 3 524 | 46.6 | 21.0 |
| NO2 | 2025-12 | 6 052 | 54 | 0.9 | 2 974 | 49.1 | 2 990 | 49.4 | 19.8 |
| NO3 | 2025-01 | 5 459 | 58 | 1.1 | 1 964 | 36.0 | 1 954 | 35.8 | 21.7 |
| NO3 | 2025-02 | 5 536 | 64 | 1.2 | 2 085 | 37.7 | 2 058 | 37.2 | 21.2 |
| NO3 | 2025-03 | 3 780 | -57 | -1.5 | 1 208 | 32.0 | 1 264 | 33.4 | 20.1 |
| NO3 | 2025-04 | 1 731 | 17 | 1.0 | 628 | 36.3 | 614 | 35.5 | 19.8 |
| NO3 | 2025-05 | 1 130 | -15 | -1.3 | 392 | 34.7 | 391 | 34.6 | 15.0 |
| NO3 | 2025-06 | 787 | -29 | -3.7 | 82 | 10.4 | 108 | 13.8 | 9.3 |
| NO3 | 2025-07 | 710 | 10 | 1.4 | 158 | 22.3 | 115 | 16.1 | 9.2 |
| NO3 | 2025-08 | 1 093 | -0 | -0.0 | 112 | 10.3 | 118 | 10.8 | 18.3 |
| NO3 | 2025-09 | 1 302 | 6 | 0.4 | 253 | 19.4 | 251 | 19.3 | 17.3 |
| NO3 | 2025-10 | 1 985 | 48 | 2.4 | 710 | 35.8 | 710 | 35.8 | 20.2 |
| NO3 | 2025-11 | 6 485 | 101 | 1.6 | 1 983 | 30.6 | 1 988 | 30.7 | 21.0 |
| NO3 | 2025-12 | 7 094 | 39 | 0.6 | 2 990 | 42.2 | 2 968 | 41.8 | 20.8 |
| NO4 | 2025-01 | 3 479 | 2 | 0.1 | 598 | 17.2 | 689 | 19.8 | 21.6 |
| NO4 | 2025-02 | 3 393 | -16 | -0.5 | 689 | 20.3 | 702 | 20.7 | 21.2 |
| NO4 | 2025-03 | 3 440 | 75 | 2.2 | 570 | 16.6 | 562 | 16.3 | 21.5 |
| NO4 | 2025-04 | 1 578 | 30 | 1.9 | 411 | 26.1 | 418 | 26.5 | 20.4 |
| NO4 | 2025-05 | 1 536 | -3 | -0.2 | 475 | 30.9 | 518 | 33.7 | 18.4 |
| NO4 | 2025-06 | 624 | -6 | -0.9 | 81 | 13.0 | 101 | 16.2 | 9.4 |
| NO4 | 2025-07 | 563 | -35 | -6.3 | 134 | 23.7 | 80 | 14.2 | 8.5 |
| NO4 | 2025-08 | 1 013 | 1 | 0.1 | 107 | 10.6 | 102 | 10.1 | 18.6 |
| NO4 | 2025-09 | 948 | -6 | -0.6 | 191 | 20.1 | 187 | 19.7 | 15.6 |
| NO4 | 2025-10 | 1 661 | 27 | 1.6 | 533 | 32.1 | 508 | 30.6 | 20.4 |
| NO4 | 2025-11 | 6 201 | 27 | 0.4 | 2 077 | 33.5 | 2 014 | 32.5 | 22.0 |
| NO4 | 2025-12 | 5 560 | 13 | 0.2 | 1 797 | 32.3 | 1 756 | 31.6 | 21.3 |
| NO5 | 2025-01 | 7 801 | 75 | 1.0 | 2 994 | 38.4 | 2 996 | 38.4 | 21.6 |
| NO5 | 2025-02 | 7 946 | 63 | 0.8 | 3 393 | 42.7 | 3 627 | 45.6 | 20.9 |
| NO5 | 2025-03 | 4 586 | 93 | 2.0 | 2 216 | 48.3 | 2 243 | 48.9 | 20.5 |
| NO5 | 2025-04 | 2 302 | 63 | 2.7 | 915 | 39.7 | 944 | 41.0 | 17.5 |
| NO5 | 2025-05 | 1 960 | -12 | -0.6 | 385 | 19.7 | 519 | 26.5 | 15.3 |
| NO5 | 2025-06 | 1 397 | -61 | -4.4 | 160 | 11.5 | 176 | 12.6 | 10.8 |
| NO5 | 2025-07 | 916 | -17 | -1.8 | 162 | 17.7 | 202 | 22.1 | 8.9 |
| NO5 | 2025-08 | 1 680 | 2 | 0.1 | 262 | 15.6 | 257 | 15.3 | 18.5 |
| NO5 | 2025-09 | 1 982 | 8 | 0.4 | 359 | 18.1 | 358 | 18.1 | 16.3 |
| NO5 | 2025-10 | 2 716 | 119 | 4.4 | 839 | 30.9 | 872 | 32.1 | 19.7 |
| NO5 | 2025-11 | 8 432 | 67 | 0.8 | 3 854 | 45.7 | 4 014 | 47.6 | 20.9 |
| NO5 | 2025-12 | 6 986 | 2 | 0.0 | 3 262 | 46.7 | 3 177 | 45.5 | 20.3 |

