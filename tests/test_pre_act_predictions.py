"""M3: direction assertions are durable before every ACT path."""
from datetime import datetime, timezone

from runner import prompts
from runner.executor import Usage
from runner.loop import Loop
from tests.test_loop_approval_execution import FakeDb, instance


class ParkDb(FakeDb):
    def park_for_human(self, work_id, note=None):
        self.events.append("park")

    def known_plan_relations(self):
        return [
            {"edge_id": 1, "source_action": action, "direct_effect": effect,
             "relation_direction": "toward", "mechanism_description": "known",
             "scope_conditions": '{"domain":"test"}', "predicted_certainty": 0.8}
            for action, effect in (("search", "facts collected"), ("cross-check", "facts verified"))
        ]


class PlannedExecutor:
    def __init__(self, db, *, touches_human=False):
        self.db = db
        self.touches_human = touches_human
        self.acted_prompt = None

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.DECIDE_SCHEMA:
            return {
                "do_nothing": False, "kind": "research", "summary": "collect and verify",
                "rationale": "supply grounded evidence", "reversible": True,
                "spends_money": False, "touches_human": self.touches_human, "commits": False,
                "plan": {"goal_predicate": "two facts are verified", "steps": [
                    {"step_id": "collect", "action": "search", "expected_effect": "facts collected",
                     "expected_direction": "toward", "scope": {"domain": "test"}},
                    {"step_id": "verify", "action": "cross-check", "expected_effect": "facts verified",
                     "expected_direction": "toward", "scope": {"domain": "test"}},
                ]},
            }, Usage()
        if schema is prompts.ACT_SCHEMA:
            self.acted_prompt = prompt
            self.db.events.append("act")
            return {"outcome": "done", "succeeded": True, "evidence": "artifact"}, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {"insight": "verification helps", "evidence": "artifact",
                    "scope": "local", "confidence": 0.8}, Usage()
        raise AssertionError(schema)


def test_autonomous_multistep_assertions_precede_act():
    db = FakeDb()
    loop = Loop(instance(), db, PlannedExecutor(db))
    loop.cycle()
    assert db.events.index("prediction") < db.events.index("act")
    assert [row[2]["step_id"] for row in db.plan_predictions] == ["collect", "verify"]
    assert all(row[2]["expected_direction"] == "toward" for row in db.plan_predictions)
    # With no known relations the callable records localized absence; it never fabricates coverage.
    assert all(row[3].edge_state.value == "absent" for row in db.plan_predictions)


def test_approved_legacy_work_gets_prediction_before_act():
    approved = {
        "id": 42, "kind": "delivery", "summary": "approved legacy work",
        "rationale": "human approved", "requires_human": True, "status": "pending",
        "approved_at": datetime.now(timezone.utc),
    }
    db = FakeDb(approved)
    loop = Loop(instance(), db, PlannedExecutor(db))
    loop.cycle()
    assert db.events.index("prediction") < db.events.index("act")
    assert db.plan_predictions[0][2]["step_id"] == "legacy-approved-step"


def test_parked_work_keeps_predictions_even_without_act():
    db = ParkDb()
    loop = Loop(instance(), db, PlannedExecutor(db, touches_human=True))
    # Default act-reversible parks a human-touching action.
    loop.cycle()
    assert db.plan_predictions
    assert "act" not in db.events
