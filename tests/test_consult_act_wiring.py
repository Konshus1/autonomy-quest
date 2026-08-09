"""Consult uncertainty is advisory; amended BB #878 authorization is consequence-only.

Low certainty may inform acquisition/re-plan, but must not create a categorical human gate.
Numeric plan expense and measured per-action blast remain the authorization floor.
"""
from unittest.mock import patch

from runner.config import (Autonomy, Boundaries, BudgetCfg, Curiosity, Engine, Instance,
                           Measure, Mission, Models, Money, Surfaces)
from runner.executor import Usage
from runner.loop import Loop
from runner import prompts
from tests.test_loop_approval_execution import FakeDb


def inst_external() -> Instance:
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
        budget=BudgetCfg(money=Money(daily_soft_usd=0, monthly_hard_usd=0),
                         autonomy=Autonomy(level="act-external")),
        surfaces=Surfaces(notify_channel="none"),
        curiosity=Curiosity(enabled=False),
    )


class ParkDb(FakeDb):
    def __init__(self):
        super().__init__()
        self.parked = []
        self.park_notes = []

    def park_for_human(self, work_id, note=None):
        self.parked.append(work_id)
        self.park_notes.append(note)

    def known_plan_relations(self):
        return [{
            "edge_id": 1, "source_action": "delivery", "direct_effect": "measure_up",
            "relation_direction": "toward", "mechanism_description": "known delivery mechanism",
            "scope_conditions": None, "predicted_certainty": 0.8,
        }]


class DecideExecutor:
    """Returns a REAL decision: non-reversible + touches_human (side-effecting for consult-act)
    but spends_money=False, commits=False — so the act-external BASE gate ALLOWS it."""

    def __init__(self, **overrides):
        plan_cost = overrides.pop("plan_expected_expense_usd", 0)
        blast_count = overrides.pop("blast_count", 0)
        irreversible = overrides.pop("irreversible_external_write", False)
        self.decision = {
            "kind": "delivery",
            "summary": "email the partner list",
            "rationale": "moves the measure",
            "reversible": False,
            "spends_money": False,
            "touches_human": True,
            "commits": False,
            "do_nothing": False,
            "plan": {
                "goal_predicate": {"metric": "mission_delta", "operator": ">=", "value": 0},
                "expected_expense_usd": plan_cost,
                    "mission_concerns": [
                    {"concern_id": "serve_mission_progress", "kind": "serve",
                     "predicate": {"metric": "mission_delta", "operator": ">=", "value": 0}},
                    {"concern_id": "must_not_overshoot", "kind": "must_not_harm",
                     "predicate": {"metric": "mission_value", "operator": "<=", "value": 10.0}},
                ],
                "subgoals": [{"subgoal_id": "delivery",
                    "success_predicate": {"metric": "mission_value", "operator": "<=", "value": 10.0},
                    "serves_concern_ids": ["serve_mission_progress", "must_not_overshoot"]}],
                "steps": [{"step_id": "email", "subgoal_id": "delivery", "action": "delivery",
                    "expected_effect": "measure_up", "expected_direction": "toward", "scope": {},
                     "blast_radius": {"affected_entities_upper_bound": blast_count, "public_or_unbounded": False,
                                      "production_wide": False, "irreversible_external_write": irreversible}}],
            },
        }
        self.decision.update(overrides)
        self.schemas = []

    def run(self, prompt, schema, tier="working"):
        self.schemas.append(schema)
        if schema is prompts.DECIDE_SCHEMA:
            return self.decision, Usage()
        if schema is prompts.ACT_SCHEMA:
            return {"outcome": "acted", "succeeded": True, "evidence": "e",
                    "observed_metrics": {"mission_delta": 1, "mission_value": 2},
                    "step_results": [{"step_id": "email", "executed": True,
                                      "confirmed": True, "harmed_concern_ids": [], "evidence": "e"}]}, Usage()
        if schema is prompts.REFLECT_SCHEMA:
            return {"insight": "i", "evidence": "e", "scope": "local", "confidence": 0.8}, Usage()
        raise AssertionError(f"unexpected schema {schema}")


def run_cycle(certainty, exc=None, base_url="http://x", **decision_overrides):
    db = ParkDb()
    ex = DecideExecutor(**decision_overrides)
    notify_calls = []
    real_notify = Loop.notify_human

    def spy_notify(self, work, reason=None):
        notify_calls.append(reason)
        return real_notify(self, work, reason)

    with patch("runner.causal_sync.mgmt_base_url", return_value=base_url), \
         patch("runner.causal_sync.assess_plan_certainty",
               side_effect=exc if exc else None, return_value=certainty) as m_assess, \
         patch.object(Loop, "notify_human", spy_notify):
        cycle = Loop(inst_external(), db, ex).cycle()
    acted = prompts.ACT_SCHEMA in ex.schemas
    return cycle, db, acted, m_assess, notify_calls


def test_A_low_certainty_does_not_become_authorization_gate():
    cycle, db, acted, _, _ = run_cycle(0.10)
    assert cycle is not None and acted is True
    assert db.parked == []


def test_B_high_certainty_does_not_defer_base_gate_decides():
    cycle, db, acted, _, _ = run_cycle(0.90)
    assert acted is True
    assert db.parked == []


def test_C_no_governing_principle_base_gate_unaffected():
    cycle, db, acted, _, _ = run_cycle(None)
    assert acted is True
    assert db.parked == []


def test_D_consult_raises_degrades_to_no_opinion_base_gate_runs():
    cycle, db, acted, _, _ = run_cycle(None, exc=RuntimeError("mgmt api down"))
    assert acted is True, "a consult failure must degrade to base-gate behavior, not block"
    assert db.parked == []


def test_E_boolean_spends_money_is_audit_only_not_gate():
    cycle, db, acted, _, _ = run_cycle(1.0, spends_money=True)
    assert cycle is not None and acted is True and db.parked == []


def test_F_consult_failure_does_not_turn_category_into_gate():
    cycle, db, acted, _, _ = run_cycle(None, exc=RuntimeError("boom"), spends_money=True)
    assert cycle is not None and acted is True and db.parked == []


def test_G_no_mgmt_base_url_no_consult_base_gate_decides():
    cycle, db, acted, m, _ = run_cycle(None, base_url=None)
    assert acted is True
    assert db.parked == []
    assert not m.called, "no base url -> no assess call at all"


def test_H_low_certainty_emits_no_human_authorization_reason():
    cycle, db, acted, _, notify_calls = run_cycle(0.05)
    assert cycle is not None and acted is True
    assert db.parked == [] and notify_calls == []


def test_I_numeric_expense_gate_persists_exact_reason():
    cycle, db, acted, _, notify_calls = run_cycle(None, plan_expected_expense_usd=3.01)
    assert cycle is None and acted is False and db.parked == [99]
    assert "exceeds $3.0" in db.park_notes[0]
    assert notify_calls[0] == db.park_notes[0]


def test_J_mass_send_high_blast_parks_at_zero_cost():
    cycle, db, acted, _, _ = run_cycle(None, blast_count=100, touches_human=True)
    assert cycle is None and acted is False and db.parked == [99]
    assert "blast radius level 3" in db.park_notes[0]
