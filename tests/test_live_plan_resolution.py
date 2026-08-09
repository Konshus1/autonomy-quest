from runner import prompts
from runner.executor import Usage
from runner.loop import Loop
from tests.test_loop_approval_execution import FakeDb, instance


class ResolutionDb(FakeDb):
    def known_plan_relations(self):
        return [{"edge_id": i, "source_action": f"a{i}", "direct_effect": f"e{i}",
                 "relation_direction": "toward", "mechanism_description": "known",
                 "scope_conditions": None, "predicted_certainty": 0.8,
                 "supplier_principle_id": f"p{i}"} for i in (1,2,3)]


class RefutingExecutor:
    def __init__(self): self.act_count = 0
    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            concerns=[
                {"concern_id":"serve_mission_progress","kind":"serve",
                 "predicate":{"metric":"mission_delta","operator":">=","value":0}},
                {"concern_id":"must_not_overshoot","kind":"must_not_harm",
                 "predicate":{"metric":"mission_value","operator":"<=","value":10.0}},
            ]
            return {"do_nothing":False,"kind":"batch","summary":"three-step plan","rationale":"test",
                    "reversible":True,"spends_money":False,"touches_human":False,"commits":False,
                    "plan":{"goal_predicate":concerns[0]["predicate"],"expected_expense_usd": 0,
                    "mission_concerns":concerns,
                            "subgoals":[{"subgoal_id":"sg","success_predicate":concerns[1]["predicate"],
                                         "serves_concern_ids":[c["concern_id"] for c in concerns]}],
                            "steps":[{"step_id":f"s{i}","subgoal_id":"sg","action":f"a{i}",
                                      "expected_effect":f"e{i}","expected_direction":"toward","scope":{},"blast_radius":{"affected_entities_upper_bound":0,"public_or_unbounded":False,"production_wide":False,"irreversible_external_write":False}}
                                     for i in (1,2,3)]}},Usage()
        if schema is prompts.ACT_SCHEMA:
            self.act_count += 1
            return {"outcome":"s2 refuted","succeeded":True,"evidence":"rows",
                    "observed_metrics":{"mission_delta":1,"mission_value":2},
                    "step_results":[
                        {"step_id":"s1","executed":True,"confirmed":True,"harmed_concern_ids":[],"evidence":"e1"},
                        {"step_id":"s2","executed":True,"confirmed":False,"harmed_concern_ids":[],"evidence":"e2"},
                        {"step_id":"s3","executed":False,"confirmed":False,"harmed_concern_ids":[],"evidence":""},
                    ]},Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {"insight":"s2 direction failed","evidence":"e2","scope":"local","confidence":0.9},Usage()
        raise AssertionError(schema)


def test_live_loop_localizes_refutation_and_next_decision_links_replan():
    db=ResolutionDb(); ex=RefutingExecutor(); loop=Loop(instance(),db,ex)
    first=loop.cycle()
    assert first is not None
    assert db.replans[0]["failed_step_id"] == "s2"
    assert db.replans[0]["preserved_prefix_step_ids"] == ["s1"]
    assert [r.outcome_kind for r in db.support_events] == ["supported","direct_effect_refuted","not_executed"]
    second=loop.cycle()
    assert second is not None
    assert db.replans[0]["status"] == "planned"
    assert db.replans[0]["replacement_work_id"] == 99
