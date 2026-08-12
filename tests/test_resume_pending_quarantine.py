"""Resume-path resilience: a stale/invalid PENDING item is PARKED, not crash-looped.

INCIDENT (#4834): the loop was down ~27h, resumed a backlog of stale pending items one at a time,
and each bad item hit a DIFFERENT deterministic crash in the resume-pending path. One such item's
frame-gap routing produced a self-contradictory meta_mode estimate that the invariant at
meta_mode.py:126 correctly rejects (``ValueError: ... known_worthless requires a non-positive upper
net bound``). That ValueError propagated out of ``cycle()``: the outer loop's ``except Exception``
recorded "cycle blew up, continuing" but wrote NO heartbeat, and the identical pending item was
re-selected next cycle -> never a clean cycle -> loop wedged.

The invariant is CORRECT and must not be weakened. The bug is BLAST RADIUS: a per-item validation
failure crashed the WHOLE cycle instead of quarantining just that item. The fix parks the bad item
(reusing the existing ``park_for_human`` surface) and returns cleanly so the loop drains the backlog.

These are red-first: tests ``bad_meta_estimate`` and ``malformed_plan`` FAIL against the pre-fix
code (cycle() raises), and the infra-failure test is the guard proving the catch is not over-broad.
"""
import pytest

from runner import prompts
from runner.executor import Usage
from runner.loop import Loop
from tests.test_loop_approval_execution import instance
from tests.test_meta_mode_loop import MetaDb, MetaExecutor
from tests.test_pre_act_predictions import PlannedExecutor


def _frame_gap_plan():
    """A durable plan whose steps have no known relations -> every step is a frame gap."""
    decision, _ = PlannedExecutor(MetaDb()).run("", prompts.DECIDE_SCHEMA)
    return decision["plan"]


def _pending_row(plan, **overrides):
    """A durable PENDING work row (no acquisition chosen yet) as pending_autonomous_work returns."""
    row = {
        "id": 4834, "kind": "research", "summary": "collect and verify",
        "rationale": "resume the durable plan interrupted by the outage",
        "requires_human": False, "reversible": True, "spends_money": False,
        "expected_expense_usd": 0, "expected_cost_usd": 0,
        "blast_radius_level": 0, "blast_radius_basis": None,
        "touches_human": False, "commits": False,
        "plan_id": "plan-4834", "plan": plan,
        "acquisition_id": None, "acquisition_rung": None, "meta_mode_decision_id": None,
        "gate_policy_version": None, "gate_reason": None, "execution_path": "mission",
    }
    row.update(overrides)
    return row


class ResumeDb(MetaDb):
    """MetaDb that starts with a stale PENDING item and captures park_for_human."""

    def __init__(self, pending_row):
        super().__init__()
        self.autonomous_pending = pending_row
        self.parked = []

    def park_for_human(self, work_id, note=None):
        self.parked.append((work_id, note))
        # Real Db moves the row to 'awaiting_human', so pending_autonomous_work stops returning it.
        if self.autonomous_pending and self.autonomous_pending.get("id") == work_id:
            self.autonomous_pending = None


def _capture_notifications(loop):
    sent = []
    original = loop.inst.surfaces.notify
    loop.inst.surfaces.notify = lambda subject, body: sent.append((subject, body)) or original(
        subject=subject, body=body)
    return sent


# --------------------------------------------------------------------------------------------
# The self-contradictory meta_mode estimate — the exact live wedge (meta_mode.py:126).
# --------------------------------------------------------------------------------------------
class ContradictoryEstimateExecutor(MetaExecutor):
    """Frame-gap routing yields a KNOWN_WORTHLESS estimate with a POSITIVE upper net bound.

    net_interval().high = direct.high + info.high - cost.low = 4 + 3 - 1 = 6 > 0, which the
    invariant at meta_mode.py:126 correctly rejects for a known_worthless estimate. This is the
    self-contradictory model output that wedged the live loop.
    """

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.META_MODE_SCHEMA:
            self.meta_calls += 1
            options = [{
                "mode": "autonomous_practice", "state": "known_worthless",
                "direct_value": {"low": 4, "high": 4},
                "information_value": {"low": 3, "high": 3},
                "cost": {"low": 1, "high": 1},
                "evidence_refs": ["mission:autonomous_practice"],
                "rationale": "model asserted worthless yet scored a positive net — self-contradictory",
                "instruction": "practice in sandbox", "expected_expense_usd": 0,
                "blast_radius_level": 0, "reversible": True, "spends_money": False,
                "touches_human": False, "commits": False,
                "block_reason": None, "wake_condition": None,
            }, self.option("abstain", (0, 0), (0, 0), (0, 0), "")]
            present = {o["mode"] for o in options}
            from runner.meta_mode import MetaMode
            for mode in MetaMode:
                if mode.value not in present:
                    options.append({"mode": mode.value, "state": "blocked", "direct_value": None,
                                    "information_value": None, "cost": None,
                                    "evidence_refs": [f"unavailable:{mode.value}"],
                                    "rationale": "unavailable", "instruction": "",
                                    "block_reason": "unavailable", "wake_condition": None})
            return {"options": options}, Usage(tokens_in=17, tokens_out=4)
        return super().run(prompt, schema, tier)


def test_contradictory_meta_estimate_parks_item_and_completes_cycle_cleanly():
    """RED-FIRST: before the fix, cycle() RAISES the ValueError and wedges the loop."""
    db = ResumeDb(_pending_row(_frame_gap_plan()))
    loop = Loop(instance(), db, ContradictoryEstimateExecutor(db))
    sent = _capture_notifications(loop)

    # The exception must NOT propagate — the cycle returns cleanly so a heartbeat can be written.
    result = loop.cycle()

    assert result is None
    # The offending item is PARKED awaiting_human, not retried forever.
    assert db.parked, "the invalid pending item must be parked for human review"
    assert db.parked[0][0] == 4834
    note = db.parked[0][1]
    assert "non-positive upper net bound" in note  # the exact invariant that rejected it
    assert "parked for human" in note.lower()
    # A human was told (the park path never goes silent).
    assert sent, "parking a decision must notify the human"
    # The invariant itself still fired — it did its job; only the blast radius changed.
    assert db.rejected_attempts, "the meta forecast attempt was still rejected/recorded"

    # And the loop DRAINS PAST it: the item is no longer 'pending', so the next resume selects
    # DIFFERENT work instead of re-crashing on the identical row (the crash-loop is broken).
    assert db.pending_autonomous_work() is None


def test_heartbeat_would_be_written_after_a_quarantine_cycle():
    """The outer loop only records liveness when cycle() does NOT raise. Prove it returns."""
    db = ResumeDb(_pending_row(_frame_gap_plan()))
    loop = Loop(instance(), db, ContradictoryEstimateExecutor(db))

    beats = []
    db.beat_cycle = lambda: beats.append(True)

    loop.cycle()               # must not raise
    loop._record_cycle_heartbeat()  # the outer loop calls this only on the no-raise path
    assert beats == [True]


# --------------------------------------------------------------------------------------------
# The CLASS, not one instance: a DIFFERENT item-validation error in the resume path.
# --------------------------------------------------------------------------------------------
def test_malformed_pending_plan_is_also_parked_not_crash_looped():
    """RED-FIRST: a plan with duplicate step_ids raises ValueError in _ensure_pre_act_predictions.

    A different validation SURFACE than meta_mode, proving the fix quarantines the whole class of
    per-item validation failures rather than one instance.
    """
    plan = _frame_gap_plan()
    plan["steps"][1]["step_id"] = plan["steps"][0]["step_id"]  # duplicate -> uniqueness ValueError
    db = ResumeDb(_pending_row(plan))
    loop = Loop(instance(), db, MetaExecutor(db))
    sent = _capture_notifications(loop)

    result = loop.cycle()

    assert result is None
    assert db.parked and db.parked[0][0] == 4834
    assert "unique" in db.parked[0][1]
    assert sent


# --------------------------------------------------------------------------------------------
# THE KEY GUARD: a NON-item (infra / unexpected) error STILL PROPAGATES — never masked.
# --------------------------------------------------------------------------------------------
class DeadConnectionDb(ResumeDb):
    """Simulates a dead DB connection surfacing inside the pre-ACT validation path."""

    def known_plan_relations(self):
        raise ConnectionError("database connection reset")


def test_infra_failure_in_resume_path_still_propagates():
    """The quarantine catch is ValueError-scoped: a connection death is NOT masked as a bad item."""
    db = DeadConnectionDb(_pending_row(_frame_gap_plan()))
    loop = Loop(instance(), db, MetaExecutor(db))

    with pytest.raises(ConnectionError, match="connection reset"):
        loop.cycle()
    # A real infra failure must surface, NOT get quietly parked as "bad item".
    assert not db.parked


class ExplodingMetaExecutor(MetaExecutor):
    """An unexpected, non-ValueError error type raised inside frame-gap routing."""

    def run(self, prompt, schema, tier="working"):
        if schema is prompts.META_MODE_SCHEMA:
            raise RuntimeError("executor backend crashed")
        return super().run(prompt, schema, tier)


def test_unexpected_error_type_in_routing_still_propagates():
    db = ResumeDb(_pending_row(_frame_gap_plan()))
    loop = Loop(instance(), db, ExplodingMetaExecutor(db))

    with pytest.raises(RuntimeError, match="backend crashed"):
        loop.cycle()
    assert not db.parked


# --------------------------------------------------------------------------------------------
# Regression guard: a HEALTHY pending resume is unchanged (reaches ACT, never parked).
# --------------------------------------------------------------------------------------------
def test_healthy_pending_resume_is_unchanged():
    db = ResumeDb(_pending_row(_frame_gap_plan()))
    loop = Loop(instance(), db, MetaExecutor(db))

    result = loop.cycle()

    assert result is not None                # executed, not parked
    assert not db.parked
    assert db.acquisitions and db.acquisitions[0]["rung"] == "environment_experiment"
    assert db.acquisitions[0]["status"] == "completed"
