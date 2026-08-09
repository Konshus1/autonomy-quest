#!/usr/bin/env python3
"""Two real Loop.cycle turns proving prediction -> refutation -> replacement -> support.

Uses a deterministic scripted subscription executor: no metered/model calls.  Set
AQ_PROOF_DISABLE_LEARNING=1 only as the deliberate completion-test negative control.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import prompts
from runner.config import (Autonomy, Boundaries, BudgetCfg, Curiosity, Engine, Instance,
                           Measure, Mission, Models, Money, Surfaces)
from runner.db import Db
from runner.executor import Usage
from runner.loop import Loop


class TwoCycleExecutor:
    def __init__(self, db: Db, prefix: str):
        self.db, self.prefix, self.decisions, self.acts = db, prefix, 0, 0
        self.concerns = [
            {"concern_id": "serve_mission_progress", "kind": "serve",
             "predicate": {"metric": "mission_delta", "operator": ">=", "value": 0}},
            {"concern_id": "must_not_overshoot", "kind": "must_not_harm",
             "predicate": {"metric": "mission_value", "operator": "<=", "value": 2.0}},
        ]

    def _plan(self, replacement=False):
        steps = ([
            {"step_id": "s1", "subgoal_id": "bounded", "action": f"{self.prefix}-a1",
             "expected_effect": f"{self.prefix}-e1", "expected_direction": "toward", "scope": {},
                     "blast_radius": {"affected_entities_upper_bound": 0, "public_or_unbounded": False,
                                      "production_wide": False, "irreversible_external_write": False}},
            {"step_id": "s2", "subgoal_id": "bounded", "action": f"{self.prefix}-a2",
             "expected_effect": f"{self.prefix}-e2", "expected_direction": "toward", "scope": {},
                     "blast_radius": {"affected_entities_upper_bound": 0, "public_or_unbounded": False,
                                      "production_wide": False, "irreversible_external_write": False}},
        ] if not replacement else [
            {"step_id": "s2-replacement", "subgoal_id": "bounded", "action": f"{self.prefix}-a3",
             "expected_effect": f"{self.prefix}-e3", "expected_direction": "toward", "scope": {},
                     "blast_radius": {"affected_entities_upper_bound": 0, "public_or_unbounded": False,
                                      "production_wide": False, "irreversible_external_write": False}},
        ])
        return {
            "goal_predicate": self.concerns[0]["predicate"],
            "expected_expense_usd": 0,
            "mission_concerns": self.concerns,
            "subgoals": [{"subgoal_id": "bounded",
                "success_predicate": self.concerns[1]["predicate"],
                "serves_concern_ids": [c["concern_id"] for c in self.concerns]}],
            "steps": steps,
        }

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            replacement = self.decisions > 0
            self.decisions += 1
            return {"do_nothing": False, "kind": "proof-cycle",
                    "summary": "replacement from s2" if replacement else "two-step causal proof",
                    "rationale": "exercise real durable plan learning", "reversible": True,
                    "spends_money": False, "touches_human": False, "commits": False,
                    "plan": self._plan(replacement)}, Usage()
        if schema is prompts.ACT_SCHEMA:
            self.acts += 1
            self.db._q(f"UPDATE {self.prefix}_metric SET value=%s", (self.acts,))
            if self.acts == 1:
                results = [
                    {"step_id": "s1", "executed": True, "confirmed": True,
                     "harmed_concern_ids": [], "evidence": "metric-row:1"},
                    {"step_id": "s2", "executed": True, "confirmed": False,
                     "harmed_concern_ids": [], "evidence": "missing-direct-effect"},
                ]
                outcome = "s1 confirmed; s2 direct effect refuted"
            else:
                results = [{"step_id": "s2-replacement", "executed": True, "confirmed": True,
                            "harmed_concern_ids": [], "evidence": "metric-row:2"}]
                outcome = "replacement from s2 confirmed"
            return {"outcome": outcome, "succeeded": True, "evidence": f"{self.prefix}_metric",
                    "observed_metrics": {"mission_delta": 1, "mission_value": self.acts},
                    "step_results": results}, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {"insight": "replace only the refuted step and preserve confirmed work",
                    "evidence": "durable prediction and metric rows", "scope": "local",
                    "confidence": 0.9}, Usage()
        raise AssertionError(schema)


def main():
    os.environ["AQ_CAUSAL_AUTOMINE"] = "0"
    url = os.environ.get("AQ_DB_URL", "postgresql://aq:aq-local-only@postgres:5432/aq")
    db = Db(url, graph="none")
    prefix = "m7_" + uuid.uuid4().hex[:10]
    work_ids, edge_ids, principle_ids = [], [], []
    db._q(f"CREATE TABLE {prefix}_metric(value integer NOT NULL)")
    db._q(f"INSERT INTO {prefix}_metric VALUES(0)")
    try:
        for i in (1, 2, 3):
            principle_id = f"{prefix}-p{i}"; principle_ids.append(principle_id)
            db._q("INSERT INTO causal_principle(principle_id,principle_text,source,status) "
                  "VALUES(%s,%s,'m7-real-cycle','provisional')", (principle_id, f"proof p{i}"))
            edge = db._q(
                "INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,"
                "mechanism_description,predicted_certainty,scope_conditions,supplier_principle_id) "
                "VALUES(%s,%s,'proof','toward','scripted direct effect',0.8,'{}',%s) RETURNING edge_id",
                (f"{prefix}-a{i}", f"{prefix}-e{i}", principle_id), one=True)["edge_id"]
            edge_ids.append(edge)
        inst = Instance(
            mission=Mission("prove two-cycle localized learning",
                Measure("proof value", f"SELECT value FROM {prefix}_metric", target=2),
                "two turns", Boundaries()),
            engine=Engine(mode="subscription"), template="proof", datastore={"graph": "none"},
            models=Models(), budget=BudgetCfg(money=Money(0, 50), autonomy=Autonomy()),
            surfaces=Surfaces(notify_channel="none"), curiosity=Curiosity(enabled=False),
        )
        if os.environ.get("AQ_PROOF_DISABLE_LEARNING") == "1":
            db.resolve_plan_predictions = lambda *args, **kwargs: None
        ex = TwoCycleExecutor(db, prefix); loop = Loop(inst, db, ex)
        first = loop.cycle(); second = loop.cycle()
        work_ids = [first.work_id, second.work_id]
        events = db._q(
            "SELECT principle_id,delta,outcome_kind FROM principle_support_event "
            "WHERE principle_id=ANY(%s) ORDER BY support_event_id", (principle_ids,))
        replan = db._q(
            "SELECT failed_step_id,preserved_prefix_step_ids,status,replacement_work_id "
            "FROM plan_replan WHERE source_work_id=%s", (first.work_id,), one=True)
        counters = db._q(
            "SELECT principle_id,status,evidence_for_count,evidence_against_count "
            "FROM causal_principle WHERE principle_id=ANY(%s) ORDER BY principle_id", (principle_ids,))
        predictions_before_runs = db._q(
            "SELECT bool_and(p.asserted_at < r.started_at) AS ok FROM planning_prediction p "
            "JOIN runs r ON r.work_id=p.work_id WHERE p.work_id=ANY(%s)", (work_ids,), one=True)["ok"]
        evaluations = db._q(
            "SELECT goal_satisfied,steps_confirmed,steps_executed,intent_satisfied "
            "FROM plan_evaluation WHERE work_id=ANY(%s) ORDER BY run_id", (work_ids,))
        output = {"cycles": len(work_ids), "predictions_before_runs": predictions_before_runs,
                  "events": [dict(x) for x in events], "replan": dict(replan) if replan else None,
                  "principles": [dict(x) for x in counters],
                  "evaluations": [dict(x) for x in evaluations]}
        print(json.dumps(output, sort_keys=True, default=str))
        assert len(events) == 3 and [e["delta"] for e in events] == [1, -1, 1]
        assert replan and replan["failed_step_id"] == "s2"
        assert replan["preserved_prefix_step_ids"] == ["s1"]
        assert replan["status"] == "planned" and replan["replacement_work_id"] == second.work_id
        assert predictions_before_runs is True
        assert all(row["status"] == "provisional" for row in counters)  # DR12
        assert evaluations[0]["steps_confirmed"] == 1 and evaluations[0]["steps_executed"] == 2
        assert evaluations[1]["steps_confirmed"] == evaluations[1]["steps_executed"] == 1
    finally:
        if work_ids: db._q("DELETE FROM work WHERE id=ANY(%s)", (work_ids,))
        if edge_ids: db._q("DELETE FROM causal_edge WHERE edge_id=ANY(%s)", (edge_ids,))
        if principle_ids: db._q("DELETE FROM causal_principle WHERE principle_id=ANY(%s)", (principle_ids,))
        db._q(f"DROP TABLE IF EXISTS {prefix}_metric")


if __name__ == "__main__":
    main()
