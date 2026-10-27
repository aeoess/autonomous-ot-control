"""Inline admission edges (ADR-004, arm C): asked after REMORA decided, able only to add refusals.

The harness consults an inline edge for a proposal only after REMORA has
approved it and the reviewer has approved it. The edge answers ``permit`` or
``refuse`` with a reason within a time budget. ``refuse`` stops the call:
the approval is rejected with the edge's reason. ``permit`` changes nothing.
A timeout, a connection error or a malformed answer counts as ``refuse`` and
is reported separately as ``edge_unavailable``, so an outage is never counted
as a safety win. The edge never sees a credential and never reaches the plant.

The composition rule is a pure function here (`allowed`) so a test can pin
it: an edge can never turn a refusal of REMORA's or the reviewer's into an
effect, whatever it answers.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_TIMEOUT_S = 2.0
VERDICTS = ("permit", "refuse")


@dataclass(frozen=True)
class Decision:
    edge_id: str
    verdict: str          # permit | refuse
    reason: str
    available: bool       # False: timeout, error or malformed answer (the verdict is then refuse)
    latency_ms: float

    @property
    def label(self) -> str:
        """The ledger's refused_by value for this decision when it stops a call."""
        return f"edge:{self.edge_id}:{'edge_unavailable' if not self.available else self.reason}"

    def to_dict(self) -> dict[str, Any]:
        return {"edge_id": self.edge_id, "verdict": self.verdict, "reason": self.reason,
                "available": self.available, "latency_ms": round(self.latency_ms, 1)}


def allowed(remora_and_reviewer_allow: bool, decision: Decision | None) -> bool:
    """The composition rule: an effect needs REMORA and the reviewer *and* every inline edge.

    With no edge consulted the answer is REMORA's and the reviewer's alone.
    An edge's permit never adds anything; an edge's refuse (or unavailability)
    removes. Nothing an edge says can make a refused call go through.
    """
    if not remora_and_reviewer_allow:
        return False
    if decision is None:
        return True
    return decision.verdict == "permit" and decision.available


class InlineEdge:
    def __init__(self, edge_id: str, url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 client: httpx.Client | None = None) -> None:
        self.edge_id, self.url, self.timeout_s = edge_id, url, timeout_s
        self._client = client or httpx.Client(timeout=timeout_s)

    @classmethod
    def from_edge_dir(cls, edge_dir: Path, *, url: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S,
                      client: httpx.Client | None = None) -> "InlineEdge":
        edge = json.loads((edge_dir / "edge.json").read_text("utf-8"))
        if edge.get("mode") != "inline":
            raise ValueError(f"{edge_dir.name} is not an inline edge")
        target = url or (edge.get("entrypoint") or {}).get("http")
        if not target:
            raise ValueError(f"{edge_dir.name}: no entrypoint.http and no url given")
        return cls(edge["edge_id"], target, timeout_s=timeout_s, client=client)

    def decide(self, call: dict[str, Any], context: dict[str, Any]) -> Decision:
        t0 = time.perf_counter()
        try:
            r = self._client.post(self.url, json={"call": call, "context": context}, timeout=self.timeout_s)
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code != 200:
                return Decision(self.edge_id, "refuse", f"http_{r.status_code}", False, ms)
            body = r.json()
            verdict = body.get("verdict") if isinstance(body, dict) else None
            if verdict not in VERDICTS:
                return Decision(self.edge_id, "refuse", "malformed_answer", False, ms)
            return Decision(self.edge_id, verdict, str(body.get("reason") or verdict), True, ms)
        except (httpx.HTTPError, ValueError) as e:
            ms = (time.perf_counter() - t0) * 1000
            return Decision(self.edge_id, "refuse", f"{type(e).__name__}", False, ms)
