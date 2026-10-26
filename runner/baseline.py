"""Rule-based baseline controller (non-agent reference).

Represents the building's own fixed schedule: 21 °C during occupancy with a
one-hour preheat, 20 °C setback otherwise. It drives an *in-process* twin and
passes every change through the same local guard as any other write. It is a
comparison reference only and never runs inside the governed deployment, so it
holds no actuator credential and opens no route to the actuator listener.

  python -m runner.baseline --scenario scenarios/baseline-day-v1.json --out artifacts
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from runner.evidence import write_bundle
from runner.scoring import score
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

OCCUPIED_SP = 21.0
SETBACK_SP = 20.0
PREHEAT_S = 3600


def target_setpoint(twin: Twin, t_s: int) -> float:
    occupied_soon = twin.scenario.occupied(t_s) or twin.scenario.occupied(t_s + PREHEAT_S)
    return OCCUPIED_SP if occupied_soon else SETBACK_SP


def run_baseline(config: dict[str, Any], scenario_path: str | Path) -> Twin:
    twin = Twin(config, load_scenario(scenario_path), record_trace=True)
    ctrl = int(config["controller_step_seconds"])
    while not twin.ended:
        t = twin.clock.now_s
        target = target_setpoint(twin, t)
        for zid in sorted(twin.zones):
            if abs(twin.zones[zid].setpoint_c - target) > 1e-9:
                twin.apply_setpoint({"operation_id": f"baseline-{t:08d}-{zid}", "zone_id": zid,
                                     "setpoint_c": target, "units": "degC",
                                     "expected_state_revision": twin.revision})
        twin.advance(min(ctrl, twin.scenario.duration_seconds - t))
    return twin


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.baseline")
    p.add_argument("--config", default="twin/fixtures/three-zone-v1.json")
    p.add_argument("--scenario", default="scenarios/baseline-day-v1.json")
    p.add_argument("--out", default="artifacts")
    p.add_argument("--run-id", required=True)
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    twin = run_baseline(cfg, a.scenario)
    metrics = score(twin)
    root = write_bundle(
        a.out, suite_id="baseline-v1", run_id=a.run_id,
        manifest_extra={"seed": twin.scenario.seed, "scenario_id": twin.scenario.scenario_id,
                        "scenario_digest": twin.scenario.digest, "config_digest": twin.config_digest,
                        "sim_range": {"start_utc": twin.scenario.start_utc,
                                      "duration_seconds": twin.scenario.duration_seconds},
                        "controller": "rule-baseline-v1"},
        metrics=metrics, trace=twin.trace,
        claims={"deterministic_twin": "TESTED_LOCALLY", "rule_baseline": "IMPLEMENTED",
                "remora_governed_dispatch": "NOT_ESTABLISHED",
                "real_building_savings": "NOT_ESTABLISHED"})
    print(root)
    print(metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
