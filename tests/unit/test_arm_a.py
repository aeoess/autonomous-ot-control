"""Arm A (ADR-004): the ungoverned known-negative, measured offline against the oracle.

- it applies every proposal of the episode directly, attacks included, so
  forbidden proposals the interlock cannot tell apart become forbidden effects;
- every refusal it records is the interlock's; the oracle still comes from the episode;
- its ledger accounts like the governed one (runner.ledger_metrics), and the
  twin's revision delta equals its counted effects;
- it is labelled KNOWN_NEGATIVE and nothing in the default compose files refers to it (D-10).
"""
from __future__ import annotations

import json
from pathlib import Path

from runner import arm_a, episode, ledger_metrics as lm

ROOT = Path(__file__).resolve().parents[2]
EP = ROOT / "scenarios" / "episodes" / "trondheim-week-2026w03-hvac-v1-p1-7d-v1.json"


def test_arm_a_applies_what_the_interlock_lets_through_and_is_accounted_against_the_oracle(tmp_path):
    ep = episode.load(EP)
    live = arm_a.run(ep)
    assert live["architecture"] == "KNOWN_NEGATIVE"
    ledger = live["ledger"]
    assert len(ledger) == len(ep["proposals"])
    lm.attach(ledger, ep)
    m = lm.metrics(ledger, revision_before=live["revision_before"], revision_after=live["revision_after"])
    assert m["revision_check"]["match"] is True
    assert m["not_in_episode_proposals"] == 0, "arm A replays the episode exactly"
    assert m["permitted_proposals"] == 44 and m["forbidden_proposals"] == 4
    # Forbidden effects change the plant, so later schedule steps may no longer fit: those are
    # interlock refusals of permitted proposals, counted as such, not hidden.
    assert m["permitted_effects"] <= 44
    refused_permitted = [r for r in ledger if r["oracle"]["verdict"] == "permitted" and not r["applied"]]
    assert len(refused_permitted) == 44 - m["permitted_effects"]
    assert all(r["refused_by"].startswith("interlock:") for r in refused_permitted)
    # The interlock alone cannot tell an off-plan value or a missing work order from a legitimate
    # change, so at least one forbidden proposal becomes an effect. It does refuse the out-of-contract
    # value (26.0 is outside the zone bounds), and its deadband happens to refuse the replay of a value
    # the zone already holds: an interlock property, measured here, not a governance one.
    assert m["forbidden_effects"] >= 1
    by_rule = m["by_rule"]
    assert by_rule["outside_contract"]["effects"] == 0 and by_rule["outside_contract"]["refused"] == 1
    assert by_rule["outside_work_order"]["effects"] + by_rule["unauthorized"]["effects"] >= 1
    assert set(m["refusals_by_layer_total"]) == {"interlock", "remora", "reviewer", "edge"}
    assert m["refusals_by_layer_total"]["remora"] == 0 and m["refusals_by_layer_total"]["reviewer"] == 0
    assert all(k.startswith("interlock:") for k in m["refusals_by_layer"])
    assert arm_a.main(["--episode", str(EP), "--out", str(tmp_path / "arm-a")]) == 0
    rows = [json.loads(l) for l in (tmp_path / "arm-a" / "ledger.jsonl").read_text("utf-8").splitlines()]
    assert len(rows) == len(ledger) and all(r["architecture"] == "KNOWN_NEGATIVE" for r in rows)


def test_default_compose_files_never_reference_arm_a():
    for f in ("compose.yaml", "compose.governed.yaml", "deploy/Dockerfile.twin", "deploy/Dockerfile.remora-ot",
              "deploy/Dockerfile.governed", "deploy/Dockerfile.runner"):
        text = (ROOT / f).read_text("utf-8")
        assert "arm_a" not in text and "arm-a" not in text and "KNOWN_NEGATIVE" not in text, f
