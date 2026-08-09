"""Adversarial end-to-end wiring tests for the consult-act ONE-DIRECTIONAL invariant (Slice 3).

Originally authored by the independent non-Claude reviewer (Hermes/Kimi-K3) during the #4407
publish gate and adopted here so the safety-critical property is locked into the suite. Drives
Loop.cycle end-to-end (the repo's own FakeDb/executor harness) at autonomy=act-external — the seam
where the BASE gate ALLOWS a non-reversible, touches_human step and consult-act is the ONLY thing
that can park it. Every case tries to make consult-act either execute a step the base gate would
have parked, or skip a base-gate park; none can.
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
        self.decision = {
            "kind": "delivery",
            "summary": "email the partner list",
            "rationale": "moves the measure",
            "reversible": False,
            "spends_money": False,
            "touches_human": True,
            "commits": False,
            "do_nothing": False,
            "plan": {"goal_predicate": "partner receives email", "steps": [{
                "step_id": "email", "action": "delivery", "expected_effect": "measure_up",
                "expected_direction": "toward", "scope": {}
            }]},
        }
        self.decision.update(overrides)
        self.schemas = []

    def run(self, prompt, schema, tier="working"):
        self.schemas.append(schema)
        if schema is prompts.DECIDE_SCHEMA:
            return self.decision, Usage()
        if schema is prompts.ACT_SCHEMA:
            return {"outcome": "acted", "succeeded": True, "evidence": "e"}, Usage()
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


def test_A_low_certainty_defers_where_base_gate_allows():
    cycle, db, acted, _, _ = run_cycle(0.10)
    assert cycle is None
    assert db.parked == [99]
    assert acted is False, "consult-act deferral must NOT execute the work"


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


def test_E_high_certainty_NEVER_unparks_a_base_gate_park():
    # THE critical anti-permissiveness case: base gate REQUIRES human (spends_money) and the
    # principle is MAXIMALLY certain (1.0). If consult-act could authorize/skip, this would act.
    cycle, db, acted, _, _ = run_cycle(1.0, spends_money=True)
    assert cycle is None
    assert db.parked == [99], "base-gate park must stand even at certainty 1.0"
    assert acted is False, "certainty 1.0 must NEVER un-park/authorize a base-gate park"


def test_F_base_park_survives_consult_failure():
    cycle, db, acted, _, _ = run_cycle(None, exc=RuntimeError("boom"), spends_money=True)
    assert cycle is None
    assert db.parked == [99]
    assert acted is False


def test_G_no_mgmt_base_url_no_consult_base_gate_decides():
    cycle, db, acted, m, _ = run_cycle(None, base_url=None)
    assert acted is True
    assert db.parked == []
    assert not m.called, "no base url -> no assess call at all"


def test_H_deferral_reason_reaches_the_human_row_and_notify():
    # The MED review fix: a consult-act deferral's REASON must reach the reviewer, not only the log.
    cycle, db, acted, _, notify_calls = run_cycle(0.05)
    assert db.parked == [99] and cycle is None and acted is False
    note = db.park_notes[0]
    assert note and "consult-act" in note, "the deferral reason must be persisted on the parked row"
    assert notify_calls and notify_calls[0] and "consult-act" in notify_calls[0], \
        "the deferral reason must be passed to notify_human"


def test_I_base_gate_park_carries_no_consult_note():
    # A base-gate park (not consult-act) must NOT be labelled as a consult-act deferral.
    cycle, db, acted, _, notify_calls = run_cycle(None, spends_money=True)
    assert db.parked == [99]
    assert db.park_notes[0] is None, "a base-gate park must not carry a consult-act note"
    assert notify_calls[0] is None
