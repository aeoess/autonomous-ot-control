"""Build the machine-readable TFM register from Statsbygg's PA 0802 code lists.

    python scripts/build_tfm_register.py --systems PA-0802-Vedlegg-9.1-Systemkodeliste-rev-5.pdf \
        --components PA-0802-vedlegg-9.2-Komponentkodeliste.pdf --out twin/tfm_register.json

Offline only: it needs PyMuPDF (`fitz`), which is not a runtime or CI
dependency. The output records each source's URL, revision, approval date and
sha256, so a rebuild from the same PDFs is byte-identical and a newer revision
shows up as a diff.

Parsing rules:

- **System codes (Vedlegg 9.1, NS 3451:2022 numbering)** are read from the text
  in reading order. An entry starts at a line that begins with a two- or
  three-digit code. The code must follow the current group (a three-digit code
  starts with the current two-digit group) and must increase; this rejects
  references inside the descriptions ("se 256 og 257"). The TFM comment column
  decides the status:
  - "Systemkode i TFM", "Systemkode for NN ... i TFM" or "Benyttes for et
    reelt, eget system" -> `system_code`;
  - "Skal ikke benyttes" -> `not_used`;
  - otherwise `group` for two-digit codes and `see_comment` for three-digit.
- **Component codes (Vedlegg 9.2)** are read from the tables: code, name,
  examples and synonyms, remarks. A two-letter code with no name is
  unassigned and is kept as such. A one-letter row is a function class.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SOURCES = {
    "systems": {"title": "PA 0802 TFM Vedlegg 9.1 Systemkodeliste",
                "url": "https://dok.statsbygg.no/wp-content/uploads/2025/06/PA-0802-Vedlegg-9.1-Systemkodeliste-rev-5.pdf"},
    "components": {"title": "PA 0802 TFM Vedlegg 9.2 Komponentkodeliste",
                   "url": "https://dok.statsbygg.no/wp-content/uploads/2020/11/PA-0802-vedlegg-9.2-Komponentkodeliste.pdf"},
    "guide": {"title": "PA 0802 Tverrfaglig merkesystem (TFM)",
              "url": "https://dok.statsbygg.no/wp-content/uploads/2024/02/PA-0802-Tverrfaglig-merkesystem-TFM-rev-3.pdf"},
}
BOILERPLATE = re.compile(r"^(SYSTEMKODELISTE|KOMPONENTKODELISTE|PROSJEKTERINGSANVISNING|PA 0802 TFM|Vedlegg 9\.[12]|"
                         r"Bygningsdelsnummer og navn NS3451:2022|Veiledning|NS3451:2022|TFM kommentarer|"
                         r"Godkjent dato:.*|Saksnr:.*|Revisjon:.*|Godkjent dokumenteier:.*|Fag og metodeansvarlig:.*|"
                         r"\d{1,2}\s*)$")


def _meta(text: str) -> dict[str, str]:
    g = lambda pat: (re.search(pat, text) or [None, ""])[1].strip()  # noqa: E731
    return {"revision": g(r"Revisjon:\s*(\S+)") or g(r"REVISJONSNR\s*(\d+)"),
            "approved": g(r"Godkjent dato:\s*([\d.]+)") or g(r"GODKJENT DATO\s*([\d.]+)")}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_systems(text: str) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("=== page") and not BOILERPLATE.match(ln)]
    entries: list[dict[str, Any]] = []
    group = None
    last = 0
    cur: dict[str, Any] | None = None
    for ln in lines:
        m = re.match(r"^(\d{2,3})(?:\s+(.*))?$", ln)
        if m:
            code, rest = m[1], (m[2] or "").strip()
            n = int(code)
            # "325 benyttes ikke i TFM" is a reference inside a group text, not an entry.
            ref = bool(re.match(r"(benyttes|og|se|til|eller)(?:\s|$)", rest))
            ok = not ref and ((len(code) == 2 and n > (int(group) if group else 0))
                              or (len(code) == 3 and group is not None and code[:2] == group and n > last))
            if ok:
                if len(code) == 2:
                    group = code
                last = n if len(code) == 3 else int(code) * 10 - 1
                cur = {"code": code, "level": len(code), "name": rest or None, "text": []}
                entries.append(cur)
                continue
        if cur is not None:
            cur["text"].append(ln)
    out = []
    for e in entries:
        body = " ".join(e.pop("text"))
        body = re.sub(r"\s+", " ", body).strip()
        if "Skal ikke benyttes" in body or (e["name"] or "").startswith("Skal ikke benyttes"):
            status = "not_used"
        elif re.search(r"Systemkode i TFM|Systemkode for |Benyttes for et reelt, eget", body):
            status = "system_code"
        else:
            status = "group" if e["level"] == 2 else "see_comment"
        if e["name"] == "Skal ikke benyttes":
            e["name"] = None
        desc = re.sub(r"\s*(Systemkode i TFM|Skal ikke benyttes i TFM|Skal ikke benyttes)\s*", " ", body).strip()
        out.append({**e, "status": status, "description": desc or None,
                    "tfm_defined": "ikke definert i NS 3451" in body})
    return out


def parse_components(pdf: Path) -> list[dict[str, Any]]:
    import fitz  # offline dependency

    doc = fitz.open(pdf)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    cls = None
    for page in doc:
        for t in page.find_tables().tables:
            for row in t.extract():
                cells = [re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip() for c in row] + ["", "", "", ""]
                code = cells[0]
                if not re.fullmatch(r"[A-ZÆØÅ]{1,2}|X_", code) or code in seen:
                    continue
                seen.add(code)
                if len(code) == 1:
                    cls = code
                    out.append({"code": code, "level": 1, "name": cells[1] or None,
                                "description": cells[2] or None, "remarks": cells[3] or None,
                                "status": "not_used" if re.search(r"Ikke i bruk|Bør ikke brukes", cells[1]) else "class"})
                    continue
                name = cells[1] or None
                out.append({"code": code, "level": 2, "class": cls, "name": name, "examples": cells[2] or None,
                            "remarks": cells[3] or None, "status": "component_code" if name else "unassigned"})
    return out


def main(argv: list[str] | None = None) -> int:
    import fitz  # offline dependency

    p = argparse.ArgumentParser(prog="build_tfm_register")
    p.add_argument("--systems", required=True, type=Path)
    p.add_argument("--components", required=True, type=Path)
    p.add_argument("--guide", type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args(argv)
    text_of = lambda pdf: "\n".join(f"=== page {i + 1} ===\n" + pg.get_text() for i, pg in enumerate(fitz.open(pdf)))  # noqa: E731
    st, ct = text_of(a.systems), text_of(a.components)
    sources = {"systems": {**SOURCES["systems"], **_meta(st), "sha256": _sha(a.systems)},
               "components": {**SOURCES["components"], **_meta(ct), "sha256": _sha(a.components)}}
    if a.guide:
        sources["guide"] = {**SOURCES["guide"], **_meta(text_of(a.guide)), "sha256": _sha(a.guide)}
    reg = {
        "schema": "ot-tfm-register/v1",
        "publisher": "Statsbygg",
        "note": ("Derived from Statsbygg's published PA 0802 code lists, unaltered in content: codes, names and "
                 "comments as published. The PDFs are the authority; this file is an index into them."),
        "sources": sources,
        "id_syntax": {
            "full": "+AAAAAA=NNN.nnn-BBnnnT",
            "location": "+AAAAAA: the building number (Statsbygg: 6 digits), given by the client",
            "system": "=NNN.nnn: system code from Vedlegg 9.1 (three digits) and a running number 001..999",
            "component": "-BBnnn: component code from Vedlegg 9.2 (two letters) and a running number (occurrence)",
            "component_type": "-BBnnnT: the same with a trailing T: the component type (parallel identification)",
        },
        "running_numbers_hvac": {"401-499": "supply or flow (tur, tilluft)", "501-599": "return or extract (retur, avtrekk)",
                                 "601-899": "placed to represent the room", "901-999": "placed to represent outdoor air"},
        "running_numbers_electrical": "UE/KW: the running number is the circuit (kurs) number",
        "systems": parse_systems(st),
        "components": parse_components(a.components),
    }
    a.out.write_text(json.dumps(reg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    s, c = reg["systems"], reg["components"]
    print(f"systems {len(s)} ({sum(x['status'] == 'system_code' for x in s)} system codes), "
          f"components {len(c)} ({sum(x['status'] == 'component_code' for x in c)} component codes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
