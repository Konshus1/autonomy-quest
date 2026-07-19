from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from runner.config import (
    Autonomy,
    Boundaries,
    BudgetCfg,
    Curiosity,
    Engine,
    Instance,
    Measure,
    Mission,
    Models,
    Money,
    Surfaces,
)
from runner.executor import Usage
from runner.loop import Loop
from runner import prompts


def instance() -> Instance:
    return Instance(
        mission=Mission(
            objective="test mission",
            measure=Measure(what="count", where="select 1", target=10, goal="reach_and_maintain"),
            horizon="ongoing",
            boundaries=Boundaries(),
        ),
        engine=Engine(mode="subscription"),
        template="running-a-business",
        datastore={"graph": "none"},
        models=Models(),
        budget=BudgetCfg(money=Money(daily_soft_usd=0, monthly_hard_usd=0), autonomy=Autonomy()),
        surfaces=Surfaces(notify_channel="none"),
        curiosity=Curiosity(enabled=False),
    )


class FakeDb:
    def __init__(self, approved_row=None):
        self.approved_row = approved_row
        self.started = []
        self.recorded = []
        self.completed = []
        self.learned = []
        self.created = []
        self.measure = Decimal("1")

    def sum_cost(self, since):
        return Decimal("0")

    def orphaned_runs(self):
        return []

    def pending_reflection(self):
        return None

    def read_measure(self, measure):
        value = self.measure
        self.measure += Decimal("1")
        return value

    def record_measurement(self, measure, value):
        pass

    def measure_trend(self, measure, days=14):
        return []

    def open_work(self):
        return []

    def recent_runs(self, limit=10):
        return []

    def recent_productivity(self, limit=15):
        return []

    def live_learnings(self, limit=50):
        return []

    def awaiting_human(self):
        return []

    def approved_work(self):
        return self.approved_row

    def create_work(self, kind, summary, rationale, requires_human=False):
        self.created.append((kind, summary, rationale, requires_human))
        return 99

    def start_run(self, work_id):
        self.started.append(work_id)
        return 501

    def record_act(self, run_id, outcome, succeeded, evidence):
        self.recorded.append((run_id, outcome, succeeded, evidence))

    @contextmanager
    def tx(self):
        yield object()

    def complete_run(self, cur, run_id, **kw):
        self.completed.append((run_id, kw))

    def write_learning(self, cur, run_id, insight, evidence, scope, confidence):
        self.learned.append((run_id, insight, evidence, scope, confidence))
        return 701

    def graph_link(self, cur, run_id, work_id, learning_id):
        pass

    def beat(self, state, detail="", retry_after_s=None):
        pass


class FakeExecutor:
    def __init__(self):
        self.schemas = []

    def run(self, prompt, schema, tier="working"):
        self.schemas.append(schema)
        if schema is prompts.ACT_SCHEMA:
            return {"outcome": "approved work executed", "succeeded": True, "evidence": "artifact"}, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {
                "insight": "approved work should execute after approval",
                "evidence": "run 501",
                "scope": "local",
                "confidence": 0.8,
            }, Usage()
        if schema is prompts.DECIDE_SCHEMA:
            return {"do_nothing": True}, Usage()
        raise AssertionError(f"unexpected schema: {schema}")


class LoopApprovalExecutionTests(unittest.TestCase):
    def test_approved_pending_work_executes_before_deciding_new_work(self):
        approved = {
            "id": 42,
            "kind": "delivery",
            "summary": "approved parked work",
            "rationale": "human said yes",
            "requires_human": True,
            "status": "pending",
            "approved_at": datetime.now(timezone.utc),
        }
        db = FakeDb(approved)
        ex = FakeExecutor()

        cycle = Loop(instance(), db, ex).cycle()

        self.assertEqual(cycle.work_id, 42)
        self.assertEqual(db.started, [42])
        self.assertEqual([s for s in ex.schemas], [prompts.ACT_SCHEMA, prompts.REFLECT_SCHEMA])
        self.assertFalse(db.created)

    def test_unapproved_pending_work_is_not_treated_as_human_approval(self):
        db = FakeDb(approved_row=None)
        ex = FakeExecutor()

        cycle = Loop(instance(), db, ex).cycle()

        self.assertIsNone(cycle)
        self.assertEqual(db.started, [])
        self.assertEqual(ex.schemas, [prompts.DECIDE_SCHEMA])


if __name__ == "__main__":
    unittest.main()
