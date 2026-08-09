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
    def __init__(self, inst: Instance, summary: str, expected_cost: float):
        self.inst = inst
        self.summary = summary
        self.expected_cost = expected_cost
        self.calls: list[str] = []

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            self.calls.append("decide")
            concerns = [
                {"concern_id": "serve_mission_progress", "kind": "serve",
                 "predicate": {"metric": "mission_delta", "operator": ">=", "value": 0}},
                {"concern_id": "must_not_overshoot", "kind": "must_not_harm",
                 "predicate": {"metric": "mission_value", "operator": "<=", "value": 20}},
            ]
            return {
                "do_nothing": False, "kind": "TEST_ONLY", "summary": self.summary,
                "rationale": f"{self.summary} rationale persisted before act",
                "reversible": True, "spends_money": self.expected_cost > 0,
                "touches_human": True, "commits": False,
                "plan": {
                    "goal_predicate": {"metric": "mission_delta", "operator": ">=", "value": 0},
                    "expected_expense_usd": self.expected_cost,
                    "mission_concerns": concerns,
                    "subgoals": [
                        {"subgoal_id": "proof", "success_predicate":
                            {"metric": "mission_delta", "operator": ">=", "value": 0},
                            "serves_concern_ids": ["serve_mission_progress"]},
                        {"subgoal_id": "safety", "success_predicate":
                            {"metric": "mission_value", "operator": "<=", "value": 20},
                            "serves_concern_ids": ["must_not_overshoot"]},
                    ],
                    "steps": [{"step_id": "proof-step", "subgoal_id": "proof",
                        "action": "TEST_ONLY", "expected_effect": "mission_delta",
                        "expected_direction": "toward", "scope": {"conditions": []},
                        "blast_radius": {"affected_entities_upper_bound": 1,
                            "public_or_unbounded": False, "production_wide": False,
                            "irreversible_external_write": False}}],
                },
            }, Usage(cost_usd=Decimal("0"))
        if schema is prompts.ACT_SCHEMA:
            self.calls.append("act")
            return {"outcome": f"{self.summary} observed outcome", "succeeded": True,
                    "evidence": "TEST_ONLY bounded gate proof",
                    "observed_metrics": {"mission_delta": 0, "mission_value": 0},
                    "step_results": [{"step_id": "proof-step", "executed": True,
                        "confirmed": True, "evidence": "TEST_ONLY bounded gate proof",
                        "harmed_concern_ids": []}]}, Usage(cost_usd=Decimal("0"))
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
    db._q("DELETE FROM causal_edge WHERE source_action='TEST_ONLY' AND direct_effect='mission_delta'")
    db._q(
        "INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,"
        "mechanism_description,scope_conditions,predicted_certainty) "
        "VALUES('TEST_ONLY','mission_delta','count of active paying customers','toward',"
        "'TEST_ONLY deterministic scripted gate proof','{}',1.0)"
    )

    results = []
    for summary, cost in ((PREFIXES[0], 5), (PREFIXES[1], 5), (PREFIXES[2], 1)):
        ex = Scripted(inst, summary, cost)
        cycle = Loop(inst, db, ex).cycle()
        row = db._q("SELECT id,status,expected_expense_usd,gate_reason FROM work WHERE summary=%s", (summary,), one=True)
        results.append((summary, ex.calls, cycle is not None, dict(row)))

    db._q("DELETE FROM causal_edge WHERE source_action='TEST_ONLY' AND direct_effect='mission_delta'")
    for summary, calls, completed, row in results:
        print(f"{summary}|calls={','.join(calls)}|completed={completed}|status={row['status']}|cost={row['expected_expense_usd']}|reason={row['gate_reason']}")


if __name__ == "__main__":
    main()
