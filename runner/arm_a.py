"""Arm A of the federation A/B/C (ADR-004): the ungoverned, known-negative measurement device.

    python -m runner.arm_a --episode scenarios/episodes/<id>.json --out artifacts/<run>-arm-a

The agent holds the actuator and applies every proposal of the episode
directly to the twin, attacks included, choosing its own operation ids. The
only thing between it and the plant is the twin's own interlock. This is
exactly what INV-01/INV-02 forbid in a deployment, which is why it is a
measurement, never an option: its ledger is labelled
``architecture: KNOWN_NEGATIVE`` and nothing in the default compose files or
images refers to it (D-10). It runs offline on the same twin, inputs and
proposal stream as the governed arms, so the arms differ only in what
governs the plant.

Its ledger has the demo's row shape, so `runner.ledger_metrics` accounts it
against the episode's oracle the same way: every refusal is the interlock's,
every applied forbidden proposal is a forbidden effect.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from runner import episode as episode_mod
from runner.scoring import score
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = "KNOWN_NEGATIVE"
#: Episode origins back to the ledger's origin vocabulary (the inverse of episode.ORIGIN_MAP).
ORIGIN_BACK = {v: k for k, v in episode_mod.ORIGIN_MAP.items()}


def run(ep: dict[str, Any]) -> dict[str, Any]:
    twin = Twin(load_config(ROOT / ep["twin_config"]), load_scenario(ROOT / ep["scenario"]))
    step = int(ep["step_seconds"])
    by_t: dict[int, list[dict[str, Any]]] = {}
    for p in ep["proposals"]:
        by_t.setdefault(int(p["at_sim_s"]), []).append(p)
    ledger: list[dict[str, Any]] = []
    rev0 = twin.revision
    n = 0
    for t in range(0, int(ep["simulated_s"]), step):
        for p in by_t.get(t, []):
            args = p["call"]["arguments"]
            zone, value = args.get("zone_id"), args.get("setpoint_c")
            row: dict[str, Any] = {"sim_time_s": t, "origin": ORIGIN_BACK.get(p["origin"], p["origin"]),
                                   "zone_id": zone, "to_c": value, "intent_ref": p.get("intent_ref"),
                                   "episode_proposal": p["id"], "strategy_reason": p.get("strategy_reason"),
                                   "architecture": ARCHITECTURE}
            if p["origin"] == "injected:replay":
                row["replayed_item"] = p.get("replay_of")
            cur = twin.zones[zone].setpoint_c if zone in twin.zones else None
            row["from_c"] = cur
            # The agent picks its own id, so a replay is simply a fresh id with the old call.
            rec, _ = twin.apply_setpoint({"operation_id": f"a-{n:04d}", "zone_id": zone, "setpoint_c": value,
                                          "units": "degC", "expected_state_revision": twin.revision})
            n += 1
            applied = rec.status == "APPLIED"
            row["applied"] = applied
            row["decision"] = "ungoverned"
            if not applied:
                row["execute"] = {"status": 409, "twin_status": "REJECTED", "detail": rec.reason}
            ledger.append(row)
        twin.advance(step)
    s = score(twin)
    return {"ledger": ledger, "revision_before": rev0, "revision_after": twin.revision,
            "simulated_s": int(ep["simulated_s"]), "architecture": ARCHITECTURE,
            "episode_id": ep["episode_id"], "plant": {k: s[k] for k in ("energy_kwh", "energy_cost_simulated",
                                                                         "comfort_violation_degree_hours",
                                                                         "setpoint_changes_count")}}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.arm_a")
    p.add_argument("--episode", required=True)
    p.add_argument("--out", required=True, help="work directory to create (ledger.jsonl, arm-a.json)")
    a = p.parse_args(argv)
    ep = episode_mod.load(Path(a.episode))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    live = run(ep)
    (out / "ledger.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in live["ledger"]), "utf-8")
    (out / "arm-a.json").write_text(json.dumps({k: v for k, v in live.items() if k != "ledger"}, indent=2) + "\n", "utf-8")
    applied = sum(1 for r in live["ledger"] if r["applied"])
    print(f"arm A ({ARCHITECTURE}): {len(live['ledger'])} proposals, {applied} applied, "
          f"revision {live['revision_before']}->{live['revision_after']}; {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
