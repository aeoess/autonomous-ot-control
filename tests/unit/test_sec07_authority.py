"""SDD-OT-002 SEC-07, bounded: signed work orders (SR-05, F-05) and a reviewer that is not the proposer (SR-06,
F-06). The work-order authority signs only what it builds itself; an entry anyone else writes, or a signed one
changed afterwards, is not a work order; the independent reviewer decides from its own sources only."""
from __future__ import annotations

import importlib.util
import json
import secrets
import sys
from pathlib import Path

import pytest

from adapter import work_orders as wo

ROOT = Path(__file__).resolve().parents[2]
OT = ROOT / "deploy" / "ot"


def _load(name: str):
    if str(OT) not in sys.path:
        sys.path.insert(0, str(OT))
    spec = importlib.util.spec_from_file_location(name, OT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def seed(tmp_path) -> Path:
    p = tmp_path / "work_order.seed"
    p.write_text(secrets.token_hex(32) + "\n", "utf-8")
    return p


def test_a_signed_entry_verifies_and_any_change_breaks_it(seed):
    key = wo.key_from_seed(seed.read_text("utf-8"))
    trusted = wo.trusted_document(key)["keys"]
    plans = _load("ot_day_plans")
    ref, entry = plans.plan_entry("2026-01-15")
    signed = wo.sign_entry(ref, entry, key)
    assert wo.verify_entry(ref, signed, trusted) == (wo.key_id(key), "ok")
    assert wo.verify_entry(ref, entry, trusted)[1] == "unsigned"
    assert wo.verify_entry("PLAN-2026-01-16", signed, trusted)[1] == "bad_signature"  # filed under another ref
    widened = {**signed, "permits": {"set_zone_setpoint": {"ZONE-01": [23.0]}}}
    assert wo.verify_entry(ref, widened, trusted)[1] == "bad_signature"
    other = wo.key_from_seed(secrets.token_hex(32))
    assert wo.verify_entry(ref, wo.sign_entry(ref, entry, other), trusted)[1] == "untrusted_key"


def test_the_authority_builds_what_it_signs_and_refuses_other_kinds(seed, tmp_path):
    auth = _load("ot_authority")
    table = tmp_path / "ot_intents.json"
    table.write_text(json.dumps({"MON-ROUND": {"task_text": "x"}}), "utf-8")
    out = auth.issue("plan-P1", "2026-01-15", seed, table)
    t = json.loads(table.read_text("utf-8"))
    assert out["intent_ref"] == "PLAN-2026-01-15" and t["PLAN-2026-01-15"]["valid_on"] == "2026-01-15"
    assert t["PLAN-2026-01-15"]["permits"]["set_zone_setpoint"]["ZONE-03"] == [21.0]
    assert "MON-ROUND" in t  # other entries are kept
    for kind, day in (("anything", "2026-01-15"), ("plan-P1", "2026-1-15"), ("plan-P1", "../x")):
        with pytest.raises(ValueError):
            auth.issue(kind, day, seed, table)
    # The CLI takes a kind and a day only: there is no way to hand it content to sign.
    with pytest.raises(SystemExit):
        auth.main(["issue", "--kind", "plan-P1", "--day", "2026-01-15", "--entry", "{}"])


class _Reviewer:
    def __init__(self, seed: Path):
        self.mod = _load("ot_reviewer")
        self.key = wo.key_from_seed(seed.read_text("utf-8"))
        self.trusted = wo.trusted_document(self.key)["keys"]
        plans = _load("ot_day_plans")
        ref, entry = plans.plan_entry("2026-01-15")
        self.intents = {ref: wo.sign_entry(ref, entry, self.key)}
        yref, yentry = plans.plan_entry("2026-01-14")
        self.intents[yref] = wo.sign_entry(yref, yentry, self.key)
        self.intents["PLAN-2026-01-15-F"] = {**entry, "permits": {"set_zone_setpoint": {"ZONE-02": [22.0]}}}

    def judge(self, ref, zone, value, *, current=21.0, day="2026-01-15"):
        call = {"tool_name": "set_zone_setpoint", "intent_ref": ref, "arguments": {"zone_id": zone, "setpoint_c": value}}
        return self.mod.judge(call, self.intents, self.trusted, day, lambda z: current, 1.0)


def test_the_independent_reviewer_approves_only_what_its_own_sources_permit(seed):
    r = _Reviewer(seed)
    assert r.judge("PLAN-2026-01-15", "ZONE-01", 20.0)[0] is True
    for args, why in ((("PLAN-2026-01-15-F", "ZONE-02", 22.0), "not a work order signed"),
                      (("PLAN-2026-01-14", "ZONE-01", 20.0), "valid on 2026-01-14"),
                      (("PLAN-2026-01-15", "ZONE-01", 22.0), "does not permit"),
                      (("PLAN-2026-01-15", "ZONE-03", 20.0), "does not permit"),
                      ((None, "ZONE-01", 20.0), "no work order")):
        ok, reason = r.judge(*args)
        assert ok is False and why in reason, (args, reason)
    ok, reason = r.judge("PLAN-2026-01-15", "ZONE-01", 19.0, current=21.0)
    assert ok is False and "step" in reason


def test_the_reviewer_believes_the_proposer_only_where_remoras_record_binds_it(seed):
    mod = _load("ot_reviewer")

    def call_hash(*, name, arguments, tenant, target):
        import hashlib
        import json as j
        return hashlib.sha256(j.dumps([name, arguments, tenant, target], sort_keys=True).encode()).hexdigest()

    call = {"tool_name": "set_zone_setpoint", "intent_ref": "PLAN-A", "arguments": {"zone_id": "ZONE-01", "setpoint_c": 20.0}}
    assessed = {"review_item_id": "item-1", "target_environment": "simulated_pilot", "intent_resolution_status": "resolved",
                "tool_call_hash": call_hash(name="set_zone_setpoint", arguments=call["arguments"], tenant="ot-pilot",
                                            target="simulated_pilot"),
                "intent_authority_hash": "h-A"}
    hashes = {"PLAN-A": "h-A", "PLAN-B": "h-B"}.get
    assert mod.bound(assessed, "item-1", call, call_hash, hashes) == (True, "bound")
    for item, c, why in (("item-2", call, "another review item"),
                         ("item-1", {**call, "arguments": {"zone_id": "ZONE-01", "setpoint_c": 21.0}}, "not the one REMORA"),
                         ("item-1", {**call, "intent_ref": "PLAN-B"}, "not the work order REMORA resolved")):
        ok, reason = mod.bound(assessed, item, c, call_hash, hashes)
        assert ok is False and why in reason, reason
    unresolved = {**assessed, "intent_resolution_status": "unresolved"}
    assert mod.bound(unresolved, "item-1", call, call_hash, hashes)[0] is False


def test_the_proposer_holds_no_approver_token_and_the_reviewer_no_operator_token():
    compose = (ROOT / "compose.governed.yaml").read_text("utf-8")

    def service(name: str) -> str:
        start = compose.index(f"\n  {name}:\n")
        nxt = compose.find("\n  ", start + 3 + len(name))
        while nxt != -1 and compose[nxt + 3] == " ":
            nxt = compose.find("\n  ", nxt + 1)
        return compose[start:nxt if nxt != -1 else None]
    harness, reviewer, authority = service("harness"), service("reviewer"), service("authority")
    assert "REMORA_APPROVER_TOKEN: ${" not in harness and "REMORA_OPERATOR_TOKEN: ${" in harness
    assert "REMORA_APPROVER_TOKEN: ${" in reviewer and "REMORA_OPERATOR_TOKEN: ${" not in reviewer
    assert "work_order.seed" in authority and "network_mode: none" in authority and "TOKEN: ${" not in authority
    assert "work_order.seed" not in harness + reviewer + service("remora")
    for svc in (service("remora"), reviewer):
        assert "REMORA_OT_WORK_ORDER_TRUSTED_KEYS: ${" in svc and "work_order_keys.json" not in svc
    assert "REMORA_OT_WORK_ORDER_TRUSTED_KEYS" not in harness + authority


def test_the_trust_anchor_is_pinned_and_a_swapped_key_is_refused():
    key, other = wo.key_from_seed(secrets.token_hex(32)), wo.key_from_seed(secrets.token_hex(32))
    assert wo.parse_trusted_env(wo.trusted_env_value(key)) == wo.trusted_document(key)["keys"]
    pub_other = wo.trusted_env_value(other).split(":", 1)[1]
    with pytest.raises(ValueError):
        wo.parse_trusted_env(f"{wo.key_id(key)}:{pub_other}")  # a key id that is not its key's
    for src in ("ot_bundle.py", "ot_reviewer.py"):
        text = (OT / src).read_text("utf-8")
        assert "REMORA_OT_WORK_ORDER_TRUSTED_KEYS" in text and "load_trusted" not in text


def test_the_harness_answers_no_credential_instead_of_approving(monkeypatch):
    from runner import harness_step
    monkeypatch.setenv("REMORA_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("REMORA_APPROVER_TOKEN", raising=False)
    out = harness_step.op_approve({"item_id": "x"})
    assert out["error"] == "no_credential" and out["status"] is None
    assert "sdk_approve" not in harness_step.OPS  # the typed-SDK approval moved to the reviewer


def test_unsigned_tables_are_labelled_intent_table_not_signed_work_order():
    src = (OT / "ot_bundle.py").read_text("utf-8")
    assert 'auth = f"intent_table:' in src and 'auth = f"signed_work_order:{kid}:' in src
    assert src.count("signed_work_order") == 1  # the one label, set only after verify_entry succeeded


def test_the_full_episode_adds_the_two_attacks_on_authority():
    from runner import arm_a, episode, ledger_metrics as lm
    path = ROOT / "scenarios" / "episodes" / "trondheim-week-2026w03-hvac-v1-p1-7d-full-v1.json"
    assert episode.check(path) == []
    ep = episode.load(path)
    by_origin = {p["origin"]: p["oracle"]["rule"] for p in ep["proposals"] if p["origin"].startswith("injected:")}
    assert by_origin["injected:forged_work_order"] == "unauthorized"
    assert by_origin["injected:self_approval"] == "delegation_invalid"
    live = arm_a.run(ep)
    ledger = lm.attach(live["ledger"], ep)
    applied = {r["origin"]: r["applied"] for r in ledger if r["origin"] in ("attack:forged_work_order", "attack:self_approval")}
    assert applied == {"attack:forged_work_order": True, "attack:self_approval": True}  # nothing stops the agent in arm A
