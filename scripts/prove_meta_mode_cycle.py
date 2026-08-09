#!/usr/bin/env python3
"""Real PostgreSQL + Loop proof of compare-not-order and explicit post-observation stop.

The executor is deterministic and subscription-shaped: this proof exercises the production Loop,
Db, migrations, transactions, and evaluator without a metered model call.
"""
from __future__ import annotations

import json, os, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import prompts
from runner.config import (Autonomy, Boundaries, BudgetCfg, Curiosity, Engine, Instance,
                           Measure, Mission, Models, Money, Surfaces)
from runner.db import Db
from runner.executor import Usage
from runner.loop import Loop


class Executor:
    def __init__(self, db, prefix):
        self.db, self.prefix = db, prefix
        self.meta_calls = 0

    @staticmethod
    def option(mode, direct, info, cost, instruction="acquire"):
        return {"mode": mode, "state": "bounded",
                "direct_value": {"low": direct[0], "high": direct[1]},
                "information_value": {"low": info[0], "high": info[1]},
                "cost": {"low": cost[0], "high": cost[1]},
                "evidence_refs": [f"real-db-forecast:{mode}"],
                "rationale": f"bounded forecast for {mode}", "instruction": instruction,
                "block_reason": None,
                "wake_condition": "new mission or causal evidence" if mode == "abstain" else None}

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            step = {"step_id": "unsupported", "subgoal_id": "goal",
                    "action": f"{self.prefix}-target", "expected_effect": "mission improves",
                    "expected_direction": "toward", "scope": {},
                    "blast_radius": {"affected_entities_upper_bound": 0,
                                     "public_or_unbounded": False, "production_wide": False,
                                     "irreversible_external_write": False}}
            concerns = [
                {"concern_id": "serve_mission_progress", "kind": "serve",
                 "predicate": {"metric": "mission_delta", "operator": ">=", "value": 0}},
                {"concern_id": "must_not_overshoot", "kind": "must_not_harm",
                 "predicate": {"metric": "mission_value", "operator": "<=", "value": 1}},
            ]
            plan = {"goal_predicate": concerns[0]["predicate"], "expected_expense_usd": 0,
                    "mission_concerns": concerns,
                    "subgoals": [
                        {"subgoal_id": "goal", "success_predicate": concerns[0]["predicate"],
                         "serves_concern_ids": ["serve_mission_progress"]},
                        {"subgoal_id": "bounded", "success_predicate": concerns[1]["predicate"],
                         "serves_concern_ids": ["must_not_overshoot"]}], "steps": [step]}
            return {"do_nothing": False, "kind": "impasse-proof", "summary": "unsupported target",
                    "rationale": "force a localized plan impasse", "reversible": True,
                    "spends_money": False, "touches_human": False, "commits": False,
                    "plan": plan}, Usage()
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            if self.meta_calls == 1:
                # Cheapest compute: cost 1, net 1. Experiment: cost 3, net 5.
                options = [self.option("internal_computation", (2,2), (0,0), (1,1), "think"),
                           self.option("environment_experiment", (4,4), (4,4), (3,3), "probe"),
                           self.option("human_question", (1,1), (2,2), (1,1), "ask"),
                           self.option("human_demonstration", (1,2), (1,2), (2,2), "demonstrate"),
                           self.option("autonomous_practice", (2,3), (2,2), (1,2), "practice"),
                           self.option("goal_relaxation", (1,1), (0,0), (2,2), "relax"),
                           self.option("abstain", (0,0), (0,0), (0,0), "")]
            else:
                # Observation made every remaining channel known-worse than stopping.
                options = [self.option("internal_computation", (0,1), (0,0), (2,2), "think"),
                           self.option("environment_experiment", (0,1), (0,1), (3,3), "probe"),
                           self.option("human_question", (0,1), (0,1), (3,3), "ask"),
                           self.option("human_demonstration", (0,1), (0,1), (3,4), "demonstrate"),
                           self.option("autonomous_practice", (0,1), (0,1), (3,4), "practice"),
                           self.option("goal_relaxation", (0,1), (0,0), (2,2), "relax"),
                           self.option("abstain", (0,0), (0,0), (0,0), "")]
            return {"options": options}, Usage()
        if schema is prompts.ACT_SCHEMA:
            assert "reversible tool/environment experiment" in prompt
            self.db._q(f"INSERT INTO {self.prefix}_observations(note) VALUES('bounded probe: no support')")
            return {"outcome": "bounded probe found no decision-supporting relation",
                    "succeeded": True, "evidence": f"table:{self.prefix}_observations",
                    "observed_metrics": [{"metric": "mission_delta", "value": 0}],
                    "step_results": [{"step_id": "unsupported", "executed": False,
                                      "confirmed": False, "evidence": "target not executed",
                                      "harmed_concern_ids": []}], "causal_proposals": []}, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {"insight": "the bounded probe did not justify more acquisition",
                    "evidence": f"table:{self.prefix}_observations", "scope": "local",
                    "confidence": 0.9}, Usage()
        raise AssertionError(schema)


def main():
    os.environ["AQ_CAUSAL_AUTOMINE"] = "0"
    db = Db(os.environ.get("AQ_DB_URL", "postgresql://kthomas@localhost/aq_voi_test"), graph="none")
    prefix = "voi_" + uuid.uuid4().hex[:10]
    db._q(f"CREATE TABLE {prefix}_metric(value integer NOT NULL)")
    db._q(f"INSERT INTO {prefix}_metric VALUES(0)")
    db._q(f"CREATE TABLE {prefix}_observations(note text NOT NULL)")
    work_id = None
    try:
        inst = Instance(Mission("resolve a real plan impasse by net value",
                    Measure("proof value", f"SELECT value FROM {prefix}_metric", target=1),
                    "two observations", Boundaries()),
                Engine(mode="subscription"), "proof", {"graph": "none"}, Models(),
                BudgetCfg(money=Money(0, 50), autonomy=Autonomy()),
                Surfaces(notify_channel="none"), Curiosity(enabled=False))
        ex = Executor(db, prefix); loop = Loop(inst, db, ex)
        first = loop.cycle(); assert first is not None; work_id = first.work_id
        second = loop.cycle(); assert second is None
        decisions = db._q(
            "SELECT observation_index,policy_version,decision,chosen_mode,chosen_score,stop_reason,scorecards "
            "FROM impasse_meta_mode_decision WHERE work_id=%s ORDER BY observation_index", (work_id,))
        acquisitions = db._q(
            "SELECT rung,status,result FROM plan_acquisition WHERE work_id=%s ORDER BY acquisition_id",
            (work_id,))
        ordering = db._q(
            "SELECT d.created_at < r.started_at AS decision_before_act, p.asserted_at < r.started_at AS prediction_before_act "
            "FROM impasse_meta_mode_decision d JOIN plan_acquisition a ON a.meta_mode_decision_id=d.decision_id "
            "JOIN runs r ON r.work_id=d.work_id JOIN planning_prediction p ON p.work_id=d.work_id "
            "AND p.step_id=a.action_step_id WHERE d.work_id=%s", (work_id,), one=True)
        output = {"work_id": work_id, "meta_calls": ex.meta_calls,
                  "decisions": [dict(x) for x in decisions],
                  "acquisitions": [dict(x) for x in acquisitions], "ordering": dict(ordering)}
        print(json.dumps(output, sort_keys=True, default=str))
        assert [d["decision"] for d in decisions] == ["acquire", "abstain"]
        assert decisions[0]["chosen_mode"] == "environment_experiment"
        assert str(decisions[0]["chosen_score"]) == "5"
        assert decisions[1]["chosen_mode"] == "abstain"
        assert decisions[1]["stop_reason"] == "no_option_worth_cost"
        assert len(acquisitions) == 1 and acquisitions[0]["status"] == "completed"
        assert ordering["decision_before_act"] and ordering["prediction_before_act"]
        assert db._q(f"SELECT count(*) AS n FROM {prefix}_observations", one=True)["n"] == 1
        assert db._q(f"SELECT value FROM {prefix}_metric", one=True)["value"] == 0
    finally:
        if work_id: db._q("DELETE FROM work WHERE id=%s", (work_id,))
        db._q(f"DROP TABLE IF EXISTS {prefix}_observations")
        db._q(f"DROP TABLE IF EXISTS {prefix}_metric")

if __name__ == "__main__": main()
