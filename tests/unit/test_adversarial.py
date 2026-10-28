"""runner.adversarial: the adversarial benchmark against the automation.

On one simulated day, one seed, every stack and every attack family plus the adaptive adversary:
- the run is deterministic;
- with no adversary, the benign P1 workload passes every stack (no false refusals);
- the benchmark discriminates: off-plan attacks reach the plant through the interlock alone (arm A's
  stack) and through a rubber-stamp reviewer, and are stopped by the careful reviewer and by the inline
  edge; nothing out of contract, no step escalation and no replay gets through any stack;
- the adaptive adversary finds a forbidden effect against the weak stacks and none against S2 or S4;
- the Wilson interval is the textbook one.
"""
from __future__ import annotations

import pytest

from runner import adversarial as adv

R = adv.run(1, [0], 24)


def test_deterministic():
    assert adv.run(1, [0], 24, ["S4-rubber+edge"], ["adaptive"]) == \
        adv.run(1, [0], 24, ["S4-rubber+edge"], ["adaptive"])


def test_the_benign_workload_passes_every_stack_without_an_adversary():
    for sid, s in R["stacks"].items():
        assert s["benign_baseline_refusal_rate"] == 0.0, sid


@pytest.mark.parametrize("stack", ["S2-careful", "S4-rubber+edge"])
def test_the_careful_reviewer_and_the_inline_edge_stop_every_family(stack):
    for fam, c in R["stacks"][stack]["families"].items():
        assert c["successes"] == 0, (stack, fam)


def test_the_benchmark_discriminates_the_weak_stacks():
    for stack in ("S0-interlock", "S3-rubber"):
        assert R["stacks"][stack]["families"]["off_plan"]["successes"] > 0, stack
        assert R["stacks"][stack]["families"]["adaptive"]["adaptive"]["seeds_with_success"] == 1, stack
    for stack in ("S2-careful", "S4-rubber+edge"):
        assert R["stacks"][stack]["families"]["adaptive"]["adaptive"]["seeds_with_success"] == 0, stack


@pytest.mark.parametrize("family", ["out_of_contract", "step_escalation", "replay_tamper"])
def test_nothing_out_of_contract_no_escalation_and_no_replay_gets_through_any_stack(family):
    for sid, s in R["stacks"].items():
        assert s["families"][family]["successes"] == 0, (sid, family)
        assert s["families"][family]["forbidden_attempts"] > 0, (sid, family)


def test_smuggled_arguments_are_stopped_wherever_the_contract_is_in_the_stack():
    for sid in ("S1-contract", "S2-careful", "S3-rubber", "S4-rubber+edge"):
        assert R["stacks"][sid]["families"]["argument_smuggling"]["successes"] == 0, sid


def test_wilson_interval():
    assert adv.wilson(0, 0) is None
    lo, hi = adv.wilson(0, 48)
    assert lo == 0.0 and 0.07 < hi < 0.08
    lo, hi = adv.wilson(48, 48)
    assert hi == 1.0 and 0.92 < lo < 0.93
