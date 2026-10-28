"""The work-order authority (SDD-OT-002 SR-05, F-05): the only holder of the key that makes a work order.

    python /app/ot_authority.py issue --kind plan-P1 --day 2026-01-15

It runs in its own container (``authority`` in compose.governed.yaml, the REMORA execution image), the only
one with the work-order seed mounted. A caller names the kind of order and the day; the authority builds the
entry from its fixed templates (``ot_day_plans``), signs it (``adapter.work_orders``) and files it in the intent
table REMORA reads. It never signs content it is handed, so a proposer that may ask for a day plan still cannot
choose what the plan permits.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from adapter import work_orders as wo
import ot_day_plans

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEED = Path(os.environ.get("OT_WORK_ORDER_SEED_FILE", "/run/authority/work_order.seed"))
TABLE = Path(os.environ.get("OT_INTENT_TABLE", "/etc/remora-ot/ot_intents.json"))


def write_table(table_path: Path, table: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=table_path.parent, prefix=".intents-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(table, indent=2, sort_keys=True) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, table_path)


def issue(kind: str, day: str, seed_path: Path = SEED, table_path: Path = TABLE) -> dict[str, str]:
    if kind not in ot_day_plans.KINDS:
        raise ValueError(f"unknown kind {kind!r}; the authority issues {sorted(ot_day_plans.KINDS)}")
    if not DAY.fullmatch(day):
        raise ValueError(f"day {day!r} is not YYYY-MM-DD")
    key = wo.key_from_seed(seed_path.read_text("utf-8"))
    ref, entry = ot_day_plans.KINDS[kind](day)
    signed = wo.sign_entry(ref, entry, key)
    table = json.loads(table_path.read_text("utf-8")) if table_path.exists() else {}
    table[ref] = signed
    write_table(table_path, table)
    return {"intent_ref": ref, "key_id": signed["signature"]["key_id"], "digest": wo.entry_digest(ref, signed)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ot_authority")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("issue")
    i.add_argument("--kind", required=True)
    i.add_argument("--day", required=True)
    a = p.parse_args(argv)
    try:
        print(json.dumps(issue(a.kind, a.day), sort_keys=True))
    except (ValueError, OSError) as e:
        print(json.dumps({"error": str(e)}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
