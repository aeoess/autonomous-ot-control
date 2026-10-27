# Exogenous data snapshots

Written by `python -m runner.exogenous fetch` and checked with
`python -m runner.exogenous verify`; see `docs/adr/ADR-005-site-data-savings-and-decision-audit.md`.
Each file records its source URLs, the SHA-256 of every raw response, the
retrieval time, its attribution and its limitations. Scenarios pin each file by
SHA-256; the twin refuses a file that changed.

| Directory | Site | Area | Span |
|---|---|---|---|
| `demo-building-001/` | fictitious, 63.4182 N 10.4051 E (Trondheim) | NO3 | 2026-01-15 .. 2026-01-21 UTC |

Attribution:

- Electricity prices: hvakosterstrommen.no, which republishes the ENTSO-E /
  Nord Pool day-ahead result per Norwegian price area. Spot price only (no grid
  tariff, VAT or markup).
- Weather: Open-Meteo.com historical weather API (CC BY 4.0), ERA5 reanalysis.

Redistribution terms outside this repository are an open owner decision (D-14).
