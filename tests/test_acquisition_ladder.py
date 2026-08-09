from runner.acquisition import AcquisitionRung, next_acquisition_step
from runner.loop import Loop
from tests.test_loop_approval_execution import FakeDb, instance
from tests.test_pre_act_predictions import PlannedExecutor


def test_ladder_advances_in_required_order_and_analogy_only_proposes():
    done = []
    observed = []
    for _ in range(5):
        step = next_acquisition_step("gap", done)
        observed.append(step.rung)
        done.append(step.rung)
        if step.rung == AcquisitionRung.ANALOGY_PROPOSAL:
            assert step.proposer_only is True
            assert "Do not act on the analogy candidate" in step.instruction
        else:
            assert step.proposer_only is False
    assert observed == [
        AcquisitionRung.RECALL, AcquisitionRung.SEARCH,
        AcquisitionRung.BOUNDED_EXPERIMENT, AcquisitionRung.ANALOGY_PROPOSAL,
        AcquisitionRung.HUMAN,
    ]


def test_optional_analogy_can_be_skipped_without_changing_other_order():
    done = [AcquisitionRung.RECALL, AcquisitionRung.SEARCH, AcquisitionRung.BOUNDED_EXPERIMENT]
    step = next_acquisition_step("gap", done, include_analogy=False)
    assert step.rung == AcquisitionRung.HUMAN


def test_absent_target_is_replaced_by_persisted_recall_action():
    db = FakeDb()  # no known relations => both target steps localize as absent
    ex = PlannedExecutor(db)
    cycle = Loop(instance(), db, ex).cycle()
    assert cycle is not None
    assert db.events.index("acquisition_persisted") < db.events.index("act")
    assert ex.acted_prompt is not None
    assert "knowledge_acquisition:recall" not in ex.acted_prompt  # prompt shows instruction, not internal kind
    assert "Recall traceable prior evidence" in ex.acted_prompt
    assert "do not execute that target step" in ex.acted_prompt
    assert "WORK: collect and verify" not in ex.acted_prompt


def test_all_completed_rungs_exhaust_instead_of_repeating_human():
    assert next_acquisition_step("gap", list(AcquisitionRung)) is None


def test_two_no_evidence_cycles_reuse_work_and_advance():
    class CountingExecutor(PlannedExecutor):
        def __init__(self, db):
            super().__init__(db)
            self.decisions = 0
            self.act_prompts = []
        def run(self, prompt, schema, tier="working"):
            from runner import prompts
            if schema is prompts.DECIDE_SCHEMA:
                self.decisions += 1
            if schema is prompts.ACT_SCHEMA:
                self.act_prompts.append(prompt)
            return super().run(prompt, schema, tier)

    db = FakeDb()
    ex = CountingExecutor(db)
    loop = Loop(instance(), db, ex)
    first = loop.cycle()
    second = loop.cycle()

    assert first is not None and second is not None
    assert ex.decisions == 1
    assert len(db.created) == 1
    assert db.started == [99, 99]
    assert [a["rung"] for a in db.acquisitions] == ["recall", "search"]
    assert [a["status"] for a in db.acquisitions] == ["completed", "completed"]
    assert "Recall traceable prior evidence" in ex.act_prompts[0]
    assert "Search authoritative sources" in ex.act_prompts[1]
    assert db.autonomous_pending["id"] == 99
    assert db.plan_evaluations == []
    assert db.support_events == []
