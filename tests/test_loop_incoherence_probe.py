"""G1 / BB #2301 slice 3 — Loop.maybe_probe_incoherence wiring test.

Drives execute_work directly (not cycle()) so the escalate verdict is controlled precisely.
Verifies: probe fires ONLY on escalate; STAGES via stage_curiosity_learning (never changes
behavior); counts as kind='curiosity'/escalation_level='curiosity'; best-effort on fetch
failure / empty frontier / API unreachable; no probe when curiosity disabled or budget out;
verdict unchanged in all cases.
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from decimal import Decimal

from runner import causal_sync, prompts
from runner.config import (
    Autonomy, Boundaries, BudgetCfg, Curiosity, CuriosityBudget, CuriosityFrontier,
    CuriosityRatchet, Engine, Instance, Measure, Mission, Models, Money, Surfaces,
)
from runner.db import Work
from runner.executor import Usage
from runner.loop import Loop


# ---- fakes ---------------------------------------------------------------

class FakeDb:
    def __init__(self):
        self.created = []
        self.started = []
        self.completed = []
        self.failed = []
        self.staged = []
        self.acted = []
        self.learned = []
        self._curiosity_cycles = 0
        self._curiosity_cost = Decimal("0")
        self._promoted = 1
        self._cycles_recent = 10
        self._measure_after = None  # None = same as before (no movement)

    def sum_cost(self, since): return Decimal("0")
    def curiosity_cost_today(self): return self._curiosity_cost
    def curiosity_cycles_today(self): return self._curiosity_cycles
    def curiosity_promoted_recent(self, w): return self._promoted
    def curiosity_cycles_recent(self, w): return self._cycles_recent

    def read_measure(self, measure):
        return self._measure_after if self._measure_after is not None else Decimal("10")
    def live_learnings(self, limit=50): return []
    def record_act(self, run_id, outcome, succeeded, evidence):
        self.acted.append((run_id, outcome, succeeded, evidence))
    def create_work(self, kind, summary, rationale, requires_human=False):
        self.created.append((kind, summary, rationale))
        return 900 + len(self.created)
    def start_run(self, work_id):
        self.started.append(work_id)
        return 5000 + len(self.started)
    def interrupt_run(self, run_id, reason): self.failed.append((run_id, reason))
    def fail_run(self, run_id, error): self.failed.append((run_id, error))
    @contextmanager
    def tx(self):
        yield object()
    def complete_run(self, cur, run_id, **kw):
        self.completed.append((run_id, kw))
    def write_learning(self, cur, run_id, insight, evidence, scope, confidence):
        self.learned.append((run_id, insight))
        return 7000 + len(self.learned)
    def graph_link(self, cur, run_id, work_id, learning_id): pass
    def beat(self, state, detail="", retry_after_s=None): pass
    def stage_curiosity_learning(self, **kw):
        self.staged.append(kw)


class FakeExecutor:
    def __init__(self, *, fail_explore=False):
        self.schemas = []
        self.fail_explore = fail_explore

    def run(self, prompt, schema, tier="working"):
        self.schemas.append(schema)
        if schema is prompts.ACT_SCHEMA:
            return {"outcome": "did the thing", "succeeded": True, "evidence": "artifact"}, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {"insight": "learned x", "evidence": "run 5001", "scope": "local", "confidence": 0.6}, Usage()
        if schema is prompts.EXPLORE_SCHEMA:
            if self.fail_explore:
                raise RuntimeError("explore blew up")
            return {
                "outcome": "inspected surprised edge neighborhood",
                "succeeded": True,
                "evidence": "causal_edges API",
                "insight": "the model over-predicted because X",
                "applies_when": "when a high-certainty edge fails",
                "falsified_by": "re-run with a different scope",
                "confidence": 0.7,
            }, Usage()
        raise AssertionError(f"unexpected schema: {schema}")


# ---- helpers -------------------------------------------------------------

_COHERENCE_FRONTIER = [
    {"cause": "deploy", "effect": "measure_up", "scope": {}, "formality": "evidential",
     "predicted_certainty": 0.9, "max_surprise": 0.9, "incoherence_records": 1,
     "seed": True, "neighbor": False},
    {"cause": "deploy", "effect": "cache_warm", "scope": {}, "formality": "fuzzy",
     "predicted_certainty": 0.5, "max_surprise": 0.0, "incoherence_records": 0,
     "seed": False, "neighbor": True},
]


def _instance(*, curiosity_enabled=True, cycles=2) -> Instance:
    return Instance(
        mission=Mission(
            objective="test", measure=Measure(what="count", where="select 1", target=10),
            horizon="ongoing", boundaries=Boundaries(),
        ),
        engine=Engine(mode="subscription"),
        template="running-a-business",
        datastore={"graph": "none"},
        models=Models(),
        budget=BudgetCfg(money=Money(daily_soft_usd=0, monthly_hard_usd=0), autonomy=Autonomy()),
        surfaces=Surfaces(notify_channel="none"),
        curiosity=Curiosity(
            enabled=curiosity_enabled,
            budget=CuriosityBudget(cycles_per_day=cycles, max_cost_usd_per_day=1.0),
            frontier=CuriosityFrontier(
                source="select 1 from external_catalog", authority="catalog",
                target=10, max_items_per_cycle=3),
            ratchet=CuriosityRatchet(window_cycles=10, shrink_factor=0.5, floor_cycles_per_day=0),
        ) if curiosity_enabled else Curiosity(enabled=False),
    )


def _work() -> Work:
    return Work(id=1, kind="deploy", summary="deploy v2", rationale="ship it")


class LoopIncoherenceProbeTests(unittest.TestCase):
    """The escalate verdict triggers a curiosity probe; pass/rework do not; probe is staged-inert
    and best-effort; verdict is unchanged in every case."""

    def setUp(self):
        import runner.causal_sync as cs
        self._orig_mgmt = cs.mgmt_base_url
        self._orig_fetch = cs.fetch_high_surprise_edges
        self._orig_assess = cs.assess_plan_certainty

    def tearDown(self):
        import runner.causal_sync as cs
        cs.mgmt_base_url = self._orig_mgmt
        cs.fetch_high_surprise_edges = self._orig_fetch
        cs.assess_plan_certainty = self._orig_assess

    def _patch(self, *, frontier=_COHERENCE_FRONTIER, pc=0.9, mgmt="http://127.0.0.1:1"):
        import runner.causal_sync as cs
        cs.mgmt_base_url = lambda env=None: mgmt
        cs.assess_plan_certainty = lambda *a, **k: pc
        cs.fetch_high_surprise_edges = lambda *a, **k: frontier

    def _run_execute(self, db, ex, *, before=Decimal("10"), after=None):
        """Drive execute_work directly; return the Cycle (or None).

        `after=None` means measure_after == measure_before (no movement -> escalate trigger
        when pc>=0.7). Pass a different Decimal to simulate movement (verdict=pass).
        """
        db._measure_after = after
        loop = Loop(_instance(), db, ex)
        return loop.execute_work(
            _work(), measure_before=before, decision_usage=Usage(), escalation_level="autonomous")

    # ---- probe fires on escalate ----

    def test_escalate_verdict_triggers_one_staged_probe(self):
        self._patch()
        db = FakeDb()
        ex = FakeExecutor()
        cycle = self._run_execute(db, ex)
        # main run completed + one curiosity probe completed
        self.assertEqual(len(db.completed), 2)
        probe_kw = db.completed[1][1]
        self.assertEqual(probe_kw["escalation_level"], "curiosity")
        self.assertIsNone(probe_kw["measure_before"])  # probe didn't touch mission measure
        # one staged curiosity learning
        self.assertEqual(len(db.staged), 1)
        self.assertEqual(db.staged[0]["run_id"], db.completed[1][0])
        # the curiosity work was created with kind=curiosity
        self.assertEqual(db.created[0][0], "curiosity")
        # the explore schema was exercised
        self.assertIn(prompts.EXPLORE_SCHEMA, ex.schemas)
        # the main cycle still returned its Cycle (verdict unchanged)
        self.assertIsNotNone(cycle)

    def test_probe_seed_is_the_just_surprised_edge(self):
        self._patch()
        db = FakeDb()
        self._run_execute(db, FakeExecutor())
        summary = db.created[0][1]
        self.assertIn("deploy->measure_up", summary)

    # ---- no probe on pass / rework ----

    def test_pass_verdict_no_probe(self):
        self._patch(pc=0.3)  # low certainty -> coherent -> verdict=pass (measure moved)
        db = FakeDb()
        self._run_execute(db, FakeExecutor(), after=Decimal("12"))
        self.assertEqual(len(db.staged), 0)
        self.assertEqual(len(db.completed), 1)  # main run only

    def test_rework_verdict_no_probe(self):
        # no evidence -> not productive -> rework; even with incoherence, no probe
        self._patch(pc=0.9)
        db = FakeDb()
        ex = FakeExecutor()
        # patch the act result to have no evidence -> was_productive=False -> rework
        original_run = ex.run
        def patched_run(prompt, schema, tier="working"):
            r, u = original_run(prompt, schema, tier)
            if schema is prompts.ACT_SCHEMA:
                r["evidence"] = ""
            return r, u
        ex.run = patched_run
        self._run_execute(db, ex)
        self.assertEqual(len(db.staged), 0)

    # ---- no probe when curiosity disabled / budget out ----

    def test_disabled_curiosity_no_probe(self):
        self._patch()
        db = FakeDb()
        loop = Loop(_instance(curiosity_enabled=False), db, FakeExecutor())
        loop.execute_work(_work(), measure_before=Decimal("10"),
                          decision_usage=Usage(), escalation_level="autonomous")
        self.assertEqual(len(db.staged), 0)

    def test_budget_exhausted_no_probe(self):
        self._patch()
        db = FakeDb()
        db._curiosity_cycles = 2  # cycles_per_day=2 -> exhausted
        self._run_execute(db, FakeExecutor())
        self.assertEqual(len(db.staged), 0)
        self.assertEqual(len(db.completed), 1)  # main run still completes

    # ---- best-effort: fetch failure / empty / no API ----

    def test_fetch_returns_none_no_probe(self):
        self._patch(frontier=None)
        db = FakeDb()
        self._run_execute(db, FakeExecutor())
        self.assertEqual(len(db.staged), 0)

    def test_fetch_returns_empty_no_probe(self):
        self._patch(frontier=[])
        db = FakeDb()
        self._run_execute(db, FakeExecutor())
        self.assertEqual(len(db.staged), 0)

    def test_no_management_api_no_probe(self):
        self._patch(mgmt=None)
        db = FakeDb()
        self._run_execute(db, FakeExecutor())
        self.assertEqual(len(db.staged), 0)

    def test_explore_failure_does_not_fail_cycle(self):
        self._patch()
        db = FakeDb()
        ex = FakeExecutor(fail_explore=True)
        cycle = self._run_execute(db, ex)
        # main run completed; probe failed but cycle returned
        self.assertEqual(len(db.completed), 1)
        self.assertEqual(len(db.failed), 1)  # probe run failed
        self.assertEqual(len(db.staged), 0)
        self.assertIsNotNone(cycle)


if __name__ == "__main__":
    unittest.main()
