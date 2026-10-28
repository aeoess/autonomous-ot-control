"""Governed acceptance suite against the live Docker stack (OT-005, SDD §11).

Runs on the HOST, stdlib only. Every action goes through one of three doors,
so a result always says which credential did what:

* ``docker compose exec harness python -m runner.harness_step ...`` for
  REMORA calls as the proposer (operator, viewer, verifier tokens) and twin
  reads;
* ``docker compose exec reviewer python /app/ot_reviewer.py ...`` for every
  approval and rejection: the approver token is there only (SDD-OT-002
  SR-06). The suite's driver plays proposer and tester, as a human tester
  would; the credentials stay in separate processes;
* ``docker compose exec authority python /app/ot_authority.py ...`` to issue
  a signed work order (SR-05);
* ``docker compose exec twin python -m twin.ctl ...`` for the twin's loopback
  control listener (clock, stop switch), which only the twin can reach;
* ``docker compose stop|start|restart`` for faults (PostgreSQL outage,
  REMORA restart).

Each case states its expectation before it runs and is judged on observed
plant state, not on REMORA's word alone: the twin's ``state_revision`` counts
applied effects, and the suite ends by checking that it equals the number of
effects the cases expected (no unexplained write). A case that cannot run is
``NOT_RUN``, never ``PASS``, and any ``FAIL`` or ``NOT_RUN`` makes the exit
code non-zero unless it is listed in ``--allow-not-run``.

    python -m runner.acceptance --run-id <fresh-id> --out artifacts
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pathlib import Path

from runner.evidence import write_bundle

SUITE_ID = "ot-governed-acceptance/v1"
WO = "WORK-ORDER-001"          # lower ZONE-01 (signed work order)
WO2 = "WORK-ORDER-002"         # raise ZONE-02
WO3 = "WORK-ORDER-003"         # adjust ZONE-03 (0.5 degC/h REMORA budget)
SET = "set_zone_setpoint"
#: The intent table REMORA, the reviewer and the authority share (deploy/.generated, gitignored).
INTENT_TABLE = Path(__file__).resolve().parents[1] / "deploy" / ".generated" / "ot_intents.json"


class StepError(RuntimeError):
    pass


@dataclass
class Stack:
    compose: list[str]
    trace: list[dict[str, Any]] = field(default_factory=list)
    #: REMORA calls that got no answer (no HTTP status): a transport failure is never a refusal (SR-10).
    no_answer: list[dict[str, Any]] = field(default_factory=list)

    def _run(self, args: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*self.compose, *args], capture_output=True, text=True,
                              timeout=timeout, env={**os.environ, "MSYS_NO_PATHCONV": "1"})

    def step(self, op: str, **args: Any) -> dict[str, Any]:
        """One step in the container that holds the credential for it: approvals and rejections as the
        approver in ``reviewer`` (SR-06), everything else in ``harness``. ``via="harness"`` forces the
        proposer's container, to show that it cannot approve."""
        via = args.pop("via", None)
        if via is None:
            via = "reviewer" if op in REVIEWER_OPS and args.get("role", "approver") == "approver" else "harness"
        cmd = (["exec", "-T", "reviewer", "python", "/app/ot_reviewer.py", op, json.dumps(args)] if via == "reviewer"
               else ["exec", "-T", "harness", "python", "-m", "runner.harness_step", op, json.dumps(args)])
        p = self._run(cmd)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        if not lines:
            raise StepError(f"{op}: no JSON from {via} (rc={p.returncode}): {p.stderr[-400:]}")
        out = json.loads(lines[-1])
        self.trace.append({"op": op, "via": via, "args": _redact(args), "result": _brief(out)})
        # A step that never reached REMORA because its container holds no such credential is not a transport
        # failure; the case that asked for it judges it.
        if op in REMORA_OPS and not answered(out) and out.get("error") != "no_credential":
            self.no_answer.append({"op": op, "error": out.get("error")})
        return out

    def authority(self, kind: str, day: str) -> dict[str, Any]:
        p = self._run(["exec", "-T", "authority", "python", "/app/ot_authority.py", "issue", "--kind", kind,
                       "--day", day])
        self.trace.append({"op": "authority.issue", "kind": kind, "day": day, "rc": p.returncode})
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        if p.returncode != 0 or not lines:
            raise StepError(f"authority issue {kind} {day} failed: {p.stdout[-300:]} {p.stderr[-300:]}")
        return json.loads(lines[-1])

    def ctl(self, *args: str) -> dict[str, Any]:
        p = self._run(["exec", "-T", "twin", "python", "-m", "twin.ctl", *args])
        self.trace.append({"op": "twin.ctl", "args": list(args), "rc": p.returncode})
        if p.returncode != 0:
            raise StepError(f"twin.ctl {' '.join(args)} failed: {p.stdout[-300:]} {p.stderr[-300:]}")
        return json.loads(p.stdout)

    def service(self, action: str, name: str) -> None:
        p = self._run([action, name], timeout=180)
        self.trace.append({"op": f"compose.{action}", "service": name, "rc": p.returncode})
        if p.returncode != 0:
            raise StepError(f"compose {action} {name}: {p.stderr[-400:]}")

    def wait_ready(self, want: bool, timeout_s: float = 90) -> float:
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            try:
                if self.step("ready")["ok"] is want:
                    return round(time.monotonic() - start, 1)
            except StepError:
                pass  # the harness may briefly lose its peer during a restart
            time.sleep(1.0)
        raise StepError(f"REMORA readiness did not become {want} within {timeout_s}s")


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if "token" not in k.lower()}


def _brief(out: dict[str, Any]) -> dict[str, Any]:
    body = out.get("body")
    keep: dict[str, Any] = {k: out.get(k) for k in ("ok", "status", "error", "elapsed_ms")
                            if k in out}
    if isinstance(body, dict):
        for k in ("decision", "reasons", "proposal_id", "review_item_id", "outcome", "detail",
                  "status", "state_revision", "setpoint_c", "valid", "reason"):
            if k in body:
                keep[f"body.{k}"] = body[k]
        te = body.get("tool_execution")
        if isinstance(te, dict):
            keep["executed"] = te.get("executed")
            res = te.get("result") if isinstance(te.get("result"), dict) else {}
            keep["twin_status"] = res.get("status")
        ts = body.get("toolspec")
        if isinstance(ts, dict):
            keep["toolspec_enforced"] = ts.get("enforced")
    for k in ("reachable", "present", "audit_deferred", "status_text", "detail"):
        if k in out:
            keep[k] = out[k]
    return keep


# -- helpers -----------------------------------------------------------------

def call(zone: str, sp: float, wo: str | None = WO, tool: str = SET,
         **extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"zone_id": zone, "setpoint_c": sp, **extra}
    c: dict[str, Any] = {"tool_name": tool, "arguments": args}
    if wo:
        c["intent_ref"] = wo
    return c


def revision(s: Stack) -> int:
    r = s.step("status")
    if not r["ok"]:
        raise StepError(f"twin status unreadable: {r}")
    return int(r["body"]["state_revision"])


def setpoint(s: Stack, zone: str) -> float:
    return float(s.step("zone", zone_id=zone)["body"]["setpoint_c"])


#: Harness steps that return REMORA's raw HTTP answer (runner.harness_step._remora): each needs an HTTP status
#: and a body to count as a decision. SDK-mediated steps (record_effect, sdk_approve) return their own shape and
#: are judged by their case; tests/unit/test_acceptance_transport.py keeps this list equal to harness_step.
#: Steps the reviewer container runs (deploy/ot/ot_reviewer.py): the approver's token is there only (SR-06).
REVIEWER_OPS = frozenset({"approve", "reject", "sdk_approve", "decide", "unresolved", "resolve"})
REMORA_OPS = frozenset({"assess", "approve", "reject", "execute", "execute_accepted", "proposal", "envelope",
                        "evidence", "lifecycle", "audit_verify", "unresolved", "resolve"})


def answered(r: dict[str, Any]) -> bool:
    """REMORA answered: an HTTP status and a body. A transport failure (status None) is no answer (SR-10)."""
    return isinstance(r.get("status"), int) and r.get("body") is not None


def require_answer(r: dict[str, Any], what: str) -> None:
    """Credit a refusal only to an answer: no answer from REMORA fails the case, it never passes it."""
    if not answered(r):
        raise StepError(f"{what}: no answer from REMORA ({r.get('error') or 'no status'}); "
                        "a transport failure is never a refusal")


def body(r: dict[str, Any]) -> dict[str, Any]:
    b = r.get("body")
    return b if isinstance(b, dict) else {}


def approved_item(s: Stack, c: dict[str, Any]) -> tuple[str, str]:
    """Assess, require review, approve as the approver. Returns (item, proposal)."""
    a = s.step("assess", **c)
    item, pid = body(a).get("review_item_id"), body(a).get("proposal_id")
    if not a["ok"] or not item:
        raise StepError(f"expected a review item, got {_brief(a)}")
    ap = s.step("approve", item_id=item)
    if not ap["ok"]:
        raise StepError(f"approve failed: {_brief(ap)}")
    return item, pid


def executed(r: dict[str, Any]) -> bool:
    te = body(r).get("tool_execution")
    return bool(isinstance(te, dict) and te.get("executed") is True
                and isinstance(te.get("result"), dict) and te["result"].get("status") == "APPLIED")


# -- cases -------------------------------------------------------------------

@dataclass
class Ctx:
    s: Stack
    expected_effects: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


Case = Callable[[Ctx], tuple[bool, str]]


def at05_no_direct_route(c: Ctx) -> tuple[bool, str]:
    env = c.s.step("env")
    probe = c.s.step("direct_actuate")
    held = set(env["present"]) & {"TWIN_ACTUATOR_TOKEN", "TWIN_CONTROL_TOKEN"}
    ok = probe["reachable"] is False and not held
    return ok, f"reachable={probe['reachable']} ({probe.get('error')}), write creds held={sorted(held)}"


def at02_approved_effect_once(c: Ctx) -> tuple[bool, str]:
    s = c.s
    before, sp0 = revision(s), setpoint(s, "ZONE-01")
    target = round(sp0 - 0.5, 1)
    cl = call("ZONE-01", target)
    a = s.step("assess", **cl)
    item = body(a).get("review_item_id")
    if body(a).get("decision") not in ("verify", "escalate") or not item:
        return False, f"write was not routed to review: {_brief(a)}"
    op_ap = s.step("approve", item_id=item, role="operator")
    ex_early = s.step("execute", item_id=item, **cl)
    ap = s.step("approve", item_id=item)
    ex = s.step("execute", item_id=item, **cl)
    after, sp1 = revision(s), setpoint(s, "ZONE-01")
    ts = body(ex).get("toolspec", {})
    c.notes["at02_proposal_id"] = body(ex).get("proposal_id")
    c.notes["at02_item"], c.notes["at02_call"] = item, cl
    ok = (op_ap["status"] == 403 and not executed(ex_early) and ap["ok"] and executed(ex)
          and after == before + 1 and abs(sp1 - target) < 1e-9 and ts.get("enforced") is True)
    if ok:
        c.expected_effects += 1
    return ok, (f"operator-approve={op_ap['status']} execute-before-approval={ex_early['status']} "
                f"approve={ap['status']} execute={ex['status']} executed={executed(ex)} "
                f"revision {before}->{after} setpoint {sp0}->{sp1} "
                f"toolspec_enforced={ts.get('enforced')}")


def at02b_effect_recorded(c: Ctx) -> tuple[bool, str]:
    pid = c.notes.get("at02_proposal_id")
    cl = c.notes.get("at02_call")
    if not pid or not cl:
        return False, "AT-02 produced no proposal"
    eff = c.s.step("record_effect", proposal_id=pid, tool_name=cl["tool_name"],
                   arguments=cl["arguments"])
    env = c.s.step("envelope", proposal_id=pid)
    lc = c.s.step("lifecycle", proposal_id=pid)
    events = [e.get("event") or e.get("type") for e in body(lc).get("events", [])]
    eb = body(env).get("effect") or {}
    outcome = eb.get("effect_outcome")
    source = (eb.get("ledger_entry") or {}).get("effect_verdict_source") or eff.get("verdict_source")
    ok = (lc["ok"] and bool(events) and eff.get("ok") is True
          and eff.get("effect_status") == "EFFECT_VERIFIED"
          and source == "computed")
    return ok, (f"lifecycle={lc['status']} events={events}; "
                f"effect_status={eff.get('effect_status')} verdict_source={source} outcome={outcome}")


def at04_replay_refused(c: Ctx) -> tuple[bool, str]:
    s = c.s
    item, cl = c.notes.get("at02_item"), c.notes.get("at02_call")
    if not item:
        return False, "AT-02 produced no item"
    before = revision(s)
    r1 = s.step("execute", item_id=item, **cl)
    s.service("restart", "remora")
    took = s.wait_ready(True)
    r2 = s.step("execute", item_id=item, **cl)
    after = revision(s)
    ok = (not r1["ok"] and not r2["ok"] and not executed(r1) and not executed(r2)
          and after == before)
    return ok, (f"replay={r1['status']} {body(r1).get('detail')}; after restart ({took}s) "
                f"replay={r2['status']} {body(r2).get('detail')}; revision {before}->{after}")


def at03_tamper_after_approval(c: Ctx) -> tuple[bool, str]:
    s = c.s
    s.ctl("advance", "900")
    sp0 = setpoint(s, "ZONE-01")
    approved = call("ZONE-01", round(sp0 - 0.5, 1))
    item, _ = approved_item(s, approved)
    before = revision(s)
    tampered = call("ZONE-01", round(sp0 - 1.0, 1))
    other_zone = call("ZONE-02", round(sp0 - 0.5, 1))
    t1 = s.step("execute", item_id=item, **tampered)
    t2 = s.step("execute", item_id=item, **other_zone)
    after = revision(s)
    rej = s.step("reject", item_id=item, reason="AT-03 cleanup: tampered calls refused")
    ok = not executed(t1) and not executed(t2) and after == before
    return ok, (f"tampered-value={t1['status']} {body(t1).get('detail')}; "
                f"other-zone={t2['status']} {body(t2).get('detail')}; revision {before}->{after}; "
                f"cleanup reject={rej['status']}")


def at08_contract_refusals(c: Ctx) -> tuple[bool, str]:
    s = c.s
    before = revision(s)
    probes = {
        "unknown_tool": call("ZONE-01", 20.0, tool="open_valve"),
        "unknown_zone": call("ZONE-09", 20.0),
        "above_bounds": call("ZONE-01", 30.0),
        "below_bounds": call("ZONE-01", 10.0),
        "extra_argument": call("ZONE-01", 20.0, override_interlock=True),
        "wrong_target": {**call("ZONE-01", 20.0), "target_environment": "production"},
        "no_work_order": call("ZONE-01", 20.0, wo=None),
    }
    seen, bad = {}, []
    for name, cl in probes.items():
        r = s.step("assess", **cl)
        require_answer(r, f"AT-08 {name}")
        b = body(r)
        seen[name] = f"{r['status']}:{b.get('decision') or b.get('detail')}"
        # A refusal is an HTTP refusal or ABSTAIN; anything with a token or a
        # review item could still lead to an effect.
        if r["ok"] and (b.get("execution_token") or b.get("review_item_id")):
            if name != "no_work_order":
                bad.append(name)
            else:
                c.notes["no_work_order_item"] = b.get("review_item_id")
    after = revision(s)
    ok = not bad and after == before
    return ok, f"{seen}; admitted={bad}; revision {before}->{after}"


def at08b_guard_refusal_is_not_success(c: Ctx) -> tuple[bool, str]:
    """REMORA approves a 2.0 degC move its contract allows; the twin's 1.0 delta
    interlock refuses it. The refusal must surface as no effect, not success."""
    s = c.s
    s.ctl("advance", "900")
    sp0 = setpoint(s, "ZONE-02")
    target = round(min(sp0 + 2.0, 23.0), 1)
    cl = call("ZONE-02", target, wo=WO2)
    a = s.step("assess", **cl)
    require_answer(a, "AT-08b assess")
    if not a["ok"]:
        return True, f"refused before dispatch: {a['status']} {body(a).get('detail')}"
    item = body(a).get("review_item_id")
    if not item:
        return False, f"no review item: {_brief(a)}"
    s.step("approve", item_id=item)
    before = revision(s)
    ex = s.step("execute", item_id=item, **cl)
    require_answer(ex, "AT-08b execute")
    after = revision(s)
    ok = not executed(ex) and after == before
    return ok, (f"execute={ex['status']} executed={executed(ex)} "
                f"detail={body(ex).get('detail') or body(ex).get('outcome')}; revision {before}->{after}")


def at10_stop_switch(c: Ctx) -> tuple[bool, str]:
    s = c.s
    s.ctl("advance", "900")
    sp0 = setpoint(s, "ZONE-01")
    cl = call("ZONE-01", round(sp0 + 0.5, 1))
    item, _ = approved_item(s, cl)
    s.ctl("stop", "--actor", "acceptance-operator")
    before = revision(s)
    ex = s.step("execute", item_id=item, **cl)
    after = revision(s)
    s.ctl("release", "--actor", "acceptance-operator")
    retry = s.step("execute", item_id=item, **cl)
    after_release = revision(s)
    ok = not executed(ex) and after == before and not executed(retry) and after_release == before
    return ok, (f"execute under stop={ex['status']} executed={executed(ex)}; revision {before}->{after}; "
                f"after release re-execute={retry['status']} executed={executed(retry)} "
                f"(no replay shortcut); revision ->{after_release}")


def at11_store_outage(c: Ctx) -> tuple[bool, str]:
    s = c.s
    s.ctl("advance", "900")
    sp0 = setpoint(s, "ZONE-01")
    cl = call("ZONE-01", round(sp0 - 0.5, 1))
    item, _ = approved_item(s, cl)
    before = revision(s)
    s.service("stop", "postgres")
    try:
        unready_after = s.wait_ready(False, timeout_s=60)
        a = s.step("assess", **call("ZONE-02", 21.0, wo=WO2))
        ex = s.step("execute", item_id=item, **cl)
        require_answer(a, "AT-11 assess during the outage")
        require_answer(ex, "AT-11 execute during the outage")
        during = revision(s)
    finally:
        s.service("start", "postgres")
    ready_after = s.wait_ready(True, timeout_s=120)
    after_recovery = revision(s)
    ex2 = s.step("execute", item_id=item, **cl)
    final = revision(s)
    ok = (not a["ok"] and not executed(ex) and during == before and after_recovery == before
          and (final == before + (1 if executed(ex2) else 0)))
    if executed(ex2):
        c.expected_effects += 1
    return ok, (f"unready after {unready_after}s; assess={a['status']} execute={ex['status']} "
                f"during-revision={during}; ready again after {ready_after}s; nothing applied "
                f"on recovery ({after_recovery}); explicit re-execute={ex2['status']} "
                f"executed={executed(ex2)} final={final}")


def at13_concurrent_same_zone(c: Ctx) -> tuple[bool, str]:
    """Two approved changes to one zone, executed concurrently: at most one effect."""
    s = c.s
    s.ctl("advance", "1800")
    sp0 = setpoint(s, "ZONE-02")
    c1, c2 = call("ZONE-02", round(sp0 + 0.5, 1), wo=WO2), call("ZONE-02", round(sp0 + 1.0, 1), wo=WO2)
    i1, _ = approved_item(s, c1)
    i2, _ = approved_item(s, c2)
    before = revision(s)
    procs = [subprocess.Popen([*s.compose, "exec", "-T", "harness", "python", "-m",
                               "runner.harness_step", "execute",
                               json.dumps({"item_id": i, **cl})],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env={**os.environ, "MSYS_NO_PATHCONV": "1"})
             for i, cl in ((i1, c1), (i2, c2))]
    outs = [json.loads([ln for ln in p.communicate(timeout=120)[0].splitlines()
                        if ln.startswith("{")][-1]) for p in procs]
    after = revision(s)
    applied = sum(1 for o in outs if executed(o))
    c.expected_effects += applied
    s.trace.append({"op": "concurrent_execute", "results": [_brief(o) for o in outs]})
    ok = applied <= 1 and after == before + applied
    return ok, (f"executed={[executed(o) for o in outs]} "
                f"details={[body(o).get('detail') or (body(o).get('tool_execution') or {}).get('result', {}).get('reason') for o in outs]} "
                f"revision {before}->{after}")


def at_limits_budget(c: Ctx) -> tuple[bool, str]:
    """ZONE-03's REMORA budget (0.5 degC/h) is tighter than the twin guard (1.0
    delta, 2 changes/h): the second 0.5 move inside the hour must be refused by
    REMORA, although the twin interlock alone would allow it."""
    s = c.s
    s.ctl("advance", "1800")
    sp0 = setpoint(s, "ZONE-03")
    first = call("ZONE-03", round(sp0 - 0.5, 1), wo=WO3)
    item, _ = approved_item(s, first)
    ex1 = s.step("execute", item_id=item, **first)
    if executed(ex1):
        c.expected_effects += 1
    s.ctl("advance", "900")
    before = revision(s)
    second = call("ZONE-03", round(sp0 - 1.0, 1), wo=WO3)
    a2 = s.step("assess", **second)
    detail = body(a2).get("detail") or body(a2).get("decision")
    executed2 = False
    if a2["ok"] and body(a2).get("review_item_id"):
        s.step("approve", item_id=body(a2)["review_item_id"])
        ex2 = s.step("execute", item_id=body(a2)["review_item_id"], **second)
        executed2 = executed(ex2)
        detail = f"{detail}; execute={ex2['status']} {body(ex2).get('detail')}"
        if executed2:
            c.expected_effects += 1
    after = revision(s)
    ok = executed(ex1) and not executed2 and after == before
    return ok, f"first executed={executed(ex1)}; second: {a2['status']} {detail}; revision {before}->{after}"


def at_sdk_typed_approve(c: Ctx) -> tuple[bool, str]:
    """The typed SDK must parse a successful approval (deferred audit ref).

    Earlier cases (AT-13) spend ZONE-02's hourly movement budget, so a fixed
    absolute target can be refused with limit_exceeded before the SDK is even
    exercised. Start a new budget window and move half a degree from where
    the zone is, so the case tests the SDK and not the budget."""
    s = c.s
    s.ctl("advance", "3600")
    sp0 = setpoint(s, "ZONE-02")
    a = s.step("assess", **call("ZONE-02", round(min(sp0 + 0.5, 23.0), 1), wo=WO2))
    item = body(a).get("review_item_id")
    if not item:
        return False, f"no review item: {_brief(a)}"
    r = s.step("sdk_approve", item_id=item)
    s.step("reject", item_id=item, reason="SDK probe cleanup")
    return bool(r["ok"]), f"sdk approve: {r}"


def _execute_detail(r: dict[str, Any]) -> str:
    te = body(r).get("tool_execution") if isinstance(body(r).get("tool_execution"), dict) else {}
    res = te.get("result") if isinstance(te.get("result"), dict) else {}
    return str(res.get("reason") or te.get("error") or body(r).get("detail") or body(r).get("outcome") or "")[:160]


def at09_basis_faults(c: Ctx) -> tuple[bool, str]:
    """An approved write whose basis is faulty is refused before it has an effect: the zone's sensor frozen
    past the observation age, its reading in another unit, or from another point (TFM). Each fault is set on
    the twin's control listener after approval, so REMORA approved a change that the plant must not take."""
    s = c.s
    seen, ok = {}, True
    for kind, value in (("sensor_freeze", None), ("reading_unit", "degF"), ("reading_tfm", "+DEMO01=564.001-RT699")):
        s.ctl("advance", "900")
        sp0 = setpoint(s, "ZONE-01")
        cl = call("ZONE-01", round(sp0 - 0.5, 1) if sp0 >= 19.0 else round(sp0 + 0.5, 1))
        item, pid = approved_item(s, cl)
        s.ctl("fault", kind, "--zone", "ZONE-01", "--actor", "acceptance", *(("--value", value) if value else ()))
        try:
            if kind == "sensor_freeze":
                s.ctl("advance", "900")  # past the twin's 600 s observation age
            before = revision(s)
            ex = s.step("execute", item_id=item, **cl)
            require_answer(ex, f"AT-09 {kind} execute")
            after = revision(s)
        finally:
            s.ctl("clear-faults", "--actor", "acceptance")
        op = s.step("operation", operation_id=pid) if pid else {}
        twin_reason = body(op).get("reason") if op.get("ok") else None
        seen[kind] = f"{ex['status']} executed={executed(ex)} twin={twin_reason} ({_execute_detail(ex)})"
        ok = ok and not executed(ex) and after == before
    s.ctl("advance", "900")  # a fresh sample before the next case
    return ok, f"{seen}"


def at12_timeout_after_actuation(c: Ctx) -> tuple[bool, str]:
    """The twin applies the write and answers only after the adapter's timeout. The outcome must not be
    reported as a clean success, the effect must exist exactly once, and nothing may repeat it: a later
    execute of the same approval adds no second effect. The unknown outcome is then reconciled from the
    plant: REMORA refuses a reviewer who claims it did not execute (the twin's log disagrees) and settles
    it when the claim matches."""
    s = c.s
    pending_before = {o.get("operation_id") for o in body(s.step("unresolved")).get("operations", [])}
    s.ctl("advance", "900")
    sp0 = setpoint(s, "ZONE-01")
    target = round(sp0 + 0.5, 1) if sp0 < 21.0 else round(sp0 - 0.5, 1)
    cl = call("ZONE-01", target)
    item, pid = approved_item(s, cl)
    s.ctl("fault", "ack_delay", "--value", "8", "--actor", "acceptance")
    before = revision(s)
    try:
        ex = s.step("execute", item_id=item, **cl)
        require_answer(ex, "AT-12 execute")
    finally:
        time.sleep(4)  # the late acknowledgement finishes; the fault was one shot
        s.ctl("clear-faults", "--actor", "acceptance")
    after = revision(s)
    op = s.step("operation", operation_id=pid) if pid else {}
    pending = [o.get("operation_id") for o in body(s.step("unresolved")).get("operations", [])
               if o.get("operation_id") not in pending_before]
    evidence = f"twin-op:{pid}"
    wrong = right = {}
    if len(pending) == 1:
        wrong = s.step("resolve", operation_id=pending[0], executed=False, evidence_ref=evidence,
                       reason="AT-12: claims no effect")
        right = s.step("resolve", operation_id=pending[0], executed=True, evidence_ref=evidence,
                       reason="AT-12: the twin's operation log records the write as applied")
        require_answer(wrong, "AT-12 resolve executed=false")
        require_answer(right, "AT-12 resolve executed=true")
    still = [o.get("operation_id") for o in body(s.step("unresolved")).get("operations", [])
             if o.get("operation_id") in pending]
    retry = s.step("execute", item_id=item, **cl)
    final = revision(s)
    if after == before + 1:
        c.expected_effects += 1
    twin_status = body(op).get("status") if op.get("ok") else None
    ok = (not executed(ex) and after == before + 1 and final == after and twin_status == "APPLIED"
          and abs(setpoint(s, "ZONE-01") - target) < 1e-9 and len(pending) == 1
          and wrong.get("status") == 409 and right.get("status") == 200 and not still and not executed(retry))
    return ok, (f"execute={ex['status']} executed={executed(ex)} ({_execute_detail(ex)}); twin record={twin_status}; "
                f"revision {before}->{after}; unknown operations={len(pending)}; resolve executed=false -> "
                f"{wrong.get('status')} {body(wrong).get('detail')}; executed=true -> {right.get('status')}; "
                f"still unresolved={len(still)}; re-execute={retry['status']} executed={executed(retry)} "
                f"({_execute_detail(retry)}); revision ->{final}")


def at_sr06_no_self_approval(c: Ctx) -> tuple[bool, str]:
    """SR-06 (F-06): the proposer's container holds no approver token, so it cannot approve its own change, as
    approver (no credential) or as operator (REMORA's 403); the reviewer's container holds no operator token."""
    s = c.s
    s.ctl("advance", "900")
    sp0 = setpoint(s, "ZONE-01")
    cl = call("ZONE-01", round(sp0 - 0.5, 1) if sp0 >= 19.5 else round(sp0 + 0.5, 1))
    a = s.step("assess", **cl)
    item = body(a).get("review_item_id")
    if not item:
        return False, f"no review item: {_brief(a)}"
    before = revision(s)
    as_approver = s.step("approve", item_id=item, role="approver", via="harness")
    as_operator = s.step("approve", item_id=item, role="operator", via="harness")
    ex = s.step("execute", item_id=item, **cl)
    env = s.step("env")
    renv = s.step("env", via="reviewer")
    s.step("reject", item_id=item, reason="SR-06 probe cleanup")
    after = revision(s)
    ok = (as_approver.get("error") == "no_credential" and as_operator.get("status") == 403 and not executed(ex)
          and after == before and "REMORA_APPROVER_TOKEN" not in env.get("present", [])
          and "REMORA_OPERATOR_TOKEN" not in renv.get("present", []))
    return ok, (f"harness approve as approver: {as_approver.get('error') or as_approver.get('status')}; as operator: "
                f"{as_operator.get('status')}; execute={ex['status']} executed={executed(ex)}; harness holds "
                f"{sorted(env.get('present', []))}; reviewer holds {sorted(renv.get('present', []))}; "
                f"revision {before}->{after}")


def at_sr05_signed_work_orders(c: Ctx) -> tuple[bool, str]:
    """SR-05 (F-05): a work order counts only when the authority signed it. A day plan the authority issues is
    grounded; an entry anyone else writes into the table, or a signed one changed afterwards, grounds nothing
    and the independent reviewer refuses both, while it approves the signed one."""
    s = c.s
    s.ctl("advance", "900")
    day = str(s.step("status")["body"]["sim_utc"])[:10]
    issued = s.authority("plan-P1", day)
    table_path = INTENT_TABLE
    table = json.loads(table_path.read_text("utf-8"))
    signed = table[issued["intent_ref"]]
    forged_ref, tampered_ref = f"PLAN-{day}-FORGED", f"PLAN-{day}-TAMPERED"
    forged = {k: v for k, v in signed.items() if k != "signature"}
    tampered = {**signed, "permits": {"set_zone_setpoint": {"ZONE-01": [23.0], "ZONE-02": [23.0]}}}
    table[forged_ref], table[tampered_ref] = forged, tampered
    table_path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", "utf-8")  # as a compromised proposer
    sp0 = setpoint(s, "ZONE-01")
    target = 20.0 if abs(sp0 - 20.0) <= 1.0 and sp0 != 20.0 else (21.0 if sp0 != 21.0 else 20.0)
    seen, ok = {}, True
    before = revision(s)
    for name, ref, want in (("signed", issued["intent_ref"], True), ("forged", forged_ref, False),
                            ("tampered", tampered_ref, False)):
        a = s.step("assess", **call("ZONE-01", target, wo=ref))
        require_answer(a, f"SR-05 assess {name}")
        b = body(a)
        grounded = (b.get("semantic") or {}).get("argument_values_grounded")
        item, pid = b.get("review_item_id"), b.get("proposal_id")
        d = s.step("decide", item_id=item, proposal_id=pid, call=call("ZONE-01", target, wo=ref)) if item else {}
        if d.get("approved"):
            s.step("reject", item_id=item, reason="SR-05 probe: approved, not executed")
        seen[name] = f"grounded={grounded} reviewer={'approve' if d.get('approved') else 'reject'} ({d.get('reason')})"
        ok = ok and bool(d.get("approved")) is want and (grounded is True) is want
        if name == "forged" and item:
            # A proposer that lies to the reviewer: it presents the signed plan for the forged proposal's item.
            lie = s.step("decide", item_id=item, proposal_id=pid, call=call("ZONE-01", target, wo=issued["intent_ref"]))
            seen["lie"] = f"reviewer={'approve' if lie.get('approved') else 'reject'} ({lie.get('reason')})"
            ok = ok and not lie.get("approved")
    after = revision(s)
    del table[forged_ref], table[tampered_ref]
    table_path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", "utf-8")
    return ok and after == before, f"{seen}; signed by {issued['key_id']}; revision {before}->{after}"


def at_audit_chain_valid(c: Ctx) -> tuple[bool, str]:
    v = c.s.step("audit_verify")
    b = body(v)
    return bool(v["ok"] and b.get("valid") is True), f"audit verify={v['status']} {b}"


def at_no_unexplained_effect(c: Ctx) -> tuple[bool, str]:
    final = revision(c.s)
    start = c.notes["start_revision"]
    return (final - start == c.expected_effects,
            f"revision {start}->{final}, effects expected by the cases: {c.expected_effects}")


CASES: list[tuple[str, str, Case]] = [
    ("AT-05", "harness has no route or credential to the actuator", at05_no_direct_route),
    ("AT-02", "approved write applies exactly once; operator cannot approve", at02_approved_effect_once),
    ("AT-02b", "the dispatch is in the proposal lifecycle", at02b_effect_recorded),
    ("AT-04", "replay refused, also after a REMORA restart", at04_replay_refused),
    ("AT-03", "changed arguments after approval are refused", at03_tamper_after_approval),
    ("AT-08", "unknown tool/zone, out of bounds, extra args, wrong target refused", at08_contract_refusals),
    ("AT-08b", "a twin interlock refusal is no effect, not success", at08b_guard_refusal_is_not_success),
    ("AT-10", "stop switch blocks an approved dispatch; no replay after release", at10_stop_switch),
    ("AT-11", "store outage: unready, no effect, no blind resend on recovery", at11_store_outage),
    ("AT-13", "two concurrent approved writes to one zone: at most one effect", at13_concurrent_same_zone),
    ("LIM-01", "REMORA's cumulative budget refuses what the interlock would allow", at_limits_budget),
    ("SDK-01", "typed SDK approve parses a successful approval", at_sdk_typed_approve),
    ("AT-09", "stale, false-unit or wrong-point basis: refused before any effect", at09_basis_faults),
    ("AT-12", "timeout after actuation: not success, one effect, never repeated", at12_timeout_after_actuation),
    ("SR-06", "the proposer cannot approve its own change; the reviewer cannot propose", at_sr06_no_self_approval),
    ("SR-05", "only a work order the authority signed grounds a write and passes the reviewer",
     at_sr05_signed_work_orders),
    ("AUD-01", "tenant audit chain verifies", at_audit_chain_valid),
    ("INV-ALL", "no effect beyond those the cases expected", at_no_unexplained_effect),
]
NOT_RUN = {
    "AT-14..15": "Miniflare / Wrangler parity: no Worker in this repository yet (OT-006)",
    "AT-16": "governed vs baseline comparison: runner.ab_compare, separate suite",
}


def run_cases(ctx: Ctx, cases: list[tuple[str, str, Case]], only: list[str] | None = None) -> list[dict[str, Any]]:
    """Run the cases in order. A case that crashed, or during which REMORA did not answer a call, is FAIL."""
    results: list[dict[str, Any]] = []
    for cid, title, fn in cases:
        if only is not None and cid not in only and cid != "INV-ALL":
            continue
        started, unanswered = time.monotonic(), len(ctx.s.no_answer)
        try:
            ok, detail = fn(ctx)
            outcome = "PASS" if ok else "FAIL"
        except Exception as e:  # a crashed case is a failure, never a skip
            outcome, detail = "FAIL", f"case_error: {type(e).__name__}: {e}"
        missed = ctx.s.no_answer[unanswered:]
        if missed and outcome == "PASS":
            outcome = "FAIL"
            detail = (f"REMORA did not answer {len(missed)} call(s) ({', '.join(m['op'] for m in missed)}): "
                      f"a transport failure is never a refusal; {detail}")
        results.append({"case_id": cid, "title": title, "outcome": outcome, "detail": detail,
                        "elapsed_s": round(time.monotonic() - started, 1)})
        print(f"[{outcome}] {cid} {title}\n        {detail}", flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runner.acceptance")
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", default="artifacts")
    p.add_argument("--project", default=os.environ.get("COMPOSE_PROJECT_NAME", ""))
    p.add_argument("--only", nargs="*", default=None)
    p.add_argument("--allow-failing", nargs="*", default=[],
                   help="case ids whose FAIL is a known, recorded finding (still reported as FAIL)")
    a = p.parse_args(argv)
    compose = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.governed.yaml"]
    if a.project:
        compose[2:2] = ["-p", a.project]
    s = Stack(compose)
    ctx = Ctx(s)
    s.wait_ready(True)
    ctx.notes["start_revision"] = revision(s)
    results = run_cases(ctx, CASES, a.only)
    for cid, why in NOT_RUN.items():
        results.append({"case_id": cid, "outcome": "NOT_RUN", "detail": why})
    failed = [r for r in results if r["outcome"] == "FAIL"]
    passed = [r for r in results if r["outcome"] == "PASS"]
    claims = {
        "authorized_effect_through_remora_dispatcher":
            "TESTED_DOCKER" if any(r["case_id"] == "AT-02" and r["outcome"] == "PASS" for r in results)
            else "NOT_ESTABLISHED",
        "real_building_savings": "NOT_ESTABLISHED",
        "physical_ot_safety": "NOT_ESTABLISHED",
    }
    root = write_bundle(a.out, suite_id=SUITE_ID, run_id=a.run_id,
                        manifest_extra={"scope": "SIMULATED_CONTROLLED_PILOT", "vantage":
                                        "same_test_deployment", "compose": compose},
                        metrics={"cases": len(results), "passed": len(passed),
                                 "failed": len(failed),
                                 "not_run": sum(1 for r in results if r["outcome"] == "NOT_RUN"),
                                 "effects_applied": ctx.expected_effects},
                        trace=s.trace, claims=claims, results=results)
    print(f"acceptance: {len(passed)} PASS, {len(failed)} FAIL, "
          f"{len(NOT_RUN)} NOT_RUN; evidence in {root}")
    unexpected = [r for r in failed if r["case_id"] not in a.allow_failing]
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
