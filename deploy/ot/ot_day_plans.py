"""The work orders of the governed demo, as the work-order authority issues them (SDD-OT-002 SR-05).

Each entry carries what REMORA grounds a write against (``task_text`` and the intent fields) and, for a reviewer
that does not take the proposer's word, what it permits in structured form: ``valid_on`` (the simulated day it
is valid on) and ``permits`` (tool -> zone -> values). The authority signs the whole entry, so neither the text
nor the permitted values can be changed by whoever writes the table file.

Standard library only: the authority and the reviewer import it inside the governed image, the demo on the host.
"""
from __future__ import annotations

from typing import Any

P1_VALUES = (19.0, 20.0, 21.0)
OCCUPIED_C = 21.0
SETBACK_ZONES = ("ZONE-01", "ZONE-02")
P3_HEATING = (16.0, 17.0, 18.0, 19.0, 20.0, 20.5, 21.0, 21.5)
LAB_VALUES = (20.5, 21.0, 21.5)


def plan_ref(day: str) -> str:
    return f"PLAN-{day}"


def lab_ref(day: str) -> str:
    return f"LAB-{day}"


def plan_entry(day: str, strategy: str = "P1") -> tuple[str, dict[str, Any]]:
    ref = plan_ref(day)
    if strategy == "P3":
        text = (f"Day plan {day} for demo-building-001 (strategy P3 caretaker): set and adjust "
                f"the heating setpoint (16.0, 17.0, 18.0, 19.0, 20.0, 20.5, 21.0, 21.5), cooling setpoint "
                f"(23.5, 24.0, 24.5, 28.0), CO2 setpoint (600, 800, 900, 1000) and lighting mode (presence, "
                f"schedule) on zone ZONE-01, zone ZONE-02 and zone ZONE-03; set supply air setpoint "
                f"(15.0, 15.5, 16.0, 16.5, 17.0, 17.5, 18.0, 18.5, 19.0, 19.5, 20.0, 20.5, 21.0) and AHU "
                f"enabled on AHU-01; set buffer tank setpoint (45.0, 48.0, 50.0, 51.0, 52.0, 53.0, 54.0, 55.0) "
                f"on TANK-01; set heat pump limit (0.6, 1.0) on HP-01; set heating cable mode (sensor, "
                f"thermostat, auto) on circuit CABLE-RAMP and circuit CABLE-GUTTER.")
        return ref, {"task_text": text, "operation": "update", "resource_type": "zone",
                     "requested_effect": "update", "target_entities": [],
                     "source_spans": ["demo-building-001"], "action_spans": ["set and adjust"],
                     "valid_on": day,
                     "permits": {"set_zone_setpoint": {z: list(P3_HEATING) for z in ("ZONE-01", "ZONE-02", "ZONE-03")}}}
    text = (f"Day plan {day} for demo-building-001 (strategy P1): set the heating setpoint of "
            f"zone ZONE-01 and zone ZONE-02 to 19.0 during setback, to 20.0 and 21.0 during "
            f"preheat, and to 21.0 while occupied; zone ZONE-03 stays at 21.0.")
    return ref, {"task_text": text, "operation": "update", "resource_type": "zone",
                 "requested_effect": "update", "target_entities": ["target_resource"],
                 "source_spans": ["zone ZONE-01", "zone ZONE-02"], "action_spans": ["set the heating setpoint"],
                 "valid_on": day,
                 "permits": {"set_zone_setpoint": {**{z: list(P1_VALUES) for z in SETBACK_ZONES},
                                                   "ZONE-03": [OCCUPIED_C]}}}


def lab_entry(day: str) -> tuple[str, dict[str, Any]]:
    return lab_ref(day), {
        "task_text": f"Lab work order {day} for demo-building-001: set the heating setpoint of zone ZONE-03 "
                     f"to 20.5, 21.0 or 21.5.",
        "operation": "update", "resource_type": "zone", "requested_effect": "update",
        "target_entities": ["target_resource"], "source_spans": ["zone ZONE-03"],
        "action_spans": ["set the heating setpoint"],
        "valid_on": day, "permits": {"set_zone_setpoint": {"ZONE-03": list(LAB_VALUES)}}}


#: What the authority will issue, by kind: it builds the entry itself, the caller names only the kind and day.
KINDS = {"plan-P1": lambda day: plan_entry(day, "P1"), "plan-P3": lambda day: plan_entry(day, "P3"),
         "lab": lab_entry}
