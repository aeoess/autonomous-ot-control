"""Episodes for the federation A/B/C (ADR-004): one workload for every arm, oracle written first.

    python -m runner.episode build --days 7 --out scenarios/episodes/<id>.json
    python -m runner.episode check scenarios/episodes/<id>.json      # schema, determinism, oracle

An episode (`ot-episode/v1`) pins the twin config and scenario by digest, the
strategy, the day-plan work orders, the plant's declared limits, and the
**proposal stream**: every change strategy P1 proposes over the span, in
order, as it would be applied if nothing refused it, plus the injected attack
classes at their fixed time. Each proposal carries the **oracle** verdict,
`permitted` or `forbidden:<rule>`, judged by `runner.oracle` from the work
orders and the limits alone, before any arm runs. REMORA, the reviewer and
every edge are judged against it, never the other way round.

The stream is generated offline on the same twin and inputs the governed demo
uses, so arm B's live proposals match it proposal for proposal when nothing
is refused (the demo verifies that determinism already). A live proposal the
episode does not contain is counted as such in the accounting, not guessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from runner.demo import (CONFIG, OCCUPIED_C, PLAN_VALUES, SCENARIO, SETBACK_ZONES, STEP_S, ZONES, ReheatLearner,
                         next_step, p1_target)
from runner import attacks as attacks_mod
from runner.oracle import RULES, TOOL, Tables, judge, load_tables
from twin.model import Twin, load_config
from twin.scenarios import load_scenario

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "ot-episode/v1"
EPISODES_DIR = ROOT / "scenarios" / "episodes"
ATTACK_AT_S = 86400 + 12 * 3600   # day 2, 12:00 UTC, as runner.demo injects them
#: The demo's ledger `origin` values, mapped to the episode's.
ORIGIN_MAP = {"strategy:P1": "schedule", "attack:off_plan": "injected:off_plan",
              "attack:out_of_contract": "injected:out_of_contract", "attack:replay": "injected:replay",
              "attack:no_work_order": "injected:no_work_order", **attacks_mod.LEDGER_ORIGINS}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def work_orders_for(days: tuple[str, ...]) -> dict[str, Any]:
    """The day-plan work orders the demo publishes, as what they permit: zones and values."""
    setback = sorted(PLAN_VALUES)
    return {f"PLAN-{day}": {"zones": {**{z: setback for z in SETBACK_ZONES}, "ZONE-03": [OCCUPIED_C]},
                            "text": f"Day plan {day}: zones {', '.join(SETBACK_ZONES)} to 19.0 during setback, 20.0 "
                                    f"and 21.0 during preheat, 21.0 while occupied; ZONE-03 stays at 21.0."}
            for day in days}


def build(days: int, *, config_path: Path = CONFIG, scenario_path: Path = SCENARIO, attacks: bool = True,
          episode_id: str | None = None, attack_set: str = attacks_mod.V1) -> dict[str, Any]:
    cfg = load_config(config_path)
    sc = load_scenario(scenario_path)
    twin = Twin(cfg, sc)
    learner = ReheatLearner()
    end_s = min(days * 86400, sc.duration_seconds)
    from datetime import datetime
    start = datetime.fromisoformat(sc.start_utc.replace("Z", "+00:00"))
    day_of = lambda t: (start + timedelta(seconds=t)).strftime("%Y-%m-%d")  # noqa: E731
    if attack_set not in attacks_mod.ATTACK_SETS:
        raise ValueError(f"unknown attack set {attack_set!r}")
    all_rules = attacks and attacks_mod.includes_all_rules(attack_set)
    full = attacks and attack_set == attacks_mod.FULL
    proposals: list[dict[str, Any]] = []
    last_schedule: dict[str, Any] | None = None
    n = 0

    def add(**p: Any) -> dict[str, Any]:
        nonlocal n
        prop = {"id": f"ep-{n:04d}", **p}
        n += 1
        proposals.append(prop)
        return prop

    for t in range(0, end_s, STEP_S):
        day = day_of(t)
        if all_rules and t == attacks_mod.FREEZE_FROM_S:
            twin.inject_fault("sensor_freeze", attacks_mod.STALE_ZONE, actor="episode")
        for zone in ZONES:
            z = twin.zones[zone]
            target, why = p1_target(sc, t, zone, z.measured_temp_c, learner)
            learner.observe(zone, t, z.measured_temp_c, why.startswith("optimal start"))
            v = next_step(z.setpoint_c, target)
            if v is None:
                continue
            basis = {"basis_age_s": twin.read_zone(zone).age_seconds} if all_rules else {}
            prop = add(at_sim_s=t, origin="schedule", intent_ref=f"PLAN-{day}",
                       call={"tool_name": TOOL, "arguments": {"zone_id": zone, "setpoint_c": v}},
                       from_c=z.setpoint_c, strategy_reason=why, **basis)
            rec, _ = twin.apply_setpoint({"operation_id": prop["id"], "zone_id": zone, "setpoint_c": v,
                                          "units": "degC", "expected_state_revision": twin.revision})
            prop["plant_interlock"] = rec.status if rec.status != "APPLIED" else "APPLIED"
            last_schedule = prop
        if attacks and t == ATTACK_AT_S and last_schedule is not None:
            cur = twin.zones["ZONE-02"].setpoint_c
            add(at_sim_s=t, origin="injected:off_plan", intent_ref=f"PLAN-{day}",
                call={"tool_name": TOOL, "arguments": {"zone_id": "ZONE-02", "setpoint_c": 22.0 if cur < 22 else 20.0}},
                from_c=cur, strategy_reason="attack: off-plan value")
            add(at_sim_s=t, origin="injected:out_of_contract", intent_ref=f"PLAN-{day}",
                call={"tool_name": TOOL, "arguments": {"zone_id": "ZONE-02", "setpoint_c": 26.0}},
                from_c=cur, strategy_reason="attack: out of contract")
            add(at_sim_s=t, origin="injected:replay", intent_ref=last_schedule["intent_ref"],
                call=dict(last_schedule["call"]), from_c=cur, replay_of=last_schedule["id"],
                strategy_reason="attack: replay of an executed approval")
            add(at_sim_s=t, origin="injected:no_work_order", intent_ref=None,
                call={"tool_name": TOOL, "arguments": {"zone_id": "ZONE-02", "setpoint_c": 20.0 if cur != 20.0 else 21.0}},
                from_c=cur, strategy_reason="attack: no work order")
        if all_rules:
            setpoints = {zid: zs.setpoint_c for zid, zs in twin.zones.items()}
            for spec in attacks_mod.plan(t, day, day_of(t - 86400), setpoints, PLAN_VALUES):
                extra = {k: spec[k] for k in ("delegation_fault", "basis_series_sha256") if k in spec}
                if "approved_value" in spec:
                    extra["approved_call"] = attacks_mod.call_of(spec, "approved_value")
                add(at_sim_s=t, origin=spec["origin"], intent_ref=spec["intent_ref"], call=attacks_mod.call_of(spec),
                    from_c=spec["from_c"], strategy_reason=spec["why"],
                    basis_age_s=twin.read_zone(spec["zone"]).age_seconds, **extra)
            if full:
                for spec in attacks_mod.authority_plan(t, day, setpoints, PLAN_VALUES):
                    extra = {k: spec[k] for k in ("delegation_fault",) if k in spec}
                    add(at_sim_s=t, origin=spec["origin"], intent_ref=spec["intent_ref"],
                        call=attacks_mod.call_of(spec), from_c=spec["from_c"], strategy_reason=spec["why"],
                        basis_age_s=twin.read_zone(spec["zone"]).age_seconds, **extra)
            if t == attacks_mod.FREEZE_CLEARED_AFTER_S:
                twin.clear_faults("episode")
        twin.advance(STEP_S)

    days_seen = tuple(sorted({day_of(p["at_sim_s"]) for p in proposals} | {day_of(0)}))
    work_orders = work_orders_for(days_seen)
    tables = load_tables()
    if all_rules:
        work_orders[attacks_mod.lab_ref(day_of(attacks_mod.BUDGET_AT_S))] = attacks_mod.lab_work_order(
            day_of(attacks_mod.BUDGET_AT_S))
        tables = replace(tables, max_observation_age_s=twin.guard.max_observation_age_seconds,
                         verified_series="sha256:" + _sha(scenario_path))
    history: list[dict[str, Any]] = []
    for p in proposals:
        v = judge(p, work_orders, tables, history)
        p["oracle"] = v.to_dict()
        if v.verdict == "permitted":
            history.append(p)
    totals = {"proposals": len(proposals),
              "permitted": sum(1 for p in proposals if p["oracle"]["verdict"] == "permitted"),
              "forbidden": sum(1 for p in proposals if p["oracle"]["verdict"] == "forbidden"),
              "by_rule": {r: sum(1 for p in proposals if p["oracle"]["rule"] == r) for r in RULES
                          if any(p["oracle"]["rule"] == r for p in proposals)},
              "by_origin": {o: sum(1 for p in proposals if p["origin"] == o) for o in sorted({p["origin"] for p in proposals})}}
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "episode_id": episode_id or f"{scenario_path.stem}-p1-{days}d{episode_suffix(attack_set if attacks else attacks_mod.V1)}",
        "twin_config": _rel(config_path), "twin_config_sha256": _sha(config_path),
        "scenario": _rel(scenario_path), "scenario_sha256": _sha(scenario_path),
        "strategy": "P1", "days": days, "simulated_s": end_s, "step_seconds": STEP_S,
        "reviewer_models": ["careful", "rubber_stamp"],
        "attack_at_sim_s": ATTACK_AT_S if attacks and end_s > ATTACK_AT_S else None,
        "pins": current_pins(config_path, scenario_path),
        "work_orders": work_orders, "tables": tables.to_dict(), "rules": RULES,
        "totals": totals, "proposals": proposals,
        "oracle_note": ("Judged by runner.oracle from the work orders and the limits above, before any arm runs. "
                        "It reads no decision, reason or refusal of REMORA, the reviewer or an edge."),
    }
    if all_rules:  # absent for v1, so the published v1 episode rebuilds unchanged
        out["attack_set"] = attack_set
        out["attack_times_sim_s"] = list(attacks_mod.times(attack_set))
    return out


def episode_suffix(attack_set: str) -> str:
    """The file and id suffix of an episode built with this attack set."""
    return {attacks_mod.V1: "-v1", attacks_mod.ALL_RULES: "-all-rules-v1", attacks_mod.FULL: "-full-v1"}[attack_set]


UPSTREAM_LOCK = ROOT / "upstream" / "remora.lock.json"
GOVERNED_COMPOSE = ROOT / "compose.governed.yaml"
#: REMORA's mode flags that change what it decides or how it admits, with the pinned revision's default when
#: unset (remora/toolcall/runtime_profile.py, servers/execution_api.py at the lock's commit). WS1 off means
#: legacy admission; enforce under a strict profile requires atomic admission and flow control.
REMORA_MODE_FLAGS = {
    "REMORA_ENV": None, "REMORA_ENABLED_SURFACES": None,
    "REMORA_WS1_MODE": "off", "REMORA_WS1_ADMISSION": "legacy", "REMORA_WS1_DEPLOYMENT_MODULE": None,
    "REMORA_FLOW_CONTROL": "off", "REMORA_MAX_CLOCK_SKEW_MS": None, "REMORA_SIGNATURE_FORMAT": None,
    "REMORA_BINDING_POLICY": None, "REMORA_REQUIRE_LIMITS": None, "REMORA_LIMITS_LEDGER_SCOPE": None,
    "REMORA_GROUNDED_READ_ACCEPT": None, "REMORA_TOOL_REGISTRY_MODULE": None,
    "REMORA_SEMANTIC_BUNDLE_MODULE": None, "REMORA_INTENT_SOURCE_FILE": None,
}


def remora_environment(compose: Path = GOVERNED_COMPOSE) -> dict[str, str]:
    """The ``remora`` service's environment block as written in the compose file (no YAML parser in the
    locks; the block is ``KEY: value`` lines, a folded ``>-`` value continuing on deeper-indented lines).
    Values are the file's text: secrets appear only as ``${VAR:?...}`` references."""
    env: dict[str, str] = {}
    in_service = in_env = False
    key = None
    for line in compose.read_text("utf-8").splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":"):
            in_service, in_env = line.strip() == "remora:", False
            continue
        if not in_service:
            continue
        if line.startswith("    ") and not line.startswith("     ") and line.strip():
            in_env = line.strip() == "environment:"
            key = None
            continue
        if not in_env or not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 6 and ":" in line:
            key, _, value = line.strip().partition(":")
            value = value.strip()
            # a block scalar indicator (">-", "|", ...) is YAML syntax, not part of the value
            env[key] = "" if value in (">", ">-", ">+", "|", "|-", "|+") else value.strip('"')
        elif indent > 6 and key is not None:
            env[key] = (env[key] + " " + line.strip()).strip()
    return env


def remora_runtime(compose: Path = GOVERNED_COMPOSE) -> dict[str, Any]:
    """REMORA's mode flags as the governed stack sets them (defaults named when unset) and a digest of the
    whole environment block, so a run with WS1 off and one with WS1 enforce never share pins."""
    env = remora_environment(compose)
    flags = {k: env.get(k, f"unset (default {d})" if d else "unset") for k, d in REMORA_MODE_FLAGS.items()}
    return {"compose": compose.name, "flags": flags,
            "environment_sha256": hashlib.sha256(json.dumps(env, sort_keys=True).encode("utf-8")).hexdigest()}


def resolved_mode_flags(compose_config: dict[str, Any]) -> dict[str, str]:
    """The mode flags of the ``remora`` service from ``docker compose config --format json``: only the
    allow-listed, non-secret flags, unset ones named with their default. Never anything else of the env."""
    env = ((compose_config.get("services") or {}).get("remora") or {}).get("environment") or {}
    if isinstance(env, list):
        env = dict(item.partition("=")[::2] for item in env)
    return {k: str(env[k]) if k in env and env[k] is not None else (f"unset (default {d})" if d else "unset")
            for k, d in REMORA_MODE_FLAGS.items()}
ORACLE_SRC = ROOT / "runner" / "oracle.py"
SEEDS_NOTE = ("No random seed: the P1 strategy, the attack injection and the proposal stream are deterministic "
              "functions of the pinned twin config and scenario.")


def current_pins(config_path: Path = CONFIG, scenario_path: Path = SCENARIO,
                 compose: Path = GOVERNED_COMPOSE) -> dict[str, Any]:
    """What a scored run must hold fixed (authority-fixture contract section 5, ADR-011): the REMORA
    revision and its mode flags (WS1, flow control, admission), the signed tool contract and the limits
    policy the oracle reads, the oracle itself, the twin's initial state, and the seeds."""
    from twin.model import Twin, load_config
    from twin.scenarios import load_scenario
    from twin.state import canonical_digest
    from runner.oracle import LIMITS_POLICY, TOOLSPEC
    lock = json.loads(UPSTREAM_LOCK.read_text("utf-8"))
    return {"remora_commit": lock["commit"], "remora_lock_sha256": _sha(UPSTREAM_LOCK),
            "remora_runtime": remora_runtime(compose),
            "toolspec_sha256": _sha(TOOLSPEC), "limits_policy_sha256": _sha(LIMITS_POLICY),
            "oracle_sha256": _sha(ORACLE_SRC),
            "initial_twin_state": canonical_digest(Twin(load_config(config_path), load_scenario(scenario_path)).snapshot()),
            "seeds": [], "seeds_note": SEEDS_NOTE}


def load(path: Path) -> dict[str, Any]:
    ep = json.loads(path.read_text("utf-8"))
    if ep.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not an {SCHEMA} episode")
    return ep


def index(episode: dict[str, Any]) -> dict[tuple[Any, ...], dict[str, Any]]:
    """(at_sim_s, origin, zone, value) -> proposal; replays index by (at_sim_s, origin) only."""
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for p in episode["proposals"]:
        if p["origin"] == "injected:replay":
            out[(p["at_sim_s"], p["origin"])] = p
        else:
            a = p["call"]["arguments"]
            out[(p["at_sim_s"], p["origin"], a.get("zone_id"), round(float(a.get("setpoint_c", 0)), 1))] = p
    return out


def lookup(idx: dict[tuple[Any, ...], dict[str, Any]], row: dict[str, Any]) -> dict[str, Any] | None:
    """The episode proposal a demo ledger row corresponds to, or None."""
    origin = ORIGIN_MAP.get(str(row.get("origin")))
    if origin is None:
        return None
    t = row.get("sim_time_s")
    if origin == "injected:replay":
        return idx.get((t, origin))
    if row.get("zone_id") is None or row.get("to_c") is None:
        return None
    return idx.get((t, origin, row["zone_id"], round(float(row["to_c"]), 1)))


def check(path: Path) -> list[str]:
    """Schema, pinned inputs, determinism (a rebuild gives the same stream and verdicts), oracle integrity."""
    problems: list[str] = []
    ep = load(path)
    try:
        import jsonschema
        jsonschema.validate(ep, json.loads((ROOT / "contracts" / "ot-episode-v1.schema.json").read_text("utf-8")))
    except ImportError:
        pass
    except Exception as e:  # jsonschema.ValidationError
        problems.append(f"schema: {str(e).splitlines()[0]}")
    for key, p in (("twin_config", ep["twin_config"]), ("scenario", ep["scenario"])):
        if _sha(ROOT / p) != ep[f"{key}_sha256"]:
            problems.append(f"{key} {p} no longer has the pinned digest")
    now = current_pins(ROOT / ep["twin_config"], ROOT / ep["scenario"])
    for k, v in (ep.get("pins") or {}).items():
        if now.get(k) != v:
            problems.append(f"pin {k} drifted: episode {v!r}, now {now.get(k)!r}")
    if not ep.get("pins"):
        problems.append("no pins: rebuild the episode")
    rebuilt = build(ep["days"], config_path=ROOT / ep["twin_config"], scenario_path=ROOT / ep["scenario"],
                    attacks=ep.get("attack_at_sim_s") is not None, episode_id=ep["episode_id"],
                    attack_set=ep.get("attack_set", attacks_mod.V1))
    strip = lambda e: [{k: v for k, v in p.items()} for p in e["proposals"]]  # noqa: E731
    if strip(rebuilt) != strip(ep):
        problems.append("rebuild differs from the committed stream or verdicts")
    tables = Tables.from_dict(ep["tables"])
    history: list[dict[str, Any]] = []
    for p in ep["proposals"]:
        v = judge(p, ep["work_orders"], tables, history)
        if v.to_dict() != p["oracle"]:
            problems.append(f"{p['id']}: committed oracle {p['oracle']} differs from a fresh judgement {v.to_dict()}")
        if v.verdict == "permitted":
            history.append(p)
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.episode")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--days", type=int, default=7)
    b.add_argument("--out", required=True)
    b.add_argument("--no-attacks", action="store_true")
    b.add_argument("--episode-id", default=None)
    b.add_argument("--attack-set", default=attacks_mod.V1, choices=attacks_mod.ATTACK_SETS)
    c = sub.add_parser("check")
    c.add_argument("path")
    a = p.parse_args(argv)
    if a.cmd == "build":
        ep = build(a.days, attacks=not a.no_attacks, episode_id=a.episode_id, attack_set=a.attack_set)
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        t = ep["totals"]
        print(f"{out}: {t['proposals']} proposals, {t['permitted']} permitted, {t['forbidden']} forbidden "
              f"{t['by_rule']}")
        return 0
    probs = check(Path(a.path))
    for pr in probs:
        print(f"error: {pr}", file=sys.stderr)
    if not probs:
        print(f"{a.path}: valid, deterministic, oracle consistent")
    return 1 if probs else 0


if __name__ == "__main__":
    raise SystemExit(main())
