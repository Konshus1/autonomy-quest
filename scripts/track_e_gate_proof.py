#!/usr/bin/env python3
"""Disposable Track E gate-path proof. Never business data; requires explicit opt-in."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if os.environ.get("AQ_TRACK_E_TEST") != "1":
    raise SystemExit("refusing: set AQ_TRACK_E_TEST=1; this writes TEST_ONLY rows")

from runner.config import Instance
from runner.db import Db
from runner.executor import Usage
from runner.loop import Loop
from runner import prompts

PREFIXES = ("TEST_ONLY_GATE_APPROVE_$5", "TEST_ONLY_GATE_REJECT_$5", "TEST_ONLY_AUTONOMOUS_$1")

class Scripted:
    def __init__(self, summary: str, expected_cost: float):
        self.summary = summary
        self.expected_cost = expected_cost
        self.calls: list[str] = []

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            self.calls.append("decide")
            return {
                "do_nothing": False, "kind": "TEST_ONLY", "summary": self.summary,
                "rationale": f"{self.summary} rationale persisted before act",
                "reversible": True, "spends_money": self.expected_cost > 0,
                "expected_cost_usd": self.expected_cost, "blast_radius": 0.1,
                "touches_human": True, "commits": False,
            }, Usage(cost_usd=Decimal("0"))
        if schema is prompts.ACT_SCHEMA:
            self.calls.append("act")
            return {"outcome": f"{self.summary} observed outcome", "succeeded": True,
                    "evidence": "TEST_ONLY bounded gate proof"}, Usage(cost_usd=Decimal("0"))
        if schema is prompts.REFLECT_SCHEMA:
            self.calls.append("reflect")
            return {"insight": f"{self.summary} learning", "evidence": "TEST_ONLY gate proof",
                    "scope": "local", "confidence": 0.5}, Usage(cost_usd=Decimal("0"))
        raise AssertionError(schema)


def main() -> None:
    inst = Instance.load("instance.yaml")
    db = Db(os.environ["AQ_DB_URL"], graph=inst.datastore.get("graph", "age"))
    # Clean only our unmistakable disposable rows, never business state.
    rows = db._q("SELECT id FROM work WHERE summary = ANY(%s)", (list(PREFIXES),)) or []
    for row in rows:
        db._q("DELETE FROM work WHERE id=%s", (row["id"],))

    results = []
    for summary, cost in ((PREFIXES[0], 5), (PREFIXES[1], 5), (PREFIXES[2], 1)):
        ex = Scripted(summary, cost)
        cycle = Loop(inst, db, ex).cycle()
        row = db._q("SELECT id,status,expected_cost_usd,gate_reason FROM work WHERE summary=%s", (summary,), one=True)
        results.append((summary, ex.calls, cycle is not None, dict(row)))

    for summary, calls, completed, row in results:
        print(f"{summary}|calls={','.join(calls)}|completed={completed}|status={row['status']}|cost={row['expected_cost_usd']}|reason={row['gate_reason']}")


if __name__ == "__main__":
    main()
