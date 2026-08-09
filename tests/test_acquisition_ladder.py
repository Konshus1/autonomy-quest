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
