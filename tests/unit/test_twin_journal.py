"""SDD-OT-002 SR-15 (F-17), SEC-03 "WAL journal with crash test": the operation log is an append-only, chained
journal next to the snapshot. Persisting an operation writes one line instead of rewriting every record, a
crash between snapshot and journal is recovered from the snapshot's tail, and any other change to the journal
refuses the resume."""
from __future__ import annotations

import json

import pytest

from tests.conftest import BASELINE, CONFIG, cmd, plant_fingerprint
from twin.model import StateStoreFailed, Twin, TwinError, load_config
from twin.scenarios import load_scenario

KEY = "snapshot-key-0123456789abcdef0123456789abcdef"


def _twin(path, key=KEY) -> Twin:
    return Twin(load_config(CONFIG), load_scenario(BASELINE), snapshot_path=path, snapshot_key=key)


def _journal(path):
    return path.with_name(path.name + ".journal")


def _lines(path) -> list[str]:
    return _journal(path).read_text("utf-8").splitlines()


SENT: dict[str, dict] = {}


def _three_ops(p) -> Twin:
    tw = _twin(p)
    for op, zone, sp in (("op-j-00001", "ZONE-01", 20.5), ("op-j-00002", "ZONE-02", 20.5),
                         ("op-j-00003", "ZONE-01", 30.0)):  # the third is refused: out of bounds
        SENT[op] = cmd(tw, op, zone=zone, sp=sp)
        tw.apply_setpoint(SENT[op])
    return tw


def test_the_snapshot_names_the_journal_and_holds_no_log(tmp_path):
    p = tmp_path / "snap.json"
    tw = _three_ops(p)
    snap = json.loads(p.read_text("utf-8"))
    assert "operations" not in snap and snap["journal"]["count"] == 3
    assert snap["journal"]["tail"]["operation_id"] == "op-j-00003"
    assert len(_lines(p)) == 3
    b = _twin(p)
    assert list(b.operations) == list(tw.operations) and b.revision == tw.revision == 2
    assert b.apply_setpoint(SENT["op-j-00001"])[1] is True  # still replay-safe


def test_persisting_an_operation_costs_one_line_not_the_whole_log(tmp_path):
    p = tmp_path / "snap.json"
    tw = _twin(p)
    sizes = []
    for i in range(400):
        tw.apply_setpoint(cmd(tw, f"op-cost-{i:05d}", sp=40.0))  # refused, recorded
        if i in (9, 399):
            sizes.append((p.stat().st_size, _journal(p).stat().st_size))
    (snap10, j10), (snap400, j400) = sizes
    assert snap400 - snap10 < 256, (snap10, snap400)       # the snapshot does not grow with the log
    assert (j400 - j10) / 390 < 1024                       # the journal grows by one short line each
    assert len(_lines(p)) == 400


def test_crash_between_snapshot_and_journal_is_recovered_from_the_tail(tmp_path):
    p = tmp_path / "snap.json"
    tw = _three_ops(p)
    lines = _lines(p)
    _journal(p).write_text("\n".join(lines[:-1]) + "\n", "utf-8")  # the last append never happened
    b = _twin(p)
    assert "op-j-00003" in b.operations and len(_lines(p)) == 3
    assert any(e.get("event") == "journal_recovered" and e["operation_id"] == "op-j-00003" for e in b.events)
    assert b.apply_setpoint(SENT["op-j-00003"])[1] is True


def test_a_crash_that_applied_an_effect_without_its_journal_line_keeps_effect_and_record(tmp_path):
    p = tmp_path / "snap.json"
    tw = _twin(p)
    tw.apply_setpoint(cmd(tw, "op-k-00001", sp=20.5))
    _journal(p).write_text("", "utf-8")
    b = _twin(p)
    assert b.revision == 1 and b.operations["op-k-00001"].status == "APPLIED"
    assert b.zones["ZONE-01"].setpoint_c == 20.5


def test_a_torn_last_line_counts_as_absent(tmp_path):
    p = tmp_path / "snap.json"
    _three_ops(p)
    lines = _lines(p)
    _journal(p).write_text("\n".join(lines[:-1]) + "\n" + lines[-1][:40], "utf-8")  # torn mid-append
    b = _twin(p)
    assert len(b.operations) == 3 and len(_lines(p)) == 3
    assert all(json.loads(x) for x in _lines(p))


@pytest.mark.parametrize("doctor", [
    lambda ls: [ls[0].replace('"REJECTED"', '"APPLIED"'), *ls[1:]],
    lambda ls: [ls[0], *ls[2:]],                     # a middle line removed
    lambda ls: ls[:-2],                              # two lines removed
    lambda ls: [*ls, ls[-1]],                        # a line added
    lambda ls: [ls[1], ls[0], *ls[2:]],              # reordered
    lambda ls: [ls[0], ls[1], ls[2].replace("op-j-00003", "op-j-99999")],
])
def test_any_other_change_to_the_journal_refuses_the_resume(tmp_path, doctor):
    p = tmp_path / "snap.json"
    _three_ops(p)
    lines = _lines(p)
    bad = doctor(lines)
    if bad == lines:  # the first case only bites a REJECTED line; make sure it changed something
        bad = [lines[0].replace("20.5", "22.5"), *lines[1:]]
    _journal(p).write_text("\n".join(bad) + "\n", "utf-8")
    with pytest.raises(TwinError, match="journal"):
        _twin(p)


def test_the_snapshots_journal_reference_is_authenticated(tmp_path):
    p = tmp_path / "snap.json"
    _three_ops(p)
    snap = json.loads(p.read_text("utf-8"))
    snap["journal"]["count"] = 2
    p.write_text(json.dumps(snap), "utf-8")
    with pytest.raises(TwinError, match="integrity"):
        _twin(p)


def test_a_snapshot_with_the_log_inline_resumes_and_writes_the_journal(tmp_path):
    p = tmp_path / "snap.json"
    tw = _twin(tmp_path / "other.json")
    tw.apply_setpoint(cmd(tw, "op-l-00001", sp=20.5))
    tw.apply_setpoint(cmd(tw, "op-l-00002", zone="ZONE-02", sp=20.5))
    old = tw.snapshot()  # the form written before SR-15: every record inline
    old["integrity"] = {"alg": "HMAC-SHA256", "mac": tw._mac(old)}
    p.write_text(json.dumps(old), "utf-8")
    b = _twin(p)
    assert b.revision == 2 and not _journal(p).exists()
    b.apply_setpoint(cmd(b, "op-l-00003", zone="ZONE-03", sp=21.5))
    assert len(_lines(p)) == 3
    c = _twin(p)
    assert list(c.operations) == ["op-l-00001", "op-l-00002", "op-l-00003"] and c.revision == 3


def test_a_failed_journal_append_keeps_the_durable_operation_and_stops_further_writes(tmp_path, monkeypatch):
    p = tmp_path / "snap.json"
    tw = _twin(p)

    def broken(_record):
        raise OSError("disk full")
    monkeypatch.setattr(tw, "_append_journal_one", broken)
    rec, _ = tw.apply_setpoint(cmd(tw, "op-m-00001", sp=20.5))
    assert rec.status == "APPLIED"  # the snapshot holds it as its tail
    fp = plant_fingerprint(tw)
    with pytest.raises(StateStoreFailed):
        tw.apply_setpoint(cmd(tw, "op-m-00002", zone="ZONE-02", sp=20.5))
    assert plant_fingerprint(tw) == fp
    b = _twin(p)
    assert b.operations["op-m-00001"].status == "APPLIED" and len(_lines(p)) == 1


def test_without_a_key_the_journal_is_still_chained_and_checked(tmp_path):
    p = tmp_path / "snap.json"
    tw = _twin(p, key="")
    tw.apply_setpoint(cmd(tw, "op-n-00001", sp=20.5))
    tw.apply_setpoint(cmd(tw, "op-n-00002", zone="ZONE-02", sp=20.5))
    lines = _lines(p)
    _journal(p).write_text(lines[1] + "\n", "utf-8")
    with pytest.raises(TwinError, match="journal"):
        _twin(p, key="")
