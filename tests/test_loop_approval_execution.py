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
        self.failed = []
        self.learned = []
        self.created = []
        self.measure = Decimal("1")
        self.pending_after_act = None
        self.plan_predictions = []
        self.events = []
        self.intent_checks = []
        self.plan_evaluations = []
        self.replans = []
        self.support_events = []
        self.acquisitions = []
        self.autonomous_pending = None

    def sum_cost(self, since):
        return Decimal("0")

    def sum_plan_expense(self):
        return sum((row[1] for row in getattr(self, "spend_reservations", {}).values()), Decimal("0"))

    def reserve_plan_expense(self, work_id, amount):
        if not hasattr(self, "spend_reservations"): self.spend_reservations = {}
        self.spend_reservations.setdefault(work_id, ("reserved", Decimal(str(amount))))

    def mark_plan_expense_incurred(self, work_id):
        if hasattr(self, "spend_reservations") and work_id in self.spend_reservations:
            _, amount = self.spend_reservations[work_id]
            self.spend_reservations[work_id] = ("incurred", amount)

    def orphaned_runs(self):
        return []

    def pending_reflection(self):
        return self.pending_after_act

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

    def pending_replans(self, limit=10):
        return self.replans[:limit]

    def link_pending_replan(self, replacement_work_id, replacement_plan_id):
        if self.replans:
            self.replans[0]["status"] = "planned"
            self.replans[0]["replacement_work_id"] = replacement_work_id

    def approved_work(self):
        if not self.approved_row:
            return None
        if self.approved_row.get("status") != "pending" or self.approved_row.get("approved_at") is None:
            return None
        return self.approved_row

    def pending_autonomous_work(self):
        return self.autonomous_pending

    def create_work(self, kind, summary, rationale, requires_human=False, plan_id=None, plan=None,
                    expected_expense_usd=None, blast_radius_level=None, blast_radius_basis=None,
                    gate_policy_version=None, gate_reason=None, **decision_properties):
        self.created.append((kind, summary, rationale, requires_human, plan_id, plan,
                             expected_expense_usd, blast_radius_level, blast_radius_basis,
                             gate_policy_version, gate_reason))
        self.autonomous_pending = {
            "id": 99, "kind": kind, "summary": summary, "rationale": rationale,
            "requires_human": requires_human, "plan_id": plan_id, "plan": plan,
            "expected_expense_usd": expected_expense_usd,
            "blast_radius_level": blast_radius_level,
            "blast_radius_basis": blast_radius_basis,
            "gate_policy_version": gate_policy_version, "gate_reason": gate_reason,
            **decision_properties,
        }
        return 99

    def known_plan_relations(self):
        return []

    def record_plan_predictions(self, work_id, plan_id, assessments):
        if not any(row[0] == work_id for row in self.plan_predictions):
            self.plan_predictions.extend((work_id, plan_id, step, result) for step, result in assessments)
        self.events.append("prediction")

    def prepare_acquisition_step(self, work_id, plan_id, target_step_id, include_analogy=True):
        from runner.acquisition import next_acquisition_step
        done = [a["rung"] for a in self.acquisitions
                if a["work_id"] == work_id and a["target_step_id"] == target_step_id
                and a["status"] in {"completed", "skipped"}]
        step = next_acquisition_step(target_step_id, done, include_analogy=include_analogy)
        if step is None:
            return None
        existing = next((a for a in self.acquisitions if a["work_id"] == work_id
                         and a["target_step_id"] == target_step_id and a["rung"] == step.rung.value), None)
        if existing:
            return existing
        acquisition = {
            "acquisition_id": 700 + len(self.acquisitions), "work_id": work_id,
            "plan_id": plan_id, "target_step_id": target_step_id, "rung": step.rung.value,
            "rung_index": step.rung_index, "action_step_id": step.action_step_id,
            "instruction": step.instruction, "proposer_only": step.proposer_only, "status": "pending",
        }
        self.acquisitions.append(acquisition)
        self.events.append("acquisition_persisted")
        return acquisition

    def mark_acquisition_running(self, acquisition_id):
        next(a for a in self.acquisitions if a["acquisition_id"] == acquisition_id)["status"] = "running"
        self.events.append("acquisition_running")

    def complete_acquisition(self, cur, acquisition_id, result):
        row = next(a for a in self.acquisitions if a["acquisition_id"] == acquisition_id)
        row.update(status="completed", result=result)
        self.events.append("acquisition_completed")

    def reset_acquisition_after_failure(self, acquisition_id):
        next(a for a in self.acquisitions if a["acquisition_id"] == acquisition_id)["status"] = "pending"

    def record_intent_verification(self, work_id, plan_id, concerns, plan, verification):
        self.intent_checks.append((work_id, plan_id, concerns, plan, verification))

    def reject_work_intent(self, work_id, reasons):
        self.events.append("intent_rejected")

    def reject_work_conflict(self, work_id, reasons):
        self.events.append("conflict_rejected")

    def record_plan_evaluation(self, cur, run_id, work, ev, observed_metrics, step_results):
        self.plan_evaluations.append((run_id, ev, observed_metrics, step_results))

    def resolve_plan_predictions(self, cur, run_id, work, ev, step_results):
        from runner.plan_resolution import resolve_plan_steps
        resolution = resolve_plan_steps((work.plan or {}).get("steps") or [], step_results,
                                        goal_satisfied=ev.plan_goal_satisfied,
                                        intent_satisfied=ev.intent_covered)
        self.support_events.extend(resolution.steps)
        if resolution.failed_step_id:
            self.replans.append({"replan_id": len(self.replans)+1,
                                 "failed_step_id": resolution.failed_step_id,
                                 "preserved_prefix_step_ids": list(resolution.preserved_prefix_step_ids),
                                 "reason": resolution.failure_reason, "status": "pending"})
        return resolution

    def start_run(self, work_id):
        self.events.append("start_run")
        self.started.append(work_id)
        if self.approved_row and self.approved_row.get("id") == work_id:
            self.approved_row["status"] = "running"
        return 501

    def record_act(self, run_id, outcome, succeeded, evidence):
        self.recorded.append((run_id, outcome, succeeded, evidence))
        self.pending_after_act = {
            "id": run_id,
            "work_id": self.approved_row["id"] if self.approved_row else 99,
            "summary": self.approved_row["summary"] if self.approved_row else "work",
            "rationale": self.approved_row["rationale"] if self.approved_row else "why",
            "outcome": outcome,
            "succeeded": succeeded,
            "evidence": evidence,
        }

    @contextmanager
    def tx(self):
        yield object()

    def complete_run(self, cur, run_id, **kw):
        self.completed.append((run_id, kw))
        self.pending_after_act = None
        if self.approved_row:
            self.approved_row["status"] = kw.get("work_status", "done")
        if self.autonomous_pending and kw.get("work_status", "done") == "done":
            self.autonomous_pending = None

    def fail_run(self, run_id, error):
        self.failed.append((run_id, error))
        if self.approved_row:
            self.approved_row["status"] = "pending"

    def fail_approved_run(self, run_id, error):
        self.failed.append((run_id, error))
        self.pending_after_act = None
        if self.approved_row:
            self.approved_row["status"] = "awaiting_human"
            self.approved_row["approved_at"] = None

    def interrupt_run(self, run_id, reason):
        self.failed.append((run_id, reason))
        if self.approved_row:
            self.approved_row["status"] = "pending"

    def interrupt_approved_after_act(self, run_id, reason):
        self.failed.append((run_id, reason))
        if self.approved_row:
            self.approved_row["status"] = "awaiting_human"
            self.approved_row["approved_at"] = None
            self.approved_row["rationale"] += "\n\nSide effects UNKNOWN — verify before re-approving."

    def write_learning(self, cur, run_id, insight, evidence, scope, confidence, evidence_kind="actor_claim"):
        self.learned.append((run_id, insight, evidence, scope, confidence))
        return 701

    def graph_link(self, cur, run_id, work_id, learning_id):
        pass

    def beat(self, state, detail="", retry_after_s=None):
        pass


class FakeExecutor:
    def __init__(self, *, fail_act=False, fail_reflect=False):
        self.schemas = []
        self.fail_act = fail_act
        self.fail_reflect = fail_reflect

    def run(self, prompt, schema, tier="working"):
        self.schemas.append(schema)
        if schema is prompts.ACT_SCHEMA:
            if self.fail_act:
                raise RuntimeError("approved act failed")
            acquisition = "do not execute" in prompt.lower()
            return {
                "outcome": "approved work executed", "succeeded": True, "evidence": "artifact",
                "observed_metrics": {"mission_delta": 1, "mission_value": 2},
                "step_results": [{"step_id": "legacy-approved-step", "executed": not acquisition,
                                  "confirmed": not acquisition, "harmed_concern_ids": [], "evidence": "artifact"}],
            }, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            if self.fail_reflect:
                raise RuntimeError("reflect failed after act")
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
        self.assertLess(db.events.index("prediction"), db.events.index("start_run"))
        self.assertEqual(len(db.plan_predictions), 1)
        self.assertEqual(db.plan_predictions[0][2]["expected_direction"], "toward")
        self.assertEqual([s for s in ex.schemas], [prompts.ACT_SCHEMA, prompts.REFLECT_SCHEMA])
        self.assertFalse(db.created)

    def test_unapproved_pending_work_is_not_treated_as_human_approval(self):
        db = FakeDb(approved_row=None)
        ex = FakeExecutor()

        cycle = Loop(instance(), db, ex).cycle()

        self.assertIsNone(cycle)
        self.assertEqual(db.started, [])
        self.assertEqual(ex.schemas, [prompts.DECIDE_SCHEMA])

    def test_failed_approved_work_is_reparked_not_reexecuted_next_cycle(self):
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
        ex = FakeExecutor(fail_act=True)
        loop = Loop(instance(), db, ex)

        with self.assertRaisesRegex(RuntimeError, "approved act failed"):
            loop.cycle()
        self.assertIsNone(loop.cycle())

        self.assertEqual(ex.schemas.count(prompts.ACT_SCHEMA), 1)
        self.assertEqual(db.started, [42])
        self.assertEqual(approved["status"], "awaiting_human")
        self.assertIsNone(approved["approved_at"])

    def test_approved_reflect_failure_warns_and_finishes_without_reexecuting_act(self):
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
        ex = FakeExecutor(fail_reflect=True)
        loop = Loop(instance(), db, ex)

        with self.assertRaisesRegex(RuntimeError, "reflect failed after act"):
            loop.cycle()

        self.assertEqual(ex.schemas.count(prompts.ACT_SCHEMA), 1)
        self.assertEqual(approved["status"], "awaiting_human")
        self.assertIsNone(approved["approved_at"])
        self.assertIn("Side effects UNKNOWN", approved["rationale"])

        ex.fail_reflect = False
        cycle = loop.cycle()

        self.assertIsNone(cycle)
        self.assertEqual(ex.schemas.count(prompts.ACT_SCHEMA), 1)
        self.assertTrue(db.completed)
        self.assertEqual(approved["status"], "done")


if __name__ == "__main__":
    unittest.main()
