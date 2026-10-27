# ADR-010: TFM (Statsbygg PA 0802) as the testbed's identifier system

- Status: Accepted for the simulator. A real site's identifiers still need its approved asset inventory (SDD-OT-001 D-08).
- Date: 2026-10-27
- Related: ADR-007 (plant and data points), ADR-009 (caretaker FDD), SDD-OT-001 (D-08, AT-09)
- Code and data:
  - `twin/tfm.py`;
  - `twin/tfm_register.json`;
  - `scripts/build_tfm_register.py`;
  - `twin/fixtures/tfm-vedlegg-9.3-examples.json`.
- Generated docs: `docs/tfm.md`.

## Context

Norwegian buildings identify every system and component with TFM (Tverrfaglig merkesystem, Statsbygg PA 0802). Before this ADR the twin had only a placeholder per zone, `DEMO=360.001`. That value is not a TFM identifier: the location marker `+` is missing, and system 360 is air handling, not a room.

The owner asked for every TFM code to be mapped so that the system understands TFM. The owner supplied:

- PA 0802 rev. 3 (the rules);
- Vedlegg 9.3 (worked examples).

The two code lists are separate Statsbygg documents. Both were fetched from dok.statsbygg.no:

- Vedlegg 9.1 Systemkodeliste, rev. 5 of 17.02.2026, on NS 3451:2022;
- Vedlegg 9.2 Komponentkodeliste, rev. 3 of 02.02.2024.

## Decision

1. **A register of every code, unaltered** (`twin/tfm_register.json`).
   - **System codes:** 472 entries. 239 are TFM system codes, 59 are groups, and 174 are marked "skal ikke benyttes". The register also flags the codes TFM defines beyond NS 3451: 290, 310, 320, 360, 370, 390, 450, 490, 520, 570, 590, 690 and 790.
   - **Component codes:** 307 assigned two-letter codes in 23 function classes. The 291 unassigned codes are kept, so a typo cannot pass as a code.
   - **Provenance:** the register records each source's URL, revision, approval date and sha256.
   - **Reproducibility:** `scripts/build_tfm_register.py` rebuilds it byte-identically from the same PDFs. It needs PyMuPDF and runs offline, not in CI.
2. **A strict parser and validator** (`twin.tfm`).
   - The syntax is `+location=NNN.nnn-BBnnn[T]`, and the en dash is accepted.
   - A system code must be a TFM system code, and a system needs its running number.
   - A component code must be assigned.
   - `explain` names the system, the group, the component and its class. For HVAC components it adds the position the running number encodes: 4xx supply, 5xx extract, 6xx–8xx room, 9xx outdoor.
3. **Tested against Statsbygg's own examples.** Every identifier in Vedlegg 9.3 is a fixture. 376 of the 381 are valid. The five rejected are:
   - three headings without a running number: `=273`, `=411.XXX` and `=430.XXX` (and 43 is a group);
   - two typos in the source: `=360.001--ST001T` and `=433.003UE001T`.
4. **Every data point names its TFM object.**
   - The 110 points of the twin map to 57 objects of the fictitious building `+DEMO01`, laid out after the Vedlegg 9.3 examples:
     - room sensors and room controllers in the bus system, `=564.001-RT60n` and `-OU60n`;
     - the common outdoor sensor on the heating plant, `=320.001-RT901`;
     - radiator valves on the heating circuit with their actuators in the bus system;
     - the AHU's fans, coils, heat recovery and sensors by position.
   - Each point has a role: `measured`, `setpoint`, `state`, `calculated` (central operations, `=562.001-OF001`) or `input`. The spot price and the equipment factor have no building object.
   - `docs/datapoints.md` and `datapoints-v1.json` carry the TFM.
5. **A zone reading carries its sensor's TFM.**
   - The zone fixtures now use `+DEMO01=564.001-RT60n`.
   - The caretaker rejects a reading whose TFM identifier differs from the expected one, as a mis-mapped point. That zone runs on the fallback and the alarm is escalated, under the separate FDD rule `tfm`.
   - This covers the "TFM mismatch" part of AT-09 offline. The running twin still has no injection hook for AT-09.

## Consequences

- With the matching fixtures, the committed results do not change. A NO1 seed-177 rerun gives the same 9 alarms and the same kWh.
- A new revision of a code list shows up as a diff in the register and in `docs/tfm.md`.
- The PDFs are not committed. The register points to Statsbygg's URLs and pins the sha256 of each.

## Not decided here

- **Real-site identifiers.** The real identifiers of a pilot building come from its approved inventory, not from `OBJECTS` (D-08, gate E).
- **Governed tools addressed by TFM.** Each tool would target a TFM object instead of a zone id. That changes the signed ToolSpec, so it gets its own change.
- **Room numbers and component type registers.** These are outside PA 0802's three main elements. Covering them would need the building's room numbering and FDV type data.
