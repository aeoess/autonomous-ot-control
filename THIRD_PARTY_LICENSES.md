# Licensing and third-party material

> **Read this before you reuse anything from this repository.**
>
> The code and documents written for this repository are licensed under
> **Apache-2.0** ([LICENSE](LICENSE), [NOTICE](NOTICE)). That grant stops at
> the boundary of what this repository's authors wrote. **REMORA and its SDK,
> third-party packages and images, external data, Statsbygg's code lists,
> energy-certificate data, and code contributed by other projects keep their
> own licences and terms.** Nothing in this repository relicenses them, and a
> user of this repository needs those terms separately.

## 1. What Apache-2.0 covers

| Covered (Apache-2.0, this repository's authors and contributors) |
|---|
| `twin/`, `adapter/`, `runner/`, `deploy/` (including `deploy/ot/`, the modules REMORA loads), `tests/`, `scripts/`, `federation/` except where a subdirectory states its own licence, `contracts/`, `scenarios/*.json` scenario definitions and `scenarios/episodes/`, `docs/` written for this repository, `.github/` |
| The sealed fixture sets `federation/fixtures/v1` and `v2` and their manifests (the decision trails were recorded on this repository's simulator) |
| Run records and reviewed reports under `federation/records/` and `federation/reports.json`, as data (see section 5 on who authored them) |

Contributions are accepted under the same licence (Apache-2.0 section 5):
a pull request is a contribution under Apache-2.0 unless the files it adds
state another OSI-approved licence and stay in their own directory.

## 2. REMORA and its SDK: **not covered**

| | |
|---|---|
| Project | REMORA (REMORA-research), https://github.com/darklordVirtual/REMORA-research |
| Licence | **Business Source License 1.1**, with REMORA's own Research Grant; see the upstream `LICENSE` and `legal/LICENSING.md` |
| In this repository | **Not included.** `upstream/remora.lock.json` pins the commit; `deploy/Dockerfile.remora-ot` and `deploy/Dockerfile.governed` fetch it at build time; CI checks it out for the SDK tests |
| Surfaces used | `remora.sdk` (client, effect verification), the `REMORA_TOOL_REGISTRY_MODULE` deployment hook |
| What that means for you | Building or running the governed path uses REMORA under **REMORA's** licence. This repository's Apache-2.0 grant gives no right in REMORA. Code here that calls the SDK is Apache-2.0; the SDK it calls is not. |

## 3. Python packages

Pinned with hashes in `requirements.lock` (runtime), `requirements-dev.lock`
(tests) and `deploy/ot/requirements-adapter.lock` (the subset installed into
the REMORA image). Installed from PyPI at build time, **not vendored**.

| Package | Version | Licence | Used for |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | runtime (FastAPI) |
| annotated-types | 0.8.0 | MIT | runtime (pydantic) |
| anyio | 4.15.1 | MIT | runtime (httpx, Starlette) |
| certifi | 2026.10.23 | **MPL-2.0** (file-level copyleft; unmodified use) | runtime (TLS roots) |
| click | 8.5.0 | BSD-3-Clause | runtime (uvicorn) |
| fastapi | 0.162.0 | MIT | runtime (twin APIs) |
| h11 | 0.16.0 | MIT | runtime |
| httpcore | 1.0.9 | BSD-3-Clause | runtime |
| httpx | 0.28.1 | BSD-3-Clause | runtime (clients, observer, adapter) |
| idna | 3.20 | BSD-3-Clause | runtime |
| opentelemetry-api | 1.45.1 | Apache-2.0 | runtime (transitive) |
| pydantic | 2.14.1 | MIT | runtime |
| pydantic-core | 2.50.1 | MIT | runtime |
| starlette | 1.10.0 | BSD-3-Clause | runtime |
| typing-extensions | 4.16.0 | PSF-2.0 | runtime |
| typing-inspection | 0.4.4 | MIT | runtime |
| uvicorn | 0.54.0 | BSD-3-Clause | runtime (servers) |
| attrs | 26.1.0 | MIT | tests (jsonschema) |
| iniconfig | 2.3.1 | MIT | tests |
| jsonschema | 4.26.0 | MIT | tests, contract validation |
| jsonschema-specifications | 2025.9.1 | MIT | tests |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | tests |
| pluggy | 1.7.0 | MIT | tests |
| pygments | 2.21.0 | BSD-2-Clause | tests |
| pytest | 9.1.1 | MIT | tests |
| referencing | 0.37.0 | MIT | tests |
| rpds-py | 2026.9.1 | MIT | tests |

Licences read from each package's published metadata. Optional local tools
used to read the calibration sources (`openpyxl`, MIT; `pypdf`, BSD-3-Clause)
are not dependencies of the repository.

The federation edge `agent-evidence-vectors-axes` declares its own package,
`agent-evidence-vectors==0.28.0` (**Apache-2.0**, Sankalp Gilda), installed
only inside that edge's sandbox.

## 4. Container images and CI

| Item | Licence | Use |
|---|---|---|
| `python:3.12-slim` (Docker Official Image) | Python: PSF-2.0; Debian base: various free licences per package | base of every image built here |
| `postgres:16-alpine` (Docker Official Image) | PostgreSQL Licence; Alpine: various | REMORA's store in the governed stack |
| GitHub Actions `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4` | MIT | CI only |

Images are pulled at build time and not redistributed by this repository.
If you publish images built from these Dockerfiles, the base images' and
REMORA's licences apply to those images.

## 5. Contributions from other projects

| Path | Author | Licence | Notes |
|---|---|---|---|
| `federation/edges/agent-evidence-vectors-axes/` | Sankalp Gilda (Probity, @astrogilda), PR #22 | **Apache-2.0**; `invocation_axes_reader.py` is an unchanged copy from `agent-evidence-vectors` 0.28.0, with its licence file `LICENSE.agent-evidence-vectors` | Copyright stays with the author. The historical run record says MIT; that field is a known metadata error, described in the edge's `run.md`. |
| `federation/records/agent-evidence-vectors-axes/` | Probity | data, as submitted | an external run record |
| `federation/records/template-reference-verifier/20261027T201255Z.json` | argentum-core (@giskard09), PR #25 | contributed under Apache-2.0 via the pull request | authorship preserved in Git |
| `federation/reports.json`, Altru.dev / Frequency row | the report's author | **the report itself is not in this repository**; the row records its published result and links to it | the linked report keeps its author's terms |

Each contributor keeps the copyright in their contribution. Verification is
not endorsement in either direction, and listing a contribution here grants
no right in the contributor's own project.

## 6. Data and documents

| Material | Where | Source and terms | Attribution required |
|---|---|---|---|
| Hourly weather (temperature, shortwave, humidity, precipitation) | `scenarios/data/**/outdoor-*`, `solar-*`, `humidity-*`, `precipitation-*` | Open-Meteo historical API, ERA5 reanalysis. Open-Meteo data: **CC BY 4.0**. ERA5: Copernicus Climate Change Service, Copernicus licence. | "Weather data by Open-Meteo.com (CC BY 4.0)"; "Contains modified Copernicus Climate Change Service information" |
| Day-ahead prices NO1–NO5 | `scenarios/data/**/price-*` | hvakosterstrommen.no, which republishes ENTSO-E / Nord Pool day-ahead results. Its API is free to use and asks for attribution; the rights in the underlying market data stay with their publishers. **Not relicensed here**; check those terms before redistributing the series. | "Prices from hvakosterstrommen.no" |
| TFM code lists (PA 0802) | `twin/tfm_register.json`, `docs/tfm.md` | **Statsbygg**, PA 0802 and appendices 9.1 and 9.2 (URLs and digests in the file). The register is an index of codes, names and comments as published; Statsbygg holds the rights in PA 0802 and the PDFs are the authority. **Not covered by Apache-2.0.** The PDFs themselves are not in the repository (`.gitignore`). | Statsbygg, PA 0802 |
| Energy-certificate priors | `docs/kalibreringsgrunnlag_ot_anonymisert.json`, `docs/Enova_OT_kalibreringsark_2026.xlsx` | Derived and anonymised from **Enova**'s published energy-certificate data (2015, 2016, 2025 exports). The terms of Enova's data apply to the underlying values; **not relicensed here**. Calculated values, not measurements. | Enova energimerking |
| One building's certificate values | `scenarios/calibration/site-001.json` (`certificate`), `twin/fixtures/calibrated/site-001-*.json` | Technical values from that building's public energy certificate (Enova). No address, cadastral, building or certificate number is included. | Enova energimerking |
| SITE_001 meter aggregates | `scenarios/calibration/site-001.json` | Aggregates of one anonymised building's meters, **published with the owner's consent** for reproducing this repository's calibration. The hourly export is **not** in the repository (`.gitignore`). **Not covered by Apache-2.0**; do not use it to identify the building. | — |
| Research referenced in the calibration sheet | cited only | theses and reports named in the sheet's `Forskning_rapporter` | — |

## 7. What is deliberately not in the repository

- REMORA's source (fetched at build time, BUSL-1.1).
- The building owner's hourly meter export (`docs/ot_hourly_*`, gitignored).
- Statsbygg's PA 0802 PDFs (`docs/TFM/`, gitignored).
- Secrets and signing keys (`.env`, `deploy/.keys/`, `deploy/.generated/`, gitignored).

## 8. Questions

Open an issue on this repository. For REMORA's licence, see REMORA's own
repository; for a contributed edge, ask its maintainer.
